---
name: apm-service-validation
description: "Use when: validating APM (apm) service behavior from device logs. Covers initialization, keepalive monitoring, voltage/supercap status, ignition tracking, WOM/IMU/GPS sensor aggregation, config parsing, crash recovery, and keepalive timeout reboot."
argument-hint: "device ID (e.g., /apm-service-validation 103432407294)"
---

# APM (`apm`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the APM service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads pytest test cases in `tests/apm/` for actual log
> patterns, device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`apm` (Advanced Power Management) is a critical background service responsible for monitoring
the device's power state, sensors, and ignition signal. It aggregates status from IMU, GPS,
ignition (IGNS), supercapacitor (SC), CAN, and crank subsystems, publishing a combined
`aggregate_status` every ~2 seconds. The service runs a keepalive monitor thread every ~30
seconds that reports health to the SVC watchdog (`SVC_U`). If the keepalive is not received
within the configured timeout, `SVC_U` triggers a device reboot. APM is **not supported on
bagheera2** — all test cases must skip on that device type.

**Process name:** `apm`
**Log file:** `apm.log` (path: `/home/ubuntu/.nddevice/log/apm/`)
**Log tag prefix:** `APM:`, `A_PWR_VOLT:`, `A_IGNS:`, `A_SUPERCAP:`, `SVC_U:`
**Primary config sections:** `[apm]`, `[apm_wom]`

---

## Log Format

APM log lines follow this format:
```
<epoch_ms>: <uptime_ms>: <TAG>: <level>: <pid>: <tid>: <message>
```

Key tags observed in device logs:
- `APM:` — main service logic (keepalive, status aggregation, socket mgmt)
- `A_PWR_VOLT:` — voltage monitor (every ~60s): `VOLT: C: X.XXX, I: X.XXX, is24V: false, BadBattery: false, FusionActive: true`
- `A_IGNS:` — ignition state changes: `IGN: ON/OFF Prev: ON/OFF Old(N): ON/OFF V: X.XXX`
- `A_SUPERCAP:` — supercapacitor state (every ~60s): `PFI: N V: X.XXX Prev: PFI: N V: X.XXX`
- `SVC_U:` — SVC util keepalive report: `all_thread_keepalive_status from util m.status: 0`

---

## Service Flows

### Flow 1: Thread Keepalive Monitor (~30s interval)

**What happens:** Every ~30 seconds, the APM main loop calls `start_monitor_fn` and reports
`all_thread_keepalive_status: 0` to the SVC watchdog utility (`SVC_U`). A status of `0`
means all threads are healthy. If the keepalive is not kicked within the configured timeout,
`SVC_U` triggers a system reboot.

**When active:** Always (regardless of config)
**Frequency:** Every ~30 seconds
**Cross-service impact:** `SVC_U` watchdog; failure causes system reboot

