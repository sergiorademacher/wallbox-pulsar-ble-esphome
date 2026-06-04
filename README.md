# Wallbox Pulsar BLE → Home Assistant (ESPHome)

Read your **Wallbox Pulsar / Pulsar Plus** charger over **Bluetooth Low Energy** with a
cheap **ESP32** and expose it to **Home Assistant** — no cloud, no account, fully local.

The headline feature is the **whole-house power** measured by the external meter wired to
the Wallbox **Power Boost** (e.g. a *Carlo Gavazzi EM112*). That value is otherwise hard to
get locally; this project surfaces it as `sensor.house_load_power`, along with per-phase
voltage/current, cumulative energy, and charger status.

> **Read-only.** This firmware only *reads* from the charger. It does not change any
> charger setting. See [Limitations](#limitations).

<p align="center"><em>House Load Power, live, straight into Home Assistant.</em></p>

---

## Features

Entities published to Home Assistant:

| Entity | Source | Notes |
|---|---|---|
| **House Load Power** (W) | meter | `p1 + p2 + p3` — total house consumption |
| Power L1 / L2 / L3 (W) | meter | per-phase active power |
| Voltage L1 / L2 / L3 (V) | meter | per-phase voltage |
| Current L1 / L2 / L3 (A) | meter | per-phase current |
| Energy Total (kWh) | meter | cumulative energy counter |
| Charging Power, Set Current, Charge Current L1–L3 | charger | live charging state |
| Charger / Lock / Meter / Power-sharing status, Max currents | charger | diagnostics |
| **Wallbox connected** (binary) | — | BLE link up/down |
| **Wallbox link** (switch) | — | turn OFF to free the BLE link for the mobile app |
| Restart, Read now (buttons) | — | convenience |

A single-phase install simply reports everything on L1 (L2/L3 = 0).

---

## Hardware

- Any **ESP32** with BLE (classic ESP32-WROOM, ESP32-C3, ESP32-S3…). **Not** the ESP32-**S2** (no BLE).
- Mount it **within good BLE range of the charger** — aim for RSSI better than about **-75 dBm**.
  Below ~ -85 dBm the connection handshake tends to fail even though scanning still sees the device.
- A USB power supply.

This has been validated on an **ESP32-C3 (LOLIN C3 mini)** with ESPHome 2026.5 / ESP-IDF.

---

## Setup

### 1. Install ESPHome
```bash
pip install esphome
```
…or use the Home Assistant **ESPHome** add-on / dashboard.

### 2. Secrets
```bash
cd esphome
cp secrets.yaml.example secrets.yaml
# edit secrets.yaml: wifi_ssid, wifi_password, api_key, ota_password
```

### 3. Find your Wallbox BLE MAC
The charger advertises as `WBxxxxxxx`. Find its MAC with any BLE scanner:

- **Linux:** `bluetoothctl` → `scan on` → look for a name like `WB1234567`.
- **Phone:** the *nRF Connect* app (iOS/Android) → scan → find `WBxxxxxxx`.
- **Home Assistant:** if you already run a Bluetooth proxy, the device shows up under discovered Bluetooth devices.

Put that MAC in `esphome/wallbox.yaml`:
```yaml
substitutions:
  wallbox_mac: "AA:BB:CC:DD:EE:FF"   # <-- your charger
```

> If you have two paired units (master + satellite via Power Boost), the meter data lives on the
> unit wired to the meter — usually the **master**. Either unit is connectable once paired.

### 4. Flash
```bash
cd esphome
esphome run wallbox.yaml          # first time over USB, then OTA over WiFi
esphome logs wallbox.yaml         # watch it connect
```

On success you'll see:
```
Wallbox connected -> starting encryption (Just Works bonding)
... auth complete ... auth success ...
House load = 1690 W
```
and the entities appear in Home Assistant automatically (native API).

---

## Using the mobile app while the ESP32 is connected

A Wallbox accepts **one** BLE central at a time. While the ESP32 holds the link, the official
Wallbox app cannot connect. Toggle the **Wallbox link (ESP32)** switch **OFF** to release the
link (it disconnects and stops reconnecting), use the app, then switch it back **ON**.

---

## How it works (short version)

The charger exposes a vendor GATT service. We:

1. Connect over **BLE LE** and perform a **Just Works** pairing (the vendor service requires an
   encrypted link).
2. Write a small framed request — `EaE` header + length + JSON + checksum — to the RX/TX
   characteristic, asking for a *method* such as `r_dca`.
3. Receive the JSON reply as a (fragmented) GATT **notification**, reassemble it, and parse it.

`r_dca` returns the power meter; `r_dat` the charger state; `r_sta` status/config.

Full details, byte layouts, method list and field meanings are in **[PROTOCOL.md](PROTOCOL.md)**.

---

## Troubleshooting

- **`status=15` (insufficient encryption) when reading the characteristic.**
  The vendor service needs an encrypted link. This firmware triggers it with
  `esp_ble_set_encryption(..., ESP_BLE_SEC_ENCRYPT)` in `on_connect`. If you removed that, add it back.
  After the first successful pairing the ESP32 stores the bond and reconnects without re-pairing.

- **It scans the device but never connects (timeouts).**
  Almost always **weak signal** — move the ESP32 closer (RSSI > -75 dBm). On the ESP32 this is LE
  only, so the Linux "dual-mode / BR-EDR" problem (below) does not apply.

- **`Ignoring unexpected GAP event type: 9` warnings.**
  Harmless. That's the key-exchange event during bonding; ESPHome just doesn't have a handler for it.

- **`House Load Power` flickers to *unavailable*.**
  Make sure the BLE-client sensor is `internal: true` and `House Load Power` is the separate
  `template` sensor (as shipped). The internal sensor must never publish its own value.

- **Running a Bluetooth proxy on the same ESP32.**
  Possible, but BLE connection slots are limited (≈3 on a C3). Set `esp32_ble: max_connections: 4`
  so the charger's client always gets a slot, or keep this ESP **dedicated** to the Wallbox.

---

## Linux / BlueZ reference tool

`tools/wallbox_read.py` is a small Python (dbus-fast) client to talk to the charger from a Linux
box (handy for exploring the protocol). On Linux the charger is **dual-mode**, and BlueZ may try a
**BR/EDR (Classic)** connection that times out (`Page Timeout`). Force LE by disabling BR/EDR on the
adapter first:

```bash
sudo btmgmt --index 0 power off
sudo btmgmt --index 0 bredr off
sudo btmgmt --index 0 le on
sudo btmgmt --index 0 power on
python3 tools/wallbox_read.py AA:BB:CC:DD:EE:FF hci0
```
(The ESP32 firmware does **not** need this — its BLE stack is LE-only.)

See [tools/README.md](tools/README.md).

---

## Limitations

- **Read-only by design.** Writing charger settings (current limit, lock, …) is not implemented.
- The non-meter fields (`r_dat` / `r_sta`) are decoded **best-effort**; some are inferred. PRs welcome.
- Tested against one firmware family (vendor service `2456e1b9-…`). Other Wallbox firmware versions
  may differ — see PROTOCOL.md and please report what you find.

---

## Disclaimer

This is an **independent, community** project. **Use entirely at your own risk.**

- **No affiliation / no endorsement.** This project is not affiliated with, authorized, sponsored,
  or endorsed by Wallbox Chargers, S.L. or any of its affiliates.
- **Trademarks.** "Wallbox", "Pulsar", "Pulsar Plus" and "Power Boost" are trademarks of their
  respective owners. They are used here **only for identification and descriptive purposes**
  (nominative fair use) to indicate compatibility. No claim is made to any such mark.
- **No warranty.** The software is provided "AS IS", without warranty of any kind, as stated in the
  [MIT License](LICENSE). The authors and contributors are **not liable** for any damage to your
  charger, devices, property, data, or for any loss or injury, however caused.
- **You are responsible.** Connecting to, pairing with, or reading from your charger may be subject
  to the manufacturer's terms of use and **may void your warranty**. You are solely responsible for
  ensuring your use complies with all **applicable laws, regulations, and agreements** in your
  jurisdiction, and with your charger's terms and conditions.
- **Reverse engineering.** The protocol notes were derived by observing local Bluetooth traffic with
  the owner's own device, for **interoperability and personal use**. No proprietary firmware,
  source code, or confidential material is included or redistributed.
- **Accuracy.** Field meanings are best-effort and may be wrong or incomplete. **Do not rely on
  these values for billing, safety-critical, or protective functions.**
- **Read-only.** This firmware only reads data; it does not change charger settings or wiring. Do
  not perform any electrical work yourself — consult a qualified electrician for anything involving
  mains wiring or the meter installation.

If you are a rights holder and have a concern about this repository, please open an issue.

## Credits & acknowledgments

A community reverse-engineering effort, built on top of [ESPHome](https://esphome.io) and
[Home Assistant](https://www.home-assistant.io).

This project stands on the shoulders of prior work — thank you to:

- **[jagheterfredrik/wallbox-ble](https://github.com/jagheterfredrik/wallbox-ble)** — Home Assistant
  component for local control of the Wallbox Pulsar Plus over BLE. This was the starting point and
  inspiration for the whole BLE approach and the `EaE` framing concept. Note: the firmware family
  targeted here exposes a *different* vendor service/characteristics and method set
  (`r_dca` / `r_dat` / `r_sta`), so the details in [PROTOCOL.md](PROTOCOL.md) were independently
  re-derived for it.
- **[jagheterfredrik/wallbox-tooling](https://github.com/jagheterfredrik/wallbox-tooling)** — tools
  and proofs of concept for extending the Pulsar Plus.
- **[winterheart/broadcom-bt-firmware](https://github.com/winterheart/broadcom-bt-firmware)** —
  patchram firmware blobs for Broadcom USB Bluetooth dongles, used during development on Linux /
  Raspberry Pi.
- **[dbus-fast](https://github.com/Bluetooth-Devices/dbus-fast)** — the BlueZ D-Bus library used by
  the Linux reference tool in [`tools/`](tools/).

Licensed under the [MIT License](LICENSE).
