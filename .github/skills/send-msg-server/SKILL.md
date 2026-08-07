---
name: send-msg-server
description: "Use when: sending push alert / user alert button press to device via TCP, triggering alert_button event, simulating long_press, installer_button_press, or longpress_offduty. Works over serial connection by running a Python TCP client on the device itself connecting to localhost:12347. Ported from nd_test_bot (6.14_changes branch) send_msg_server.py."
argument-hint: "alert type (e.g., push_alert, long_press, longpress_offduty)"
---

# Send Msg Server

Send button press events to the device's TCP msg server (port 12347) to trigger alerts, long presses, and other button-simulated actions. Works via serial connection by executing a Python one-liner on the device that connects to `localhost:12347`.

**Source**: Ported from `nd_test_bot` repo, branch `6.14_changes`, file `Test_Automation_Framework/Lib/apis/send_msg_server.py`.

## When to Use

- Trigger a **user alert** (push alert button press) for alert session tests
- Simulate a **long press** for privacy mode activation
- Simulate an **off-duty long press** for off-duty privacy mode
- Simulate an **installer button press**
- Send **SDK errors** to the device
- Any TC step that says "Push alert to device" or "send alert_button via TCP"

## Prerequisites

- Device connected via serial (`/dev/ttyACM0`) or ADB
- Python3 available on the device
- Device TCP msg server listening on port **12347** (verify: `ss -tlnp | grep 12347`)
- `serial_conn.py` available for D450 serial communication

## Supported Actions

| Action | JSON Payload | Description |
|--------|-------------|-------------|
| `push_alert` | `{"alert": "alert_button"}` | Trigger user alert button press |
| `long_press` | `{"long_press": "long_press_button"}` | Simulate long button press (privacy toggle) |
| `longpress_offduty` | `{"longpress_offduty": {}}` | Simulate off-duty long press |
| `installer_button_press` | `{"installer_app": "installer_app_button"}` | Simulate installer app button |
| `push_sdk_error` | `{"sdk_error": "<error_type>"}` | Send SDK error event |
| `run_command` | `{"command_to_run": "<cmd>"}` | Run command via DTA |
| `reboot` | `{"reboot": "reboot_device"}` | Request device reboot via DTA |

## Procedure

### Step 1 — Verify TCP server is listening

Run via serial:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ss -tlnp | grep 12347"
```

Expected: A line showing port 12347 in LISTEN state. If not listening, the bagheera/ndcentral service may not be running.

### Step 2 — Send the alert via serial (localhost TCP)

For **push_alert** (user alert button press):
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'alert':'alert_button'}).encode()); print('ALERT_SENT_OK'); s.close()\""
```

For **long_press**:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'long_press':'long_press_button'}).encode()); print('LONG_PRESS_SENT_OK'); s.close()\""
```

For **longpress_offduty**:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'longpress_offduty':{}}).encode()); print('OFFDUTY_PRESS_SENT_OK'); s.close()\""
```

For **installer_button_press**:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'installer_app':'installer_app_button'}).encode()); print('INSTALLER_SENT_OK'); s.close()\""
```

For **push_sdk_error**:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'sdk_error':'<ERROR_TYPE>'}).encode()); print('SDK_ERROR_SENT_OK'); s.close()\""
```

### Step 3 — Verify the alert was received

Check expected output: `ALERT_SENT_OK` (or equivalent `*_SENT_OK` message).

Then verify in device logs:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "grep -iE 'User alert msg received|alert_button|user alert' /home/ubuntu/.nddevice/log/ndcentral/log_*.log 2>/dev/null | awk -F: -v ts=<TIMESTAMP> '\$1 >= ts' | tail -5"
```

Expected log patterns after push_alert:
- `User alert msg received` in ndcentral logs
- `Will copy CAM0 video because of user alert`
- `Will copy audio because of user alert`
- `sending msg to hs : {"session": "...", "alert_info": {...}}`

Expected log patterns after long_press:
- `BUTTON_LONG_PRESS received` in ndcentral logs
- `Privacy Mode is Activated` or `Privacy Mode is Deactivated`

## Using send_msg_server.py CLI

The script `claude_device_validator/src/send_msg_server.py` works with **all connection types**:

### Serial/Minicom devices (D450 — `--via-serial`)
Runs the TCP client ON the device via `serial_conn.py`, connecting to `localhost:12347`:
```bash
# Push alert
python3 claude_device_validator/src/send_msg_server.py --device-id <DEVICE_ID> --via-serial push_alert

# Long press
python3 claude_device_validator/src/send_msg_server.py --device-id <DEVICE_ID> --via-serial long_press

# Off-duty long press
python3 claude_device_validator/src/send_msg_server.py --device-id <DEVICE_ID> --via-serial longpress_offduty

# Installer button
python3 claude_device_validator/src/send_msg_server.py --device-id <DEVICE_ID> --via-serial installer_button_press

# SDK error
python3 claude_device_validator/src/send_msg_server.py --device-id <DEVICE_ID> --via-serial push_sdk_error --error-type drowsy_detected --count 3
```

### ADB-connected devices (`--via-adb`)
Runs the TCP client ON the device via `adb shell`, connecting to `localhost:12347`:
```bash
# Push alert (single ADB device)
python3 claude_device_validator/src/send_msg_server.py --via-adb push_alert

# Push alert (specific ADB device by serial)
python3 claude_device_validator/src/send_msg_server.py --device-id <ADB_SERIAL> --via-adb push_alert

# Long press
python3 claude_device_validator/src/send_msg_server.py --via-adb long_press
```

### Direct TCP (network-connected devices — `--device-ip`)
Connects directly from host to device IP:
```bash
# Push alert
python3 claude_device_validator/src/send_msg_server.py --device-ip <IP> push_alert

# Resolve IP from serial and connect
python3 claude_device_validator/src/send_msg_server.py --device-id <DEVICE_ID> push_alert
```

## Important Notes

- **Port 12347** is the DTA (Device Test Agent) TCP server port
- The device must have the DTA service running (part of ndcentral/bagheera)
- For D450 (bagheera3) via serial: always use `localhost` / `127.0.0.1` approach
- For network-connected devices: can connect directly to device IP on port 12347
- After sending push_alert, wait **60-65 seconds** for the alert session to complete
- Alert session includes: LED blink, video copy for all cameras, audio copy, metadata generation  
