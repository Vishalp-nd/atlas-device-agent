---
name: otacheck-service-validation
description: "Use when: validating otacheck service behavior from device logs. Covers per-minute polling cycle, counter increment, version check API call at multiples of 10, reboot-triggered OTA call, device-ID-modulo sleep, override config download, mandatory files post-reboot, stop-bagheera reboot trigger, internet stability, and RTC-time-mismatch handling."
argument-hint: "device ID (e.g., /otacheck-service-validation 103432407294)"
---

# otacheck — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the `otacheck` service —
> what it does, how its flows relate to each other, and which runtime conditions activate
> which code paths. The agent reads pytest test cases in `tests/otacheck/` for exact log
> patterns and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`otacheck` is a Python daemon that runs as a short-lived process every ~60 seconds (invoked
by a cron/timer). Each invocation creates a new PID, transitions `otacheck_state.txt` from
`DEFAULT_STATE → RUN_STATE → DEFAULT_STATE`, increments an internal counter, and optionally
calls the IDMS version-check API if one of three trigger conditions is true:
- `rebootTimeOta:True` — first invocation after a device reboot
- `countFromFile:True` — counter file value is a multiple of 10
- `uptimeOta:True` — uptime-based trigger

When an API call is triggered, the service sleeps `device_id % 60` seconds before calling
`wget` to POST to the versioncheck endpoint. After the call, it validates the override config
version and writes the result to `versioncheckresponse.txt`.

**Process name:** `otacheck` (new PID each invocation)
**Log file:** `/home/ubuntu/.nddevice/log/otacheck/`
**Log format:** `YYYY-MM-DD HH:MM:SS,mmm - __main__ - LEVEL - <message>`
**State files:**
- bagheera2/3: `/dev/shm/nd_files_c/otacheck_state.txt`, `otacheck_count.txt`, `otacheck.pid`
- krait/krait2/bagheera4: `/dev/shm/otacheck_state.txt`, `otacheck_count.txt`, `otacheck.pid`
**API endpoint:** `https://idms.netradyne.com/restserver/api/v1/versioncheck/<version>`
**Cloud response file:** `/home/ubuntu/.nddevice/cloud_response/versioncheckresponse.txt`

---

## Log Format

All otacheck log lines follow:
```
YYYY-MM-DD HH:MM:SS,mmm - __main__ - INFO - <message>
YYYY-MM-DD HH:MM:SS,mmm - __main__ - ERROR - <message>
```

There are no multi-tag prefixes like APM/audio services — all lines use `__main__` as the
logger name. ERROR lines are significant events (e.g., missing counter file, traceback).

---

## Service Flows

### Flow 1: Per-Minute Polling Cycle (every ~60s)

**What happens:** Every ~60 seconds a new `otacheck` process starts. It:
1. Creates/updates `otacheck.pid` with new PID
2. Reads `otacheck_state.txt` → gets `DEFAULT_STATE`
3. Writes `RUN_STATE` to `otacheck_state.txt`
4. Logs current wall clock time
5. Logs current firmware version and state
6. Enters `check_uptodate_from_cloud`
7. Logs counter value
8. Evaluates trigger conditions (`rebootTimeOta`, `countFromFile`, `uptimeOta`)
9. If no trigger: logs `callOta = False`, exits
10. Writes `DEFAULT_STATE` back to `otacheck_state.txt`
11. Logs `OTA Update Check Shutting down`

**When active:** Always
**Frequency:** Every ~60 seconds
**Cross-service impact:** None on normal cycle

