---
name: relay-control
description: "Use when: controlling ignition relay, turning ignition on/off, reading automation_config.ini from device, looking up Raspberry Pi IP from MongoDB, sending relay curl commands. Handles the full flow: ADB config read → MongoDB Pi IP lookup → HTTP relay toggle."
argument-hint: "on or off (e.g., /relay-control on)"
---

# Relay Control

Control the ignition relay connected to a device under test. Reads the device's `automation_config.ini` via ADB, resolves the Raspberry Pi IP from MongoDB, and sends an HTTP command to toggle the relay.

## When to Use

- Turn device ignition **on** or **off** before/after a test run
- Verify relay connectivity during sanity checks
- Any test workflow that requires ignition state changes

## Prerequisites

- Device connected via ADB (`adb devices` shows the serial)
- MongoDB running locally (`mongodb://localhost:27017/`)
- The `pymongo` Python package installed (`pip install pymongo`)
- Raspberry Pi relay server reachable on port **8081**

## Procedure

### Step 1 — Read automation config from the device

Run via ADB to fetch the config file:

```bash
adb -s <SERIAL> shell "cat /home/ubuntu/config/automation_config.ini"
```

Expected output format:

```ini
[test_automation]
automation=true
ignition_relay_no=0
ignition_relay_id=AB0KKU70
raspberry_id=pi1testdev
```

Extract these three values:
| Key | Description |
|-----|-------------|
| `raspberry_id` | Hostname used to look up the Pi's IP in MongoDB |
| `ignition_relay_no` | Relay channel number (e.g., `0`) |
| `ignition_relay_id` | Relay board identifier (e.g., `AB0KKU70`) |

If `automation` is not `true`, stop and inform the user that automation is disabled on this device.

### Step 2 — Look up Raspberry Pi IP from MongoDB

Query the `ip_db.automation_peripheral` collection for the Pi's current IP:

```bash
python3 -c "
from pymongo import MongoClient
client = MongoClient('mongodb://localhost:27017/')
doc = client['ip_db']['automation_peripheral'].find_one({'Username': '<RASPBERRY_ID>'})
if doc:
    print(doc['IP_Address'])
else:
    print('ERROR: No document found for <RASPBERRY_ID>')
"
```

If no document is found, report failure — the Pi may be offline or not registered.

### Step 3 — Send relay command

Build and execute the curl command to toggle the relay:

```bash
curl "http://<PI_IP>:8081/Relay?id=<RELAY_ID>&<RELAY_NO>=<STATUS>"
```

Where:
- `<PI_IP>` — IP address from Step 2
- `<RELAY_ID>` — `ignition_relay_id` from Step 1
- `<RELAY_NO>` — `ignition_relay_no` from Step 1
- `<STATUS>` — `on` or `off` (from user input)

**Example:**
```bash
curl "http://192.168.1.50:8081/Relay?id=AB0KKU70&0=on"
```

### Step 4 — Verify and report

After sending the curl command:
1. Check the HTTP response (should be `200 OK` or a success body)
2. Report the result:

```
Relay Control Result:
  Device:       <SERIAL>
  Raspberry Pi: <RASPBERRY_ID> (<PI_IP>)
  Relay ID:     <RELAY_ID>
  Channel:      <RELAY_NO>
  Action:       <STATUS>
  Result:       SUCCESS | FAILED (<error>)
```

## One-Shot Script

For convenience, use the bundled script that handles the full flow:

```bash
python3 .github/skills/relay-control/scripts/relay_control.py --serial <SERIAL> --action <on|off>
```

See [relay_control.py](./scripts/relay_control.py) for implementation.

## Error Handling

| Error | Action |
|-------|--------|
| `automation_config.ini` not found on device | FAIL — config missing, device may not be set up for automation |
| `automation=false` | FAIL — automation disabled on this device |
| MongoDB document not found for `raspberry_id` | FAIL — Pi not registered; check if Pi is online and reporting |
| Curl command fails or times out | FAIL — Pi relay server unreachable; check network/Pi status |
| Relay response indicates error | FAIL — report the response body |

## Constraints

- This skill only controls the **ignition** relay.
- Do NOT run relay commands without the user explicitly requesting on/off.
- Always read the config fresh from the device — do not cache across sessions.