**Key log patterns:**
```
APM: ... start_monitor_fn all_thread_keepalive_status:  0
SVC_U: ...  all_thread_keepalive_status from util  m.status: 0
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                      | Related Bugs |
| ------------------ | -------------------------------------------------------------- | --------------------------------------------------- | --- |
| `TC_apm_1584`      | `tests/apm/test_tc_apm_1584_all_thread_keep_alive_check_30sec.py` | Average keepalive interval is 25000–35000 ms     | `DT-3723`, `DT-3950`, `DT-3693` |
| `TC_apm_303`       | `tests/apm/test_tc_apm_303_keepalive_timeout.py`               | Keepalive timeout triggers SVC reboot               | `DT-3723`, `DT-3457`, `BG4-626` |

---

### Flow 2: Aggregate Status Polling (~2s interval)

**What happens:** Every ~2 seconds, APM polls all sensor subsystems (IMU, GPS, IGNS, SC, CAN,
crank) and logs a combined status line. `igns_status = 2` means ignition ON; `sc_status = 2`
means supercap present; `aggregate_status = 1` means at least one source is active.
The line also logs transition/notification events with `mask` and `state` values.

**When active:** Always
**Frequency:** Every ~2 seconds
**Cross-service impact:** power_monitor, keep_alive_manager

**Key log patterns:**
```
APM: ... notification received or in transition state, ready 0, mask = 0x21, state = 1
APM: ... imu_status = 0, gps_status = 0, igns_status = 2, sc_status = 2, can_status = 0, crank_status = 0, aggregate_status = 1
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                      | Related Bugs |
| ------------------ | -------------------------------------------------------------- | --------------------------------------------------- | --- |
| `TC_apm_3328`      | `tests/apm/test_tc_apm_3328_verify_ignition_status.py`         | igns_status reflects actual ignition state          | `DT-3748`, `DT-3361`, `DT-3806`, `DT-3557`, `DT-3909` |
| `TC_apm_3338`      | `tests/apm/test_tc_apm_3338_status_update_every_60_sec.py`     | A_PWR_VOLT status updates every ~60s                | — |
| `TC_apm_1585`      | `tests/apm/test_tc_apm_1585_ign_toggle_off.py`                 | Ignition OFF event reflected in status              | `DT-3773`, `DT-3361`, `DT-3476`, `DT-3557`, `DT-3909`, `DT-3483` |
| `TC_apm_1586`      | `tests/apm/test_tc_apm_1586_ign_toggle_on.py`                  | Ignition ON event reflected in status               | `DT-3773`, `DT-3361`, `DT-3486`, `DT-3143` |

---

### Flow 3: Voltage & Power Monitoring (~60s interval)

**What happens:** The `A_PWR_VOLT` thread reads current (C) and instantaneous (I) voltage
from the power rail every ~60 seconds. It reports `is24V`, `BadBattery`, and `FusionActive`
flags. The `S:` state line shows ON/OFF counts and event counters per session.
Initial voltage is also read at boot (krait/krait2 only via `/sys/kernel/krait/shdn_stat`).

**When active:** Always (voltage monitor), krait/krait2 only for initial UART voltage read
**Frequency:** Every ~60 seconds
**Cross-service impact:** Reported to health stats; `BadBattery: true` can block session start

**Key log patterns:**
```
A_PWR_VOLT: ... VOLT: C: 14.606, I: 14.939, is24V: false, BadBattery: false, FusionActive: true
A_PWR_VOLT: ... S: ON(2), IDX: 0, DIDX 59, DC - OFF 0, ON 0, IDLE 1800, ...
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                        | What it checks                                   | Related Bugs |
| ------------------ | ------------------------------------------------------------------ | ------------------------------------------------ | --- |
| `TC_apm_3318`      | `tests/apm/test_tc_apm_3318_check_initial_voltage_and_update_file.py` | Initial voltage from UART (krait/krait2 only) | `DT-3371`, `DT-3241` |
| `TC_apm_3338`      | `tests/apm/test_tc_apm_3338_status_update_every_60_sec.py`         | Voltage update interval ~60s                     | — |
| `TC_apm_3403`      | `tests/apm/test_tc_apm_3403_class_based_threshold_validations.py`  | Voltage threshold classification                 | `DT-3286`, `DT-3241` |

---

### Flow 4: Supercapacitor Status Monitoring (~60s interval)

**What happens:** The `A_SUPERCAP` thread monitors the supercapacitor voltage every ~60 seconds.
It logs the current PFI state, voltage (V), previous voltage, and timing stats (DC/PDC counters,
event counts per session). `PFI: 1` means supercap is present and charged.

**When active:** Only when `apm_supercap_enable = true`
**Frequency:** Every ~60 seconds
**Cross-service impact:** Low voltage supercap can affect session recording

**Key log patterns:**
```
A_SUPERCAP: ... PFI: 1 V: 14.308 Prev: PFI: 1 V: 14.390 Old(0): PFI: 1 V: 14.565 Time: 0ms Volt: 0.000V Backup: 0s
A_SUPERCAP: ... S: OFF(0), IDX: 0, DIDX 59, DC - OFF 24, ON 0, EVNT 1, ...
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                   | Related Bugs |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------ | --- |
| `TC_apm_1633`      | `tests/apm/test_tc_apm_1633_wom_gps_imu_enable_master.py`     | Supercap register check when enabled             | `DT-4080`, `BG4-846`, `DT-3710`, `DT-3483`, `DT-3444`, `DT-3301`, `DT-3884` |