**Key log patterns:**
```
::====================::OTA Update Check Starting::====================::
otacheck.pid file created: 0
File: /dev/shm/nd_files_c/otacheck.pid is modified to  state:<PID> otacheck
Got state '<prev_PID> DEFAULT_STATE'in file: /dev/shm/nd_files_c/otacheck_state.txt
File: /dev/shm/nd_files_c/otacheck_state.txt is modified to  state:<PID> RUN_STATE
Otacheck with time = b'<UTC_datetime>\n'
Environment ND_DEVICE_REL_PATH = /home/ubuntu/.nddevice and Current Version =<version> and state STABLE
deviceId = <ID> and Endpoint = https://idms.netradyne.com/restserver/api/v1/versioncheck
OTACHECK COUNTER VALUE = <N>
rebootTimeOta:False or countFromFile:False or uptimeOta:False
uptime value = <N>, callOta = False
CurrentVersion <version>, currentState =STABLE ::: UpgradeVersion =<version> UpgradeState =NONE package_type = OTA
File: /dev/shm/nd_files_c/otacheck_state.txt is modified to  state:<PID> DEFAULT_STATE
::====================::OTA Update Check Shutting down::====================::
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_38`        | `tests/otacheck/test_tc_38_otacheck_counter_increment.py`                 | Counter increments by +1 each cycle          | `DT-3938` |
| `TC_32`        | `tests/otacheck/test_tc_32_otacheck_check_mandatoryfiles_post_everyreboot.py` | State files exist post-reboot            | `DT-3938` |

---

### Flow 2: Counter Increment (per invocation)

**What happens:** Each invocation reads the counter from `otacheck_count.txt`, increments it
by 1, and logs `OTACHECK COUNTER VALUE = <N>`. If `otacheck_count.txt` is absent (e.g.,
first boot after a fresh reboot when `/dev/shm` is cleared), an ERROR traceback is logged:
`Exception: File /dev/shm/nd_files_c/otacheck_count.txt not present`. The missing file is
treated as `countFromFile:False` — not a fatal error.

**When active:** Always
**Frequency:** Every ~60 seconds

**Key log patterns:**
```
OTACHECK COUNTER VALUE = <N>
```
**On missing counter file (ERROR — non-fatal):**
```
ERROR - Traceback (most recent call last):
  File "otacheck.py", line 871, in otacheck.read_count_from_file
Exception: File /dev/shm/nd_files_c/otacheck_count.txt not present
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_38`        | `tests/otacheck/test_tc_38_otacheck_counter_increment.py`                 | Consecutive counter values differ by +1      | `DT-3938` |
| `TC_32`        | `tests/otacheck/test_tc_32_otacheck_check_mandatoryfiles_post_everyreboot.py` | `otacheck_count.txt` exists post-reboot  | `DT-3938` |

---

### Flow 3: Version Check API Call at Counter Multiple of 10

**What happens:** When the counter value is a multiple of 10 (10, 20, 30, ...), the service
sets `countFromFile:True` and triggers the API call. The counter file can be manually set to
10 to force trigger in tests.

**Trigger condition:** `counter_value % 10 == 0`
**When active:** When `otacheck_count.txt` contains a multiple of 10
**Frequency:** Every 10th invocation (~10 minutes under normal operation)

**Key log patterns:**
```
OTACHECK COUNTER VALUE = 10
rebootTimeOta:False or countFromFile:True or uptimeOta:False
uptime value = <N>, callOta = True
Sleeping for <N> secs
JWT response code = 1
JWT auth header = X-Device-JWT: <token>
auto_fw_download not present in BAGHEERA_OVERRIDE_CONFIG
Executing command: wget -O /home/ubuntu/.nddevice/cloud_response/versioncheckresponse.txt ...
verState = 0
Override config validation is disabled, skipping validation.
Override config is up to date.
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_29`        | `tests/otacheck/test_tc_29_otacheck_versioncheckapi_multiples_of_10.py`   | counter=10 → `countFromFile:True` → `callOta = True` | — |

---

### Flow 4: Reboot-Triggered OTA Call (`rebootTimeOta`)

**What happens:** On the first invocation after a device reboot, `rebootTimeOta:True` is set
and `callOta = True` is logged. The missing `otacheck_count.txt` file (cleared by `/dev/shm`
reset at boot) triggers a non-fatal ERROR traceback but does NOT block the API call.

**When active:** First ~1–3 invocations after every device reboot
**Frequency:** Once per reboot
**Cross-service impact:** Verifies connectivity is restored post-reboot

