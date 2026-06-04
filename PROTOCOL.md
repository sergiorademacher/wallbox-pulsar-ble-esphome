# Wallbox Pulsar BLE protocol (reverse-engineered)

This documents the local **Bluetooth Low Energy** protocol used by a Wallbox Pulsar / Pulsar Plus
firmware family whose vendor GATT service is `2456e1b9-26e2-8f83-e744-f34f01e9d701`.

> Reverse-engineered by observing local Bluetooth traffic with the owner's own device, for
> interoperability and personal use. Field meanings are best-effort and some are inferred;
> corrections / additions welcome. This is **not** official documentation, and this project is
> independent and not affiliated with or endorsed by Wallbox Chargers, S.L. Trademarks belong to
> their respective owners. No proprietary firmware or source code is included. See the
> [Disclaimer](README.md#disclaimer). **Use at your own risk.**

---

## 1. Link layer

- **Transport:** BLE **LE only**. The module (a u-blox **NINA-B22**) is dual-mode (BR/EDR + LE), and
  its BLE address is a **public** address. On a dual-mode host (Linux/BlueZ) a plain `Connect` may be
  attempted over **BR/EDR (Classic)** and fail with **Page Timeout** — you must force LE
  (`btmgmt bredr off`). An ESP32 is LE-only, so it connects directly.
- **Pairing:** the vendor service requires an **encrypted** link. Pairing is **Just Works**
  (`NoInputNoOutput`, no PIN/passkey). Without encryption, reads/notifications on the vendor
  characteristics fail with ATT **status `0x0F` (insufficient encryption)**.
  - On ESP32 (ESP-IDF): set IO capability to `none` and call
    `esp_ble_set_encryption(bda, ESP_BLE_SEC_ENCRYPT)` after connecting.
  - On Linux (BlueZ): register a `NoInputNoOutput` agent and `Pair()` the device.
- **Connecting** is only reliable at decent signal (RSSI better than ~ -75 dBm). Scanning tolerates
  much weaker signal than the connection handshake.
- Only **one** central can be connected at a time (releasing the link frees it for the phone app).

---

## 2. GATT layout

| UUID | Role | Properties |
|---|---|---|
| `2456e1b9-26e2-8f83-e744-f34f01e9d701` | Vendor service | — |
| `2456e1b9-26e2-8f83-e744-f34f01e9d703` | **RX/TX** | read, write, write-without-response, **notify** |
| `2456e1b9-26e2-8f83-e744-f34f01e9d704` | aux | write, notify (returns a 1-byte ack `0x10`) |

Use **`…d703`** for everything: **write** the request frame to it, and **enable notifications** on
it to receive the reply. (`…d704` returns only a one-byte acknowledgement and is not needed.)

Standard GATT/DIS characteristics are also present and readable without encryption, e.g.
`0x2A00` Device Name = `WBxxxxxxx`, `0x2A29` Manufacturer = `u-blox`, `0x2A24` Model = `NINA-B22`.

---

## 3. Frame format ("EaE")

Every request and response is wrapped in this framing:

```
+-------+-------+-------------------+----------+
| "EaE" |  LEN  |   JSON payload    | CHECKSUM |
| 3 B   |  1 B  |   LEN bytes       |   1 B    |
+-------+-------+-------------------+----------+
```

- **`"EaE"`** — literal ASCII bytes `0x45 0x61 0x45`.
- **`LEN`** — length of the JSON payload in bytes (single byte).
- **JSON payload** — UTF-8, see below.
- **`CHECKSUM`** — `(sum of every preceding byte) mod 256`, i.e. the sum of `E,a,E`, the length
  byte, and all JSON bytes, modulo 256.

### Building a frame (Python)
```python
import json
def eae(method, par=None, id=1):
    payload = json.dumps({"met": method, "par": par, "id": id}).encode()  # note: spaces, like json.dumps default
    frame = bytearray(b"EaE")
    frame.append(len(payload))
    frame += payload
    frame.append(sum(frame) % 256)
    return bytes(frame)
```

### Building a frame (C++ / ESPHome lambda)
```cpp
std::string j = "{\"met\": \"r_dca\", \"par\": null, \"id\": 1}";
std::vector<uint8_t> f = {'E','a','E', (uint8_t)j.size()};
for (char c : j) f.push_back((uint8_t)c);
uint8_t s = 0; for (uint8_t b : f) s += b; f.push_back(s);
```

### Request payload
```json
{"met": "<method>", "par": <params or null>, "id": <number>}
```

### Response payload
Success:
```json
{"id": <number>, "r": { ...result... }}
```
Unknown method:
```json
{"error": {"code": 1, "message": "No dispatch method found"}, "id": <number>}
```

> **Notifications are fragmented.** A single reply arrives as several GATT notifications (sometimes
> byte-by-byte). Accumulate the bytes until you have a complete JSON object (it ends with `}}`),
> then parse.

---

## 4. Methods

Of ~30 `r_*` names probed, only three exist; everything else returns `No dispatch method found`.
(All requests above used `"par": null`.)

### `r_dca` — power meter (the external EM112 / Power Boost meter)
```json
{"id":1,"r":{"c1":76,"c2":0,"c3":0,"e":402400,"p1":1698,"p2":0,"p3":0,"v1":228,"v2":0,"v3":0}}
```
| Field | Meaning | Unit |
|---|---|---|
| `p1` `p2` `p3` | active power per phase | **W** |
| `v1` `v2` `v3` | voltage per phase | **V** |
| `c1` `c2` `c3` | current per phase | **deci-amps** (divide by 10 → A) |
| `e` | cumulative energy | **Wh** (divide by 1000 → kWh) |

**Whole-house load = `p1 + p2 + p3` (W).** Single-phase installs use L1 only.
Sanity check: `v1 × (c1/10) ≈ p1` (e.g. `228 × 7.6 ≈ 1733 ≈ 1698`, difference is power factor).

### `r_dat` — charger state
```json
{"id":1,"r":{"L1":0,"L2":0,"L3":0,"cp":0.0,"cur":32,"den":0,"en":0,"gen":0,
             "grid":0,"mid":1,"ocpp":1,"ps":3,"s":0,"st":0,"usid":1}}
```
| Field | Meaning (best-effort) | Unit / notes |
|---|---|---|
| `L1` `L2` `L3` | charger output current per phase | deci-amps (÷10) when charging |
| `cp` | charging power | kW |
| `cur` | offered / set current | A |
| `mid` | MID energy meter present | 0/1 |
| `ocpp` | OCPP enabled | 0/1 |
| `ps` | power-sharing role | enum (1 = satellite, 3 = master, observed) |
| `s` | charger state | enum |
| `st` | status | enum |
| `usid` | user / session id | — |
| `grid` `gen` `den` `en` | unknown (0 in all observations) | **unverified** |

> Note: `r_dat`'s `grid` is **not** the meter reading — it stayed 0 with real house load. Use `r_dca`.

### `r_sta` — status / configuration
```json
{"id":1,"r":{"charger_status":0,"external_meter_status":2,"lock_status":0,
             "max_available_current":32,"max_charging_current":32,"mid_status":1,
             "ocpp_status":1,"phases_connection":3,"power_sharing_status":3}}
```
| Field | Meaning (best-effort) | Notes |
|---|---|---|
| `charger_status` | charger state machine | enum |
| `external_meter_status` | Power Boost / external meter status | `2` observed = present/OK |
| `lock_status` | charger lock | 0 = unlocked |
| `max_available_current` | max current the install allows | A |
| `max_charging_current` | configured charging limit | A |
| `mid_status` | MID meter status | — |
| `ocpp_status` | OCPP status | — |
| `phases_connection` | phase wiring | enum (`3` observed) |
| `power_sharing_status` | power-sharing status | enum |

---

## 5. Typical session

```
1. Scan (LE)                         → find WBxxxxxxx, note its public MAC
2. Connect (LE)                      → GATT
3. Pair / encrypt (Just Works)       → required before vendor reads work
4. Enable notifications on …d703
5. Write EaE frame for "r_dca" to …d703
6. Reassemble notification(s)        → {"r":{...}}, take p1+p2+p3 = house load
7. Repeat (e.g. every 15 s); optionally also poll "r_dat" and "r_sta"
```

---

## 6. Write methods?

Not investigated here — this project is read-only. Write/command methods (likely `w_*` / `s_*`) were
**deliberately not probed** to avoid changing charger behavior. If you explore them, do so carefully
and at your own risk.