---

### Flow 5: Ignition State Tracking

**What happens:** The `A_IGNS` thread tracks ignition ON/OFF transitions. Each transition logs
the current state, previous state, and voltage at the time of the event. The `RC`/`GC` counters
track rising/ground count per session. `IGN: ON Prev: ON` indicates steady ignition-on state.

**When active:** Always (`apm_igns_enable = true` by default)
**Frequency:** On ignition state change event
**Cross-service impact:** Ignition state controls session start/stop in cam_rec, nd-central

**Key log patterns:**
```
A_IGNS: ... IGN: ON Prev: ON Old(0): ON V: 14.339
A_IGNS: ... S: ON(2), IDX: 0, DIDX 59, DC - OFF 0, ON 0, EVNT 0, ...
A_IGNS: ... IGN: OFF Prev: ON ...
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                   | Related Bugs |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------ | --- |
| `TC_apm_1585`      | `tests/apm/test_tc_apm_1585_ign_toggle_off.py`                 | Ignition OFF transition logged                   | `DT-3773`, `DT-3361`, `DT-3476`, `DT-3557`, `DT-3909`, `DT-3483` |
| `TC_apm_1586`      | `tests/apm/test_tc_apm_1586_ign_toggle_on.py`                  | Ignition ON transition logged                    | `DT-3773`, `DT-3361`, `DT-3486`, `DT-3143` |
| `TC_apm_3328`      | `tests/apm/test_tc_apm_3328_verify_ignition_status.py`         | igns_status in aggregate matches physical state  | `DT-3748`, `DT-3361`, `DT-3806`, `DT-3557`, `DT-3909` |
| `TC_apm_3335`      | `tests/apm/test_tc_apm_3335_ignition_on_off_wom_gps_disabled.py` | Ignition behavior when WOM+GPS disabled       | `DT-3272`, `DT-3668` |

---

### Flow 6: Config Parsing & Override

**What happens:** At startup, APM reads its configuration from `bagheera_config.ini` (base) and
`bagheera_override.ini` (override). Key config keys control which subsystems are enabled.
Override parsing is validated separately to ensure runtime config changes take effect after
reboot.

**When active:** At boot / after config upload + reboot
**Frequency:** Once per boot
**Cross-service impact:** Config changes affect all sensor-gated flows

**Key log patterns** (from tests, not observed in steady-state log):
```
APM: ... config parsed: apm_wom_enable=true
APM: ... config parsed: apm_imu_enable=true
APM: ... config parsed: apm_gps_enable=true
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                   | Related Bugs |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------ | --- |
| `TC_apm_1608`      | `tests/apm/test_tc_apm_1608_config_parsing.py`                 | All base config keys parsed correctly            | `DT-3668`, `DT-3475`, `DT-3023` |
| `TC_apm_1629`      | `tests/apm/test_tc_apm_1629_override_parsing.py`               | Override config keys take effect                 | `DT-3301`, `DT-3475` |
| `TC_apm_1633`      | `tests/apm/test_tc_apm_1633_wom_gps_imu_enable_master.py`     | All features enabled via override + reboot       | `DT-4080`, `BG4-846`, `DT-3710`, `DT-3483`, `DT-3444`, `DT-3301`, `DT-3884` |
| `TC_apm_3616`      | `tests/apm/test_tc_apm_3616_low_power_count_when_wom_disabled.py` | All features disabled via override           | `DT-3669`, `DT-3668`, `DT-3710`, `DT-3882`, `DT-3245`, `DT-3022`, `BG4-580`, `DT-3884` |

---

### Flow 7: WOM / IMU / GPS Sensor Aggregation