**Key log patterns:**
```
ERROR - Traceback (most recent call last):
  File "otacheck.py", line 871, in otacheck.read_count_from_file
Exception: File /dev/shm/nd_files_c/otacheck_count.txt not present
rebootTimeOta:True or countFromFile:False or uptimeOta:False
uptime value = <N>, callOta = True
Sleeping for <N> secs
JWT response code = 1
Executing command: wget -O ... versioncheck/<version>
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_28`        | `tests/otacheck/test_tc_28_otacheck_rebootcall_check.py`                  | After reboot: `rebootTimeOta:True` + `callOta = True` in logs | `DT-3938` |
| `TC_46`        | `tests/otacheck/test_tc_46_otacheck_stop_bagheera_service.py`             | Kill bagheera → svc triggers reboot → `rebootTimeOta:True` post-reboot | `BG4-658` |

---

### Flow 5: Device-ID-Modulo Sleep Before API Call

**What happens:** Before making the versioncheck API call, the service sleeps for
`device_id % 60` seconds. This staggers API calls across the fleet. For device
`103432407294`: `103432407294 % 60 = 54` → `Sleeping for 54 secs` (confirmed in device log).

**When active:** Every time `callOta = True`
**Frequency:** Same as Flows 3 and 4

**Key log patterns:**
```
uptime value = <N>, callOta = True
Sleeping for <N> secs
```

**Computing expected sleep from device ID:**
```python
expected_sleep = int(device_id) % 60
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_30`        | `tests/otacheck/test_tc_30_otacheck_sleep_based_on_deviceid_mod.py`       | `Sleeping for <device_id % 60>` logged after `callOta = True` | — |

---

### Flow 6: JWT Authentication + wget Version Check API

**What happens:** After the sleep, the service obtains a JWT token (`JWT response code = 1`
= success) and constructs a `wget` POST command to the IDMS versioncheck endpoint with headers
`X-DeviceType`, `X-Device-JWT`, `X-DeviceId`, `Content-Type`, and a JSON body containing
`module_name`, `device_id`, `config_version`, `config_override_version`, `deviceversion`,
`os_version`, `partition_id`, `format_version`, `support_7z`. The response is saved to
`versioncheckresponse.txt`. `verState = 0` means the current version is up to date.

**When active:** Every time `callOta = True`
**Frequency:** Same as Flows 3 and 4
**Cross-service impact:** IDMS cloud API; requires internet connectivity

**Key log patterns:**
```
JWT response code = 1
JWT auth header = X-Device-JWT: <eyJ...token...>
auto_fw_download not present in BAGHEERA_OVERRIDE_CONFIG
Executing command: wget -O /home/ubuntu/.nddevice/cloud_response/versioncheckresponse.txt --timeout=60 \
  --header="X-DeviceType: bagheera3" --header="X-Device-JWT: <token>" \
  --header="X-DeviceId: <device_id>" --header="Content-Type: application/json" \
  --post-data="{\"module_name\":\"CP\",\"device_id\":\"<ID>\",\"config_version\":\"0.0\",...}" \
  https://idms.netradyne.com/restserver/api/v1/versioncheck/<version>
verState = 0
Override config validation is disabled, skipping validation.
Override config is up to date.
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_73`        | `tests/otacheck/test_tc_73_otacheck_versioncheckapi_internet_stability_60min.py` | After internet disconnect+restore: JWT success + wget triggered | — |
| `TC_40`        | `tests/otacheck/test_tc_40_otacheck_devicenotifies_cloud_newversion.py`   | `CurrentVersion <version>` logged matches cloud ops-data version | `KRT2-700`, `BG4-565` |

---

### Flow 7: Override Config Download

**What happens:** When the override config version in `nddevice.ini` is cleared (blank),
otacheck detects the mismatch and re-downloads `bagheera_override.ini`. The log shows
`File sync finished: /home/ubuntu/config/bagheera_override.ini` (bagheera) or
`File sync finished: /data/nd_files/config/bagheera_over...` (krait), followed by
`Updated configuration in path /home/ubuntu/.nddevice/nddevice.ini`.

**Override config file paths by device type:**
| Device Type    | Override Config Path                                |
| -------------- | --------------------------------------------------- |
| krait / krait2 | `/data/nd_files/config/bagheera_override.ini`       |
| bagheera2/3/4  | `/home/ubuntu/config/bagheera_override.ini`         |

