---
name: api-monitor
description: "Use when: checking frequency-based API calls from device logs, verifying event-based API calls, checking internet connectivity from device (ping 8.8.8.8 or DNS), checking if a network port is listening, or verifying early-slot-success.service content. Ported from nd_test_bot (6.14_changes branch) frequency_based_api.py, calculator_api.py, gps_lte_api.py."
argument-hint: "action and params (e.g., frequency_based_calls pattern service interval, event_based_api_call pattern service, internet_status_check, check_internet_exist, check_listening_port port, check_early_slot_service)"
---

# API Monitor

Monitor and verify device API calls, internet connectivity, network port access, and service configurations.

**Source**: Ported from `nd_test_bot` repo, branch `6.14_changes`:
- `frequency_based_api.py` — frequency_based_calls, event_based_api_call
- `calculator_api.py` — internet_status_check, check_listening_port_accessible, check_early_slot_service
- `gps_lte_api.py` — check_internet_exist

## Procedures

### frequency_based_calls — Monitor API call frequency in logs

Greps device logs for an API URL pattern, measures time between occurrences, and validates against expected interval.

```bash
# Grep for API pattern in service logs:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "grep -ria 'https://idms-staging.netradyne.com/restserver<API_PATTERN>' /home/ubuntu/.nddevice/log/<SERVICE_NAME> | sort | tail -1"

# Get device epoch for timing:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "date +%s"
```

**Logic**: 
1. Grep logs for the API pattern
2. Extract timestamp from the log line
3. Calculate time since last call
4. Compare against `expected_interval` (tolerance ±30s typically)
5. Optionally verify via cloud API (check `api_key` against IDMS)
6. Sleep for remaining interval, repeat

**PASS**: API calls occur at expected frequency.
**FAIL**: Calls missing or interval outside tolerance.

Used by: TC-1, TC-8, TC-9, TC-10

### event_based_api_call — Verify single API call in logs

Single-shot version — check that a specific API was called (not periodic):

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "grep -riwa '<API_PATTERN>' /home/ubuntu/.nddevice/log/<SERVICE_NAME> | sort | tail -1"
```

**Logic**:
1. Grep for the API pattern
2. Extract timestamp
3. Optionally check cloud delay ≤40s

**PASS**: API call found in logs. **FAIL**: Not found.

Used by: TC-11, TC-14, TC-295, TC-303

### internet_status_check — Ping 8.8.8.8

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ping -c 1 8.8.8.8"
```

Retries `loop_count` times (60s apart). If `reconnect=True`, restarts `wifi_mgr` before retrying:
```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl restart wifi_mgr"
```

**PASS**: Ping succeeds (exit 0). **FAIL**: All attempts fail.

Used by: TC-117, TC-1716

### check_internet_exist — Ping DNS servers (platform-aware)

More thorough connectivity check using multiple DNS servers:

```bash
# krait/krait2:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "busybox ping6 -I eth0 -c 3 -w 1 2001:4860:4860::8888"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "busybox ping6 -I eth0 -c 3 -w 1 2001:4860:4860::8844"

# bagheera/octo:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ping -I eth1 -c 3 -w 1 8.8.8.8"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ping -I eth1 -c 3 -w 1 2001:4860:4860::8888"
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "ping -I eth1 -c 3 -w 1 1.1.1.1"
```

**DNS servers checked**:
- krait: `2001:4860:4860::8888`, `2001:4860:4860::8844`, `2001:4860:4860::6464`, `2001:4860:4860::64`
- bagheera/octo: `8.8.8.8`, `2001:4860:4860::8888`, `1.1.1.1`, `2001:4860:4860::8844`, `8.8.4.4`, `2001:4860:4860::6464`

**PASS**: At least one DNS responds. **FAIL**: All pings fail.

Used by: TC-8, TC-9, TC-10, TC-96, TC-97

### check_listening_port_accessible — Verify port is listening

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "netstat -tulpn | grep LISTEN"
```

Parse output for a line matching:
- Local address: `0.0.0.0:<PORT>`
- Foreign address: `0.0.0.0:*`

**PASS**: Port is listening on all interfaces. **FAIL**: Port not found or not on `0.0.0.0`.

Used by: TC-3294

### check_early_slot_service — Verify systemd service file content

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "cat /etc/systemd/system/early-slot-success.service"
```

Compare output against expected service unit file content (hardcoded expected content in source).

**PASS**: Content matches expected. **FAIL**: Content differs.

Used by: TC-3294

### get_inference_summary — Get inference summary from device

```bash
# Default command:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "<COMMAND>"
```

Runs a provided command on device, parses output for inference stats. If no command provided, uses device-type-specific default.

Used by: TC-1805 (not in SANITY scope but referenced)

## Connection Methods

```bash
# D450/bagheera (serial):
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "<COMMAND>"

# D210/krait (ADB):
adb -s <ADB_SERIAL> shell "<COMMAND>"

# D470/octo (ADB):
adb -s <ADB_SERIAL> shell "<COMMAND>"
```