**What happens:** When `apm_wom_enable`, `apm_imu_enable`, or `apm_gps_enable` are enabled,
APM opens sockets for IMU and GPS data streams. IMU data provides motion detection (WOM thresholds:
`apm_wom_x_thr`, `apm_wom_y_thr`, `apm_wom_z_thr`). GPS data is polled every ~10 seconds.
When all sensors report no motion (`imu_status = 0, gps_status = 0`), the device may enter
low-power state.

**When active:** Only when respective `apm_*_enable` config keys are `true`
**Frequency:** IMU: event-driven; GPS: every ~10s
**Cross-service impact:** Wakeup decisions; session start inhibition in idle state

**Key log patterns:**
```
APM: ... imu_status = 0, gps_status = 0, igns_status = 2, sc_status = 2, ...
APM: ... IMU socket created successfully
APM: ... GPS socket created successfully
APM: ... WOM threshold triggered
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                   | Related Bugs |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------ | --- |
| `TC_apm_1633`      | `tests/apm/test_tc_apm_1633_wom_gps_imu_enable_master.py`     | All sensors enabled + GPS 10s poll + supercap    | `DT-4080`, `BG4-846`, `DT-3710`, `DT-3483`, `DT-3444`, `DT-3301`, `DT-3884` |
| `TC_apm_1634`      | `tests/apm/test_tc_apm_1634_imu_gps_socket_creation_check.py` | IMU and GPS sockets created on boot              | `DT-4080`, `DT-3485` |
| `TC_apm_3335`      | `tests/apm/test_tc_apm_3335_ignition_on_off_wom_gps_disabled.py` | Behavior when WOM+GPS disabled              | `DT-3272`, `DT-3668` |
| `TC_apm_3336`      | `tests/apm/test_tc_apm_3336_verify_behaviour_no_gps.py`        | Correct behavior when GPS unavailable            | — |
| `TC_apm_3616`      | `tests/apm/test_tc_apm_3616_low_power_count_when_wom_disabled.py` | Low power count when all sensors off        | `DT-3669`, `DT-3668`, `DT-3710`, `DT-3882`, `DT-3245`, `DT-3022`, `BG4-580`, `DT-3884` |
| `TC_apm_2793`      | `tests/apm/test_tc_apm_2793_checks_multiple_wakeup_logs.py`    | Multiple wakeup events logged correctly          | `DT-4080`, `OCTO-2151`, `DT-3710`, `DT-3882` |

---

### Flow 8: Crash Recovery & Service Restart

**What happens:** If APM crashes (SIGABRT/SIGKILL), service_mon automatically restarts it.
The new process gets a fresh PID and logs a new `ActiveEnterTimestamp`. The `APM:` keepalive
resumes within ~30 seconds. A crash does NOT trigger a system reboot on its own — only a
keepalive timeout does.

**When active:** On crash or explicit SIGKILL
**Frequency:** Event-driven
**Cross-service impact:** service_mon (APM label); SVC_U keepalive gap during restart

**Key log patterns** (in service_mon log, not apm.log):
```
service_mon: ... Service error: APM :
service_mon: ... Service started: APM :
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                   | Related Bugs |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------ | --- |
| `TC_apm_3323`      | `tests/apm/test_tc_apm_3323_check_behaviour_when_apm_crash.py` | APM auto-restarts after kill, new PID            | `DT-4251`, `DT-3685`, `DT-3272`, `DT-3143` |
| `TC_apm_1602`      | `tests/apm/test_tc_apm_1602_service_status.py`                 | Service is active, binary exists, permissions ok | `DT-4251`, `DT-3685` |

---

### Flow 9: Keepalive Timeout → Reboot

**What happens:** If APM is killed and does NOT restart fast enough, the SVC watchdog
(`SVC_U`) detects the keepalive gap and triggers a full device reboot. This is distinct
from crash recovery (Flow 8) — this flow validates the safety net when service_mon also fails
to restart APM within the timeout window.

**When active:** When APM is killed AND keepalive timeout is reached
**Frequency:** Event-driven (negative scenario)
**Cross-service impact:** Full device reboot; all services restart

