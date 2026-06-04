# Linux / BlueZ tools

Reference client for talking to the Wallbox from a Linux machine (e.g. a Raspberry Pi).
Handy for exploring the protocol; the ESP32 firmware in `../esphome/` is the actual product.

## `wallbox_read.py`

Connects, pairs (Just Works), and reads the three known methods (`r_dca`, `r_dat`, `r_sta`),
printing the decoded values including the computed house load.

```bash
pip install dbus-fast
python3 wallbox_read.py <MAC> [hciN]
# e.g.
python3 wallbox_read.py AA:BB:CC:DD:EE:FF hci0
```

### Force LE first (important on Linux)

The charger is **dual-mode** with a **public** address, so BlueZ may attempt a BR/EDR (Classic)
connection that times out (`Page Timeout`). Disable BR/EDR on the adapter so all connections are LE:

```bash
sudo btmgmt --index 0 power off
sudo btmgmt --index 0 bredr off
sudo btmgmt --index 0 le on
sudo btmgmt --index 0 power on
```

(Replace `0` with your adapter index; `btmgmt info` lists them. This setting does not persist a
reboot.) The ESP32 firmware does not need any of this — its stack is LE-only.

### Notes
- A USB BlueZ dongle that needs a patchram firmware blob (e.g. Broadcom BCM20702) must have that
  firmware installed, or the data channel will be unreliable. Built-in Pi radios are fine.
  Community blobs: [winterheart/broadcom-bt-firmware](https://github.com/winterheart/broadcom-bt-firmware).
- Aim for RSSI better than ~ -75 dBm; the connection handshake needs more signal than scanning.

### Built with
- [dbus-fast](https://github.com/Bluetooth-Devices/dbus-fast) — async BlueZ D-Bus library.
- Inspired by [jagheterfredrik/wallbox-ble](https://github.com/jagheterfredrik/wallbox-ble). See the
  repo root [README](../README.md#credits--acknowledgments) for full credits.
