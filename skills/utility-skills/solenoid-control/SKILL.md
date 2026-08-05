---
name: solenoid-control
description: "Use when: triggering solenoid blink to simulate Wake-on-Motion (WOM) on a device. Reads automation_config.ini from device via ADB, looks up Raspberry Pi IP from MongoDB, sends HTTP blink command to the solenoid. Handles the full flow: ADB config read → MongoDB Pi IP lookup → HTTP solenoid blink."
argument-hint: "Just invoke — always sends blink (e.g., /solenoid-control)"
---

# Solenoid Control

Trigger a solenoid blink on a device under test to simulate Wake-on-Motion (WOM). Reads the device's `automation_config.ini` via ADB, resolves the Raspberry Pi IP from MongoDB, and sends an HTTP blink command to the solenoid relay channel.

## When to Use

- Trigger WOM (Wake-on-Motion) wakeup during power management tests
- Any test workflow that requires physical vibration/motion simulation on the device

## When NOT to Use

- Controlling ignition on/off → use `relay-control` skill instead

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
solenoid_no=1
solenoid_id=XY1234AB
raspberry_id=pi1testdev
```

Extract these three values:
| Key | Description |
|-----|-------------|
| `raspberry_id` | Hostname used to look up the Pi's IP in MongoDB |
| `solenoid_no` | Solenoid relay channel number (e.g., `1`) |
| `solenoid_id` | Solenoid board identifier (e.g., `XY1234AB`) |

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

### Step 3 — Send solenoid blink command

Build and execute the curl command:

```bash
curl "http://<PI_IP>:8081/Relay?id=<SOLENOID_ID>&<SOLENOID_NO>=blink"
```

Where:
- `<PI_IP>` — IP address from Step 2
- `<SOLENOID_ID>` — `solenoid_id` from Step 1
- `<SOLENOID_NO>` — `solenoid_no` from Step 1
- Action is always `blink`

**Example:**
```bash
curl "http://192.168.1.50:8081/Relay?id=XY1234AB&1=blink"
```

### Step 4 — Verify and report

After sending the curl command:
1. Check the HTTP response (should be `200 OK` or a success body)
2. Report the result:

```
Solenoid Control Result:
  Device:       <SERIAL>
  Raspberry Pi: <RASPBERRY_ID> (<PI_IP>)
  Solenoid ID:  <SOLENOID_ID>
  Channel:      <SOLENOID_NO>
  Action:       blink
  Result:       SUCCESS | FAILED (<error>)
```

## One-Shot Script

For convenience, use the bundled script that handles the full flow:

```bash
python3 .github/skills/solenoid-control/scripts/solenoid_control.py --serial <SERIAL>
```

See [solenoid_control.py](./scripts/solenoid_control.py) for implementation.

## Error Handling

| Error | Action |
|-------|--------|
| `automation_config.ini` not found on device | FAIL — config missing, device may not be set up for automation |
| `automation=false` | FAIL — automation disabled on this device |
| `solenoid_no` or `solenoid_id` not found | FAIL — solenoid not configured on this device |
| MongoDB document not found for `raspberry_id` | FAIL — Pi not registered; check if Pi is online and reporting |
| Curl command fails or times out | FAIL — Pi relay server unreachable; check network/Pi status |
| Relay response indicates error | FAIL — report the response body |

## Constraints

- This skill only controls the **solenoid** (blink action).
- For ignition relay on/off, use the `relay-control` skill instead.
- Always read the config fresh from the device — do not cache across sessions.
 