**nddevice.ini path:** `/home/ubuntu/.nddevice/nddevice.ini` (all device types)

**How to force re-download (test setup):** Clear the `version =` line:
```bash
# krait/krait2:
sed -i "s/^version.*=.*$/version = /" /home/ubuntu/.nddevice/nddevice.ini
# bagheera:
sudo sed -i "s/^version.*=.*$/version = /" /home/ubuntu/.nddevice/nddevice.ini
```
Then inject counter = 10 to trigger API call.

**When active:** When override config version in nddevice.ini is blank/stale
**Frequency:** Event-driven

**Key log patterns:**
```
File sync finished: /home/ubuntu/config/bagheera_override.ini
Updated configuration in path /home/ubuntu/.nddevice/nddevice.ini
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_216`       | `tests/otacheck/test_tc_216_otacheck_overrideconfigs_download.py`         | Override file synced, nddevice.ini updated, file exists on device | `BG4-565` |

---

### Flow 8: Version Notification to Cloud

**What happens:** Every invocation logs the current firmware version as:
`CurrentVersion <version>, currentState =STABLE ::: UpgradeVersion =<version> UpgradeState =NONE ...`
This is the log pattern the cloud uses to confirm what firmware version the device is running.

**When active:** Always (every invocation)
**Frequency:** Every ~60 seconds

**Key log patterns:**
```
CurrentVersion 5.6.14.rc.4, currentState =STABLE ::: UpgradeVersion =5.6.14.rc.4 UpgradeState =NONE package_type = OTA
```

**How to read current version on device:**
```bash
# bagheera:
grep -ih 'CurrentVersion ' /home/ubuntu/.nddevice/log/otacheck/* 2>/dev/null | tail -1 | sed -E 's/.*CurrentVersion[[:space:]]+([^[:space:]]+).*/\1/'
# krait:
grep -i '^version' /data/nd_files/config/package_manifest.ini | head -1 | cut -d= -f2 | tr -d ' \r\n'
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_40`        | `tests/otacheck/test_tc_40_otacheck_devicenotifies_cloud_newversion.py`   | `CurrentVersion <X>` in log matches cloud ops-data version | `KRT2-700`, `BG4-565` |

---

### Flow 9: Stop Bagheera → SVC Reboot → OTA Call

**What happens:** When bagheera is killed, the svc watchdog detects the keepalive timeout
and triggers a device reboot. After the reboot, `otacheck` runs and fires
`rebootTimeOta:True` → `callOta = True`. The total time from kill to OTA call is:
uptime_before_kill + reboot_duration + otacheck_first_invocation_time.

**When active:** When bagheera is killed or stopped
**Frequency:** Event-driven
**Cross-service impact:** bagheera → svc watchdog → device reboot → otacheck post-reboot call

**Key log patterns** (after reboot):
```
rebootTimeOta:True or countFromFile:False or uptimeOta:False
uptime value = <N>, callOta = True
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_46`        | `tests/otacheck/test_tc_46_otacheck_stop_bagheera_service.py`             | Kill bagheera → track reboot → `rebootTimeOta:True` + `callOta = True` | `BG4-658` |

---

### Flow 10: RTC Time Mismatch — Connection Failure

**What happens:** When the device clock is set to a future date (e.g., +1 year), the JWT/TLS
handshake fails because the certificate validity window does not match the device time.
otacheck logs `Failed to connect`. The test restores the clock via NTP
(`ntpd -q -p pool.ntp.org || hwclock -s`) and restarts `wifi_mgr`.

**When active:** When device system clock is significantly wrong
**Frequency:** Event-driven (test scenario / NTP outage edge case)
**Cross-service impact:** `wifi_mgr` restart needed to recover network state

**Key log patterns:**
```
OTACHECK COUNTER VALUE = 10
callOta = True
Failed to connect
```

