---
name: device-controller
description: "Use when: tracking device reboot timing, checking service keepalive intervals, tracking low power wakeup, tracking device shutdown/turn-on, getting device UDID, waiting for device to come back online after reboot, or executing commands on device. Ported from nd_test_bot (6.14_changes branch) reboot_device.py — all functions except reboot_device(). Works over serial connection via serial_conn.py."
argument-hint: "action (e.g., track_reboot, keepalive_check, track_low_power, track_turn_off, get_udid, ping_device)"
---

# Device Controller

Device lifecycle management functions: track reboots, check keepalive, monitor low power wakeup cycles, track shutdown/turn-on, get UDID, and execute commands. All operations run via serial connection using `serial_conn.py` (for D450/bagheera3/bagheera2) or `adb shell` (for D210/krait/krait2).

**Source**: Ported from `nd_test_bot` repo, branch `6.14_changes`, file `Test_Automation_Framework/Lib/apis/reboot_device.py` — all functions **except** `reboot_device()`.

## When to Use

- **track_reboot**: After triggering a reboot, verify device rebooted within expected time and UDID incremented by 1
- **keepalive_check**: Verify a service is sending keepalive at the correct interval (60s for nd_central, 30s for others)
- **track_low_power**: Track ignition-off → low power → wakeup cycle with timing validation
- **track_turn_off**: Verify device shuts down within max time after ignition off
- **track_turn_on**: After device shutdown, wait and verify it comes back within expected time
- **get_udid**: Get current UDID from device gen_property.db (increments on each reboot)
- **ping_device**: Wait for device to become reachable after reboot/power cycle
- **command_execute**: Run any command on device via serial

## Prerequisites

- Device connected via serial (`/dev/ttyACM0`) for D450 or ADB for D210
- `serial_conn.py` available at `claude_device_validator/src/serial_conn.py`
- DTA (Device Test Agent) TCP server listening on port **12347** (for track_reboot, track_low_power, track_turn_off)

## Supported Actions

| Action | Description | DTA Required |
|--------|-------------|:---:|
| `ping_device` | Poll device until it responds | No |
| `keepalive_check <service>` | Check keepalive interval for a service | No |
| `command_execute <cmd>` | Run command on device | No |
| `get_udid` | Get UDID from gen_property.db | No |
| `prepare_to_track_reboot <max_sec>` | Prepare DTA to track reboot | Yes |
| `track_reboot <expected_sec> <max_sec>` | Track reboot occurred within timing window | Yes |
| `track_low_power <off_expected> <off_max> <on_expected> <on_max>` | Track full low power cycle | Yes |
| `track_turn_off <max_sec>` | Track device shutdown | Yes |
| `track_turn_on <max_sec>` | Wait for device to turn on after shutdown | No |

## Procedures

### ping_device — Wait for device to come back online

Poll device via serial until it responds (useful after reboot):
```bash
# Poll uptime until device responds (near-zero uptime confirms fresh reboot)
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "uptime -s"
```

Loop until device responds. Timeout after 300s. Verify uptime is recent (< 120s since reboot was triggered).

### keepalive_check — Verify service keepalive interval

Check that a service sends keepalive at the correct interval:
- `nd_central`: every ~60 seconds (55-63s acceptable)
- All other services: every ~30 seconds (25-33s acceptable)

```bash
# Get last 3 keepalive entries from svc logs
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "grep -iraI 'Keep alive received from: q_<SERVICE>' /home/ubuntu/.nddevice/log/svc/log_*.log 2>/dev/null | tail -3"
```

Extract epoch timestamps from each line (field 2, colon-delimited), compute differences, and verify they fall within the acceptable range.

### get_udid — Get device UDID

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "sqlite3 /home/ubuntu/.nddevice/gen_property.db 'SELECT DATA FROM GENPROP WHERE PROPERTY=\"udid\";'"
```

UDID increments by 1 on each reboot. Compare before/after reboot to confirm exactly 1 reboot occurred.

### prepare_to_track_reboot — Setup DTA reboot tracking

Send `track_reboot_or_shutdown` message to DTA before triggering the event that causes reboot:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'track_reboot_or_shutdown':{'timeout':<MAX_SEC>}}).encode()); print('TRACK_REBOOT_SENT_OK'); s.close()\""
```

### track_reboot — Verify reboot happened within expected timing