**Key log patterns** (in svc/service_mon logs):
```
SVC_U: ... keepalive timeout for APM
svc: ... initiating reboot due to keepalive timeout
```

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                              | What it checks                              | Related Bugs |
| ------------------ | -------------------------------------------------------- | ------------------------------------------- | --- |
| `TC_apm_303`       | `tests/apm/test_tc_apm_303_keepalive_timeout.py`         | Device reboots after APM keepalive timeout  | `DT-3723`, `DT-3457`, `BG4-626` |

---

### Flow 10: Reboot Master & Multiple Wakeup

**What happens:** After a device reboot triggered by APM (or by external request via APM),
the service initializes fresh and logs multiple wakeup events. `TC_apm_1632` (reboot master)
verifies the full reboot-and-restart cycle; `TC_apm_2793` verifies wakeup logs appear
correctly after multiple ignition toggle cycles.

**When active:** After reboot events
**Frequency:** Once per reboot
**Cross-service impact:** All services restart; session IDs reset

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                   | Related Bugs |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------ | --- |
| `TC_apm_1632`      | `tests/apm/test_tc_apm_1632_reboot_master.py`                  | Reboot cycle completes, APM restarts cleanly     | — |
| `TC_apm_2793`      | `tests/apm/test_tc_apm_2793_checks_multiple_wakeup_logs.py`    | Wakeup logs present after multiple ignition cycles | `DT-4080`, `OCTO-2151`, `DT-3710`, `DT-3882` |

---

### Flow 11: Time Jump Handling

**What happens:** When the device clock jumps (NTP sync, timezone change, or manual
adjustment), APM must handle the timestamp discontinuity gracefully without crashing or
emitting bogus keepalive intervals. `TC_apm_1203` validates that the service continues
normal operation after a time jump.

**When active:** When system clock jumps
**Frequency:** Event-driven
**Cross-service impact:** Log timestamps become discontinuous; interval calculations must be relative

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                   | Related Bugs |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------ | --- |
| `TC_apm_1203`      | `tests/apm/test_tc_apm_1203_service_working_when_timejump.py`  | APM continues working after system clock jump    | — |

---

### Flow 12: No-Internet & Camera-Crash Behavior

**What happens:** APM must continue operating normally even when cloud connectivity is lost
or when the camera service crashes. These are independent services — APM should not be
blocked by either condition.

**Test cases that validate this flow:**
| Test Case ID       | pytest Path                                                    | What it checks                                   | Related Bugs |
| ------------------ | -------------------------------------------------------------- | ------------------------------------------------ | --- |
| `TC_apm_3334`      | `tests/apm/test_tc_apm_3334_check_behaviour_when_no_internet.py` | APM keepalive continues with no internet      | — |
| `TC_apm_3337`      | `tests/apm/test_tc_apm_3337_verify_ignition_behaviour_camera_crash.py` | Ignition behavior after camera crash    | — |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key                    | Value         | Activates Flow(s)                      | Test Cases Affected                         |
| -------------- | ----------------------------- | ------------- | -------------------------------------- | ------------------------------------------- |
| `[apm]`        | `apm_wom_enable`              | `true` / `1`  | WOM/IMU/GPS Sensor Aggregation         | `TC_apm_1633`, `TC_apm_1634`, `TC_apm_2793` |
| `[apm]`        | `apm_imu_enable`              | `true` / `1`  | WOM/IMU/GPS Sensor Aggregation         | `TC_apm_1634`, `TC_apm_3335`               |
| `[apm]`        | `apm_gps_enable`              | `true` / `1`  | WOM/IMU/GPS Sensor Aggregation (GPS)   | `TC_apm_1633`, `TC_apm_3336`               |
| `[apm]`        | `apm_supercap_enable`         | `true` / `1`  | Supercapacitor Status Monitoring       | `TC_apm_1633`                              |
| `[apm]`        | `apm_igns_enable`             | `true` / `1`  | Ignition State Tracking                | `TC_apm_1585`, `TC_apm_1586`, `TC_apm_3328`|
| `[apm]`        | `apm_motion_detection`        | `true` / `1`  | WOM/IMU sensor motion threshold        | `TC_apm_1608`, `TC_apm_3403`               |
| `[apm]`        | `apm_can_enable`              | `false`       | CAN disabled (default) — skip CAN TCs | —                                          |
| —              | —                             | —             | Keepalive Monitor (always active)      | `TC_apm_1584`, `TC_apm_303`                |
| —              | —                             | —             | Aggregate Status Poll (always active)  | `TC_apm_3338`, `TC_apm_3328`               |
| —              | —                             | —             | Voltage Monitor (always active)        | `TC_apm_3318`, `TC_apm_3338`               |

