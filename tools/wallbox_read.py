#!/usr/bin/env python3
"""
Wallbox Pulsar BLE reader -- Linux / BlueZ reference client (read-only).

Connects to a Wallbox over BLE, performs Just Works pairing, and reads the three
known methods (r_dca = power meter, r_dat = charger state, r_sta = status), printing
the decoded values. Useful for exploring the protocol on a Linux box / Raspberry Pi.

Requirements:
    pip install dbus-fast
    BlueZ (bluetoothd) running.

IMPORTANT (dual-mode quirk): the charger is dual-mode with a PUBLIC address, so BlueZ
may try a BR/EDR (Classic) connection that times out. Force LE by disabling BR/EDR on
the adapter first:

    sudo btmgmt --index 0 power off
    sudo btmgmt --index 0 bredr off
    sudo btmgmt --index 0 le on
    sudo btmgmt --index 0 power on

Usage:
    python3 wallbox_read.py <MAC> [hciN]
    e.g. python3 wallbox_read.py AA:BB:CC:DD:EE:FF hci0
"""
import asyncio
import json
import sys
import time

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Variant
from dbus_fast.service import ServiceInterface, method

SERVICE = "2456e1b9-26e2-8f83-e744-f34f01e9d701"
D703 = "2456e1b9-26e2-8f83-e744-f34f01e9d703"   # RX/TX: write + notify
METHODS = ["r_dca", "r_dat", "r_sta"]


class PairAgent(ServiceInterface):
    """Just Works agent (NoInputNoOutput): accepts pairing without PIN/passkey."""
    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self): pass
    @method()
    def RequestPinCode(self, device: 'o') -> 's': return "0000"
    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's'): pass
    @method()
    def RequestPasskey(self, device: 'o') -> 'u': return 0
    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'): pass
    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u'): pass
    @method()
    def RequestAuthorization(self, device: 'o'): pass
    @method()
    def AuthorizeService(self, device: 'o', uuid: 's'): pass
    @method()
    def Cancel(self): pass


def eae(method_name, par=None, id=1):
    """Build an 'EaE' frame: header + length + JSON + checksum (sum % 256)."""
    payload = json.dumps({"met": method_name, "par": par, "id": id}).encode()
    frame = bytearray(b"EaE")
    frame.append(len(payload))
    frame += payload
    frame.append(sum(frame) % 256)
    return bytes(frame)


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


async def get_iface(bus, path, name):
    intro = await bus.introspect("org.bluez", path)
    obj = bus.get_proxy_object("org.bluez", path, intro)
    return obj, obj.get_interface(name)


async def managed(bus):
    _, om = await get_iface(bus, "/", "org.freedesktop.DBus.ObjectManager")
    return await om.call_get_managed_objects()


async def guarded(coro, timeout=8):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except Exception:
        return None


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    dev_mac = sys.argv[1].upper()
    adapter = sys.argv[2] if len(sys.argv) > 2 else "hci0"
    adapter_path = f"/org/bluez/{adapter}"
    dev_path = f"/org/bluez/{adapter}/dev_" + dev_mac.replace(":", "_")

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Register a Just Works pairing agent (the vendor service requires encryption).
    bus.export("/wb/agent", PairAgent())
    try:
        _, am = await get_iface(bus, "/org/bluez", "org.bluez.AgentManager1")
        await am.call_register_agent("/wb/agent", "NoInputNoOutput")
        await am.call_request_default_agent("/wb/agent")
    except Exception as e:
        log("register_agent:", e)

    aobj, adp = await get_iface(bus, adapter_path, "org.bluez.Adapter1")
    aprops = aobj.get_interface("org.freedesktop.DBus.Properties")
    powered = await aprops.call_get("org.bluez.Adapter1", "Powered")
    if not powered.value:
        await aprops.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))

    # LE-only discovery, drop any cached (BR/EDR) view of the device, keep scanning
    # active during Connect() (more reliable on BlueZ).
    try:
        await adp.call_set_discovery_filter({"Transport": Variant("s", "le")})
    except Exception:
        pass
    try:
        await adp.call_remove_device(dev_path)
    except Exception:
        pass
    await adp.call_start_discovery()

    log(f"scanning for {dev_mac} on {adapter} ...")
    for _ in range(40):
        if dev_path in (await managed(bus)):
            break
        await asyncio.sleep(1)
    else:
        log("device not seen -> abort (check MAC and signal)")
        return

    connected = False
    dev = dprops = None
    for attempt in range(10):
        try:
            dobj, dev = await get_iface(bus, dev_path, "org.bluez.Device1")
            dprops = dobj.get_interface("org.freedesktop.DBus.Properties")
            await asyncio.wait_for(dev.call_connect(), timeout=12)
            connected = True
            break
        except Exception:
            try:
                c = await dprops.call_get("org.bluez.Device1", "Connected")
                if c.value:
                    connected = True
                    break
            except Exception:
                pass
            await asyncio.sleep(1.5)
    if not connected:
        log("could not connect (force LE with `btmgmt bredr off`, and check signal)")
        return
    log("connected")

    for _ in range(25):
        try:
            sr = await dprops.call_get("org.bluez.Device1", "ServicesResolved")
            if sr.value:
                break
        except Exception:
            pass
        await asyncio.sleep(1)

    # Pair (Just Works) -> unlocks the encrypted vendor service.
    try:
        paired = await dprops.call_get("org.bluez.Device1", "Paired")
        if not paired.value:
            await guarded(dev.call_pair(), 25)
    except Exception as e:
        log("pair:", e)

    # Locate the d703 characteristic.
    d703_path = None
    for path, ifaces in (await managed(bus)).items():
        ch = ifaces.get("org.bluez.GattCharacteristic1")
        if ch and ch.get("UUID") and ch["UUID"].value == D703:
            d703_path = path
            break
    if not d703_path:
        log("d703 characteristic not found -> abort")
        return
    _, c703 = await get_iface(bus, d703_path, "org.bluez.GattCharacteristic1")

    # Notifications arrive fragmented -> reassemble until we have a full JSON object.
    buf = {"b": b""}
    cobj, _ = await get_iface(bus, d703_path, "org.freedesktop.DBus.Properties")

    def on_changed(iface, changed, invalidated):
        if "Value" in changed:
            buf["b"] += bytes(changed["Value"].value)

    cobj.on_properties_changed(on_changed)
    await guarded(c703.call_start_notify(), 12)
    await asyncio.sleep(1)

    async def request(method_name, wait=2.0):
        buf["b"] = b""
        await guarded(c703.call_write_value(eae(method_name), {}), 6)
        await asyncio.sleep(wait)
        return buf["b"].decode("utf-8", "ignore")

    for m in METHODS:
        resp = await request(m)
        log(f"{m}: {resp}")
        if m == "r_dca":
            try:
                r = json.loads(resp[resp.find("{"):]).get("r", {})
                house_load = r.get("p1", 0) + r.get("p2", 0) + r.get("p3", 0)
                log(f"  -> HOUSE LOAD = {house_load} W  (v1={r.get('v1')} V, "
                    f"energy={r.get('e', 0) / 1000:.2f} kWh)")
            except Exception:
                pass

    try:
        await c703.call_stop_notify()
    except Exception:
        pass
    try:
        await dev.call_disconnect()
    except Exception:
        pass
    try:
        await adp.call_stop_discovery()
    except Exception:
        pass
    log("done")


if __name__ == "__main__":
    asyncio.run(main())