After reboot is triggered:
1. Get UDID before (from prepare step)
2. Wait for device to come back (`ping_device`)
3. Get UDID after — must be exactly `before_udid + 1`
4. Query DTA for reboot status:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'get_reboot_or_shutdown_status':{}}).encode()); data=s.recv(4096); print(data.decode()); s.close()\""
```

Response JSON: `{"device_rebooted": true/false, "last_uptime": ..., "current_uptime": ..., "last_time": "YYYY-MM-DD HH:MM:SS"}`

Verify `last_time` (device reboot time) is between `start_time + expected_sec` and `start_time + max_sec`.

### track_low_power — Track full low power wakeup cycle

Full cycle: ignition off → device enters low power → device wakes up

1. Capture start_time: `date +%s` on device
2. Send `track_reboot_or_shutdown` to DTA with `track_off_max_sec` timeout
3. Wait for DTA connection to drop (device powered off) or timeout response
4. Track turn on: poll device until reachable within `track_on_max_sec`
5. Query `get_system_boot_uptime` from DTA after wake:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'get_system_boot_uptime':{}}).encode()); data=s.recv(4096); print(data.decode()); s.close()\""
```
6. Query `get_reboot_or_shutdown_status` to get shutdown time
7. Validate: device went into low power between `expected_sec` and `max_sec`
8. Validate: device turned on within `track_on_expected_sec` to `track_on_max_sec`

### track_turn_off — Verify device shuts down

Send `track_reboot_or_shutdown` and wait for DTA to stop responding (= device powered off):
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "python3 -c \"import socket,json; s=socket.socket(); s.settimeout(<MAX_SEC+2>); s.connect(('127.0.0.1',12347)); s.sendall(json.dumps({'track_reboot_or_shutdown':{'timeout':<MAX_SEC>}}).encode()); data=s.recv(4096); print(data.decode()); s.close()\""
```

If no response received within timeout → device shut down (PASS).
If response received with `device_rebooted: false` → device did NOT shut down (FAIL).

### track_turn_on — Wait for device to become reachable

After shutdown, poll device via serial until responsive:
```bash
# Loop with retries — device is expected to come back within max_sec
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "echo alive"
```

Record the time when device first responds. Calculate `turn_on_time = first_response_time - shutdown_time`.

### command_execute — Run command on device

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "<COMMAND>"
```

## CLI Usage

```bash
python3 claude_device_validator/src/device_controller.py --device-id <DEVICE_ID> --via-serial <ACTION> [ARGS]

# Examples:
python3 claude_device_validator/src/device_controller.py --device-id 103382300371 --via-serial keepalive_check --service bagheera
python3 claude_device_validator/src/device_controller.py --device-id 103382300371 --via-serial get_udid
python3 claude_device_validator/src/device_controller.py --device-id 103382300371 --via-serial track_reboot --expected-sec 300 --max-sec 600
python3 claude_device_validator/src/device_controller.py --device-id 103382300371 --via-serial ping_device
python3 claude_device_validator/src/device_controller.py --device-id 103382300371 --via-serial track_turn_off --max-sec 120
python3 claude_device_validator/src/device_controller.py --device-id 103382300371 --via-serial track_low_power --off-expected 60 --off-max 120 --on-expected 30 --on-max 180
```

## DTA JSON Message Reference

| Message | Purpose | Response |
|---------|---------|----------|
| `{"track_reboot_or_shutdown": {"timeout": N}}` | Start tracking reboot/shutdown for N seconds | None (connection drops on shutdown) or JSON status on timeout |
| `{"get_reboot_or_shutdown_status": {}}` | Get last reboot/shutdown info | `{"device_rebooted": bool, "last_uptime": N, "current_uptime": N, "last_time": "..."}` |
| `{"get_current_udid": {}}` | Get current UDID | `{"udid": N}` |
| `{"get_system_boot_uptime": {}}` | Get boot uptime in ms | `{"boot_uptime": N}` |

## Important Notes

- UDID is stored in `/home/ubuntu/.nddevice/gen_property.db` (table GENPROP, property "udid")
- UDID increments by exactly 1 on each reboot — use this to verify single reboot occurred
- DTA port is **12347** (same as send-msg-server skill)
- For serial devices: all DTA communication uses localhost TCP via Python one-liner on device
- Keepalive logs are in `/home/ubuntu/.nddevice/log/svc/log_*.log`
- After reboot, allow 30-60s for all services to start before checking keepalive
- `track_low_power` combines: track shutdown + wait for wakeup + verify timing of both
