---
name: service-controller
description: "Use when: killing/stopping a service, restarting a service, starting a service, checking service status or inactive state, getting system uptime, comparing uptime difference between system and services, checking CPU affinity for all services. Ported from nd_test_bot (6.14_changes branch) service_controller.py."
argument-hint: "action and params (e.g., kill_service ndcentral, restart_service bagheera, start_service apm, service_status all, service_inactive ext_cam, system_uptime, uptime_difference, cpu_affinity krait)"
---

# Service Controller

Manage and inspect device services: kill/stop, restart, start, check status/inactive, get system uptime, compare service start times against boot, and verify CPU affinity settings.

**Source**: Ported from `nd_test_bot` repo, branch `6.14_changes`, file `Test_Automation_Framework/Lib/apis/service_controller.py`.

## When to Use

- **kill_service**: Stop a running service (TC-295, TC-300, TC-303, TC-304)
- **restart_service**: Restart one or more services and verify they came back active (TC-101, TC-110, TC-111, TC-1413, etc.)
- **start_service**: Start a stopped service (TC-295, TC-300)
- **service_inactive**: Verify service(s) are inactive (TC-305, TC-768)
- **service_status**: Check if services are active/inactive (TC-185, TC-1330, TC-1684, TC-1804, TC-1790, TC-1797)
- **system_uptime**: Get device uptime in minutes or seconds (TC-1634, TC-1641)
- **get_uptime_minutes**: Get device uptime in minutes from `uptime` command (TC-295)
- **uptime_difference**: Compare system boot time vs service start time — fail if any >45s (TC-338)
- **cpu_affinity**: Check all services CPU pinning matches expected JSON (TC-2543, TC-3047)

## Procedures

### kill_service — Stop a service

```bash
# Check if service is active first
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl is-active <SERVICE_NAME>"
# If active, stop it
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "sudo systemctl stop <SERVICE_NAME>"
# Wait 2s, then verify stopped
sleep 2
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl is-active <SERVICE_NAME>"
```

**PASS**: Service transitions from active to inactive.
**FAIL**: Service was already inactive, or stop command failed.

### restart_service — Restart service(s)

```bash
# Restart one or more services
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "sudo systemctl restart <SERVICE_NAME>"
# Wait 30 seconds for service to stabilize
sleep 30
# Verify service is active
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl is-active <SERVICE_NAME>"
# Verify uptime is < 60s (confirms it actually restarted)
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl show -p ActiveEnterTimestamp <SERVICE_NAME>"
```

For multiple services, pass space-separated: `sudo systemctl restart svc bagheera power_monitor`

**PASS**: All services are active AND uptime < 60s after restart.
**FAIL**: Any service is not active, or uptime >= 60s (means it didn't actually restart).

### start_service — Start a stopped service

```bash
# Get PID before start (should be empty/0 if inactive)
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl show -p MainPID --value <SERVICE_NAME>"
# Start the service
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "sudo systemctl start <SERVICE_NAME>"
# Verify it's now active
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl is-active <SERVICE_NAME>"
```

**PASS**: Service transitions from inactive to active.
**FAIL**: Service was already active, or start failed.

### service_inactive — Verify service(s) are inactive

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl is-active <SERVICE_NAME>"
# Expected output: "inactive"
```

For multiple services, check each one.

**PASS**: All specified services are `inactive`.
**FAIL**: Any service is `active` or `activating`.

### service_status — Check service(s) active state

```bash
# Single service:
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl is-active <SERVICE_NAME>"

# All services — loop through the service list for device type:
for svc in <SERVICE_LIST>; do
  python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl is-active $svc"
done
```

**Service lists by device type:**

| Device Type | Services |
|-------------|----------|
| krait/krait2 | analytics, audioPlayback, awsiot, bagheera, nd_bt, circular_buffer, conn_mgr, diagnostic, HealthStatsManager, onetime_service, fan_control, free_cache, apm, installer_app, unifiedAnalyticsClient, obd, scheduler_manager, outwardAnalyticsClient, power_monitor, service_mon, speed, svc, time_sync, uploader, wifi_mgr, onetimereboot, ext_cam, nd_sam |
| bagheera2 | analyticsService, audioPlayback, cam_rec, awsiot, bagheera, nd_bt, circular_buffer, conn_mgr, diagnostic, ext_cam, nd_sam, HealthStatsManager, installer_app, unifiedAnalyticsClient, obd, outwardAnalyticsClient, power_monitor, service_mon, speed, svc, time_sync, uploader, wifi_mgr, scheduler_manager, nd_shutdown, nd_suspendresume |
| bagheera3 | Same as bagheera2 + apm, nd_reboot, nd_app_reboot |
| octo | analyticsService, audioPlayback, cam_rec, awsiot, bagheera, nd_bt, circular_buffer, conn_mgr, diagnostic, ext_cam, nd_sam, HealthStatsManager, installer_app, unifiedAnalyticsClient, obd, outwardAnalyticsClient, power_monitor, service_mon, speed, svc, time_sync, uploader, wifi_mgr, scheduler_manager, nd_shutdown, nd_suspendresume, apm, nd_app_reboot |

**Known inactive by default:**
- bagheera2: `apm`
- bagheera3: none specifically
- krait/krait2: `fan_control`, `onetimereboot`
- octo: none specifically

**PASS**: All expected-active services are active.
**FAIL**: Any expected-active service is inactive.

### system_uptime — Get device uptime

```bash
# Get uptime in seconds from /proc/uptime
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "cat /proc/uptime | awk '{print \$1}'"
# Get device time
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "date -u '+%Y-%m-%d %H:%M:%S'"
```

**Returns**: `(status, system_start_time, uptime_value)` — uptime in minutes (default) or seconds.

### get_uptime_minutes — Get uptime in minutes (simple)

```bash
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "uptime | awk -F'(up |,  *[0-9]+ user)' '{print \$2}' | awk '{if (\$2==\"min\") print \$1; else if (index(\$1,\":\")>0) {split(\$1,a,\":\"); print a[1]*60+a[2]} else if (\$2==\"days,\" || \$2==\"day,\") print \$1*1440; else print \$1*60}'"
```

**Returns**: integer minutes.

### uptime_difference — Compare boot time vs service start times

For each service, get its start timestamp and compare against system boot time:

```bash
# System uptime
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "cat /proc/uptime | awk '{print \$1}'"
# Device time
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "date -u '+%Y-%m-%d %H:%M:%S'"
# Per-service start time
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl show -p ActiveEnterTimestamp <SERVICE> | cut -d' ' -f2-3"
```

Calculate: `service_start_time - system_boot_time = delta_seconds`.

**PASS**: All services started within 45 seconds of boot.
**FAIL**: Any service started >45s after boot or has negative delta.

### cpu_affinity — Check CPU pinning for all services

```bash
# Get PID for each service
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "systemctl show -p MainPID --value <SERVICE>"
# Check CPU affinity
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "taskset -cp <PID> | awk -F': ' '{print \$2}'"
```

Compare against expected values from JSON:
- Krait: `cpu_check_affinities_krait.json`
- Bagheera: `cpu_check_affinities_bagheera.json`
- Bagheera2: `cpu_check_affinities_bagheera2.json`

**PASS**: All services match expected CPU affinity.
**FAIL**: Any mismatch.

## Path Mappings

All commands use `serial_conn.py` for D450/bagheera or `adb shell` for D210/krait:

```bash
# D450/bagheera (serial):
python3 claude_device_validator/src/serial_conn.py --device-id <DEVICE_ID> "<COMMAND>"

# D210/krait (ADB):
adb -s <ADB_SERIAL> shell "<COMMAND>"
```