**Clock manipulation commands (test setup):**
```bash
# krait/krait2:
date -s "$(date -d "+1 year" +"%Y-%m-%d %H:%M:%S")"
# bagheera:
sudo date -s "$(date -d "+1 year" +"%Y-%m-%d %H:%M:%S")"
```
**Clock restore commands:**
```bash
# krait/krait2:
ntpd -q -p pool.ntp.org 2>/dev/null || hwclock -s
# bagheera:
sudo ntpd -q -p pool.ntp.org 2>/dev/null || sudo hwclock -s
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_74`        | `tests/otacheck/test_tc_74_otacheck_rtctime_not_insync.py`                | Future-date clock → `Failed to connect` in otacheck logs | — |

---

### Flow 11: Internet Recovery — API Resumes

**What happens:** When the default route is removed to simulate internet outage, then
restored, otacheck successfully calls the API after recovery:
`JWT response code = 1` → `wget -O ... versioncheckresponse.txt`.

**Internet disconnect command (test setup):**
```bash
# krait/krait2:
ip route del default 2>/dev/null; sleep 2; ip route del default 2>/dev/null
# bagheera:
sudo ip route del default 2>/dev/null; sleep 2; sudo ip route del default 2>/dev/null
```

**When active:** After internet connectivity is restored
**Frequency:** Event-driven
**Cross-service impact:** `wifi_mgr` restart after route removal to recover state

**Key log patterns:**
```
JWT response code = 1
wget -O /home/ubuntu/.nddevice/cloud_response/versioncheckresponse.txt
versioncheckresponse.txt
```

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_73`        | `tests/otacheck/test_tc_73_otacheck_versioncheckapi_internet_stability_60min.py` | Disconnect + reconnect → JWT + wget succeed | — |

---

### Flow 12: Mandatory Files Post-Reboot

**What happens:** After every reboot, otacheck must recreate three state files in `/dev/shm`
(tmpfs — cleared at boot). The files appear within the first invocation (~60s after boot).
Their absence indicates otacheck failed to start.

**File paths by device type:**
| File                  | bagheera2/3              | krait/krait2/bagheera4   |
| --------------------- | ------------------------ | ------------------------- |
| `otacheck.pid`        | `/dev/shm/nd_files_c/`   | `/dev/shm/`               |
| `otacheck_state.txt`  | `/dev/shm/nd_files_c/`   | `/dev/shm/`               |
| `otacheck_count.txt`  | `/dev/shm/nd_files_c/`   | `/dev/shm/`               |

**When active:** After every reboot
**Frequency:** Once per reboot cycle

**Test cases that validate this flow:**
| Test Case ID   | pytest Path                                                               | What it checks                               | Related Bugs |
| -------------- | ------------------------------------------------------------------------- | -------------------------------------------- | --- |
| `TC_32`        | `tests/otacheck/test_tc_32_otacheck_check_mandatoryfiles_post_everyreboot.py` | All 3 state files exist post-reboot     | `DT-3938` |

---

## Config-Driven Flow Activation

otacheck has no user-configurable ini section. Behavior is determined by runtime state:

| Condition / File                   | Value / State                   | Activates Flow(s)                               | Test Cases             |
| ---------------------------------- | ------------------------------- | ----------------------------------------------- | ---------------------- |
| First invocation after reboot      | `rebootTimeOta = True`          | Flow 4: Reboot-Triggered OTA Call               | TC_28, TC_46           |
| `otacheck_count.txt` = multiple 10 | `countFromFile = True`          | Flow 3: Version Check at Counter Multiple of 10 | TC_29, TC_216, TC_30   |
| Counter file absent at boot        | ERROR traceback (non-fatal)     | Flow 2: Counter Increment (missing file case)   | TC_32                  |
| `device_id % 60`                   | sleep N secs before wget        | Flow 5: Device-ID-Modulo Sleep                  | TC_30                  |
| `nddevice.ini version =` (blank)   | Force override config re-sync   | Flow 7: Override Config Download                | TC_216                 |
| Internet unavailable               | `Failed to connect`             | Flow 10, 11                                     | TC_74, TC_73           |
| Bagheera killed                    | SVC reboot → rebootTimeOta      | Flow 9: Stop Bagheera → Reboot                  | TC_46                  |
| Clock set to future date           | TLS failure → Failed to connect | Flow 10: RTC Time Mismatch                      | TC_74                  |

**Trigger priority:** `rebootTimeOta > countFromFile > uptimeOta`

**Counter file injection commands (test setup):**
```bash
# bagheera2/3:
echo 10 | sudo tee /dev/shm/nd_files_c/otacheck_count.txt
# OR with chmod first:
sudo chmod 777 /dev/shm/nd_files_c/otacheck_count.txt; echo 10 > /dev/shm/nd_files_c/otacheck_count.txt
# krait/krait2/bagheera4:
echo 10 > /dev/shm/otacheck_count.txt
```

---

## Cross-Service Dependencies

| Related Service  | Why                                                                      | When to check its logs               |
| ---------------- | ------------------------------------------------------------------------ | ------------------------------------ |
| `svc`            | Detects bagheera keepalive timeout → triggers reboot (Flow 9)            | TC_46 reboot trigger verification    |
| `bagheera`       | Killing bagheera triggers svc watchdog reboot (Flow 9)                   | Flow 9 pre-condition                 |
| `wifi_mgr`       | Restart needed after `ip route del default` to restore network state     | TC_73, TC_74 postconditions          |
| IDMS cloud API   | `https://idms.netradyne.com/restserver/api/v1/versioncheck` — JWT POST   | Flows 3, 4, 6, 7, 11                 |
| `nddevice.ini`   | Source of override config version; cleared to force re-download          | Flow 7 (TC_216)                      |