**Default values** (when key is absent from config):
- `apm_wom_enable` → `false`
- `apm_imu_enable` → `false`
- `apm_gps_enable` → `false`
- `apm_supercap_enable` → `false`
- `apm_igns_enable` → `true`
- `apm_motion_detection` → `false`
- `apm_can_enable` → `false`

**Rules:**
- Skip ALL apm test cases if `device_type == "bagheera2"`
- Flows marked "always active" → run their test cases unconditionally (on non-bagheera2)
- Flows gated by a config key → run only if that key is set to the activating value
- Config values in `device_list_config.csv` take precedence if present

---

## Cross-Service Dependencies

| Related Service    | Why                                                                   | When to check its logs              |
| ------------------ | --------------------------------------------------------------------- | ----------------------------------- |
| `service_mon`      | Reports `Service started/error/stopped: APM :` on crash/restart      | Flow 8 (crash recovery)             |
| `svc`              | `SVC_U` keepalive watchdog — triggers reboot on timeout              | Flow 9 (keepalive timeout)          |
| `power_monitor`    | Sends ignition state to APM via IPC                                   | Flow 5 (ignition tracking)          |
| `keep_alive_manager` | Consumes aggregate_status for wakeup decisions                      | Flow 7 (WOM/IMU/GPS aggregation)    |
| `nd-central`       | Receives ignition ON/OFF events from APM for session management       | Flow 5 (ignition tracking)          |
| `cam_rec`          | Session start/stop gated on ignition state from APM                  | Flow 12 (camera crash behavior)     |

---

## Flow Dependency Graph

```
boot → [Flow 6: Config Parsing]
     → [Flow 1: Keepalive Monitor] — every 30s → SVC_U → [Flow 9: Timeout Reboot if missed]
     → [Flow 2: Aggregate Status Poll] — every 2s (always)
     → [Flow 3: Voltage Monitor] — every 60s (always)
     → [Flow 4: Supercap Monitor] — every 60s (if apm_supercap_enable)
     → [Flow 5: Ignition Tracker] — on event (if apm_igns_enable)
     → [Flow 7: IMU/GPS/WOM] — every 2s/10s (if apm_imu/gps/wom_enable)

crash → [Flow 8: Crash Recovery] → service_mon restarts APM
      → if restart fails within timeout → [Flow 9: SVC Reboot]

reboot → [Flow 10: Reboot Master + Wakeup Logs]
clock-jump → [Flow 11: Time Jump Handling]
no-internet → [Flow 12: No-Internet Behavior] (no impact on APM core)
cam-crash → [Flow 12: Camera Crash Behavior] (no impact on APM core)
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Skip all tests** if `device_type == "bagheera2"` — APM is not supported
3. **Determine active flows** using the Config-Driven Flow Activation table above
4. **For each active flow**, read the corresponding pytest files from `tests/apm/`
5. **From each test file**, use the log search patterns in `device.search_log()` calls
   and assertion strings as acceptance criteria
6. **Search device logs** in `device_logs/<device_id>/apm.log` for APM-tagged lines;
   also check `service_mon` logs for crash/restart events (Flows 8, 9)
7. **Timing flows** (1, 2, 3, 4): compute average interval between matching log entries;
   tolerance is ±20% of the expected interval
8. **Voltage values**: `C:` (current reading) should be in 9–30V range; `BadBattery: true`
   is a failure signal for the voltage flow
9. **Aggregate status**: `aggregate_status = 1` is expected when ignition is ON;
   `aggregate_status = 0` is expected after ignition OFF
10. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED / SKIPPED