---

## Flow Dependency Graph

```
boot → [Flow 12: Mandatory Files] — state files created in /dev/shm
     → [Flow 4: Reboot OTA Call] — rebootTimeOta:True on first invocation

every ~60s → [Flow 1: Polling Cycle]
           → [Flow 2: Counter Increment] — counter += 1
           → if counter % 10 == 0: [Flow 3: API Call at Multiple-of-10]
               → [Flow 5: Sleep device_id % 60]
               → [Flow 6: JWT + wget versioncheck]
               → [Flow 7: Override Config Sync] (if nddevice.ini version blank)
               → [Flow 8: CurrentVersion logged]

kill bagheera → svc timeout → reboot → [Flow 4: Reboot OTA Call]
                                      → [Flow 9: Stop Bagheera trigger]

internet down → [Flow 11: Internet Recovery] (after reconnect)
clock skewed  → [Flow 10: RTC Time Mismatch] → Failed to connect
```

---

## Validation Instructions for the Agent

1. **Identify device type** from `device_data/device_<ID>_config.ini` — state file paths differ
2. **Log file**: `/home/ubuntu/.nddevice/log/otacheck/` — may contain multiple rotated files; search all
3. **Timing**: log timestamps are wall-clock `YYYY-MM-DD HH:MM:SS` — use `since_ts` string comparisons
4. **For Flow 2 (counter increment)**: read at least 3 consecutive `OTACHECK COUNTER VALUE = N`
   entries; verify each differs by exactly +1; use `device.check_otacheck_counter()`
5. **For Flows 3/4 (API call trigger)**: look for `callOta = True` — must be preceded on the
   immediately prior line by either `countFromFile:True` or `rebootTimeOta:True`
6. **For Flow 5 (modulo sleep)**: extract device ID from `deviceId = <N> and Endpoint =...`,
   compute `N % 60`, verify `Sleeping for <result> secs` appears next
7. **For Flow 6 (JWT+wget)**: `JWT response code = 1` = success; `0` = auth failure
8. **For Flow 7 (override download)**: search for
   `File sync finished:.*bagheera_override` + `Updated configuration in path.*nddevice.ini`
9. **For Flow 8 (version notification)**: `CurrentVersion <X>` must match cloud ops-data value
   obtained via `device.ops_data_api()`
10. **For Flow 10 (RTC mismatch)**: `Failed to connect` must appear AFTER `callOta = True`
11. **For Flow 12 (mandatory files)**: `ls /dev/shm/nd_files_c/otacheck*` (bagheera) or
    `ls /dev/shm/otacheck*` (krait) — all 3 files must return exit code 0
12. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED / SKIPPED
