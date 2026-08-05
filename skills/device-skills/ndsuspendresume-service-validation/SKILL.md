---
name: ndsuspendresume-service-validation
description: "Use when: validating NDSuspendResume (nd_suspendresume) service behavior from device logs. Covers standby entry/exit sequences, service stop/start orchestration, suspend mode config, crank-off behavior, low power wakeup, partial file handling, edge crank scenarios, WOM configuration, and nd_shutdown validation."
argument-hint: "device ID (e.g., /ndsuspendresume-service-validation 103452403510)"
---

# NDSuspendResume (`nd_suspendresume`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the NDSuspendResume service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads test case files for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`nd_suspendresume` is a critical system service responsible for orchestrating device suspend (SC7 standby) entry and exit on Netradyne devices. When invoked by `power_monitor` on crank-off or LPW events, it stops all device services in a defined order, configures wake-on-motion (WOM) if supported, disables PMIC watchdog, executes the vendor standby entry script, and enters SC7 suspend. On resume, it reboots the device (when `reboot_on_suspend_exit=true`) or restarts services in reverse order.

**Process name:** `nd_suspendresume`
**Log tag:** `STNDBY`
**Log folder:** `nd_suspendresume/` (path defined per device type — see log_paths below)
**Primary config section:** `[power]`
**Device support:** bagheera, bagheera2, bagheera3 only (krait/krait2 → skip/NA)
**Related binary:** `nd_shutdown` (handles non-suspend graceful shutdown)

---

## Log Paths by Device Type

| Device Type | Log Root |
|---|---|
| bagheera, bagheera2, bagheera3, bagheera4, octo | `/home/ubuntu/.nddevice/log` |
| krait, krait2 | `/data/nd_files/log` (but service is NA on krait) |

Service-specific logs at: `<log_root>/nd_suspendresume/`

---

## Log Format

```
<epoch_ms>: <uptime_ms>: STNDBY: <LEVEL>: <PID>: <TID>: <message>
```

Also contains `SYS_U` tagged lines for each system command executed:
```
<epoch_ms>: <uptime_ms>: SYS_U: I: <PID>: <TID>: system_execute cmd: <command>
<epoch_ms>: <uptime_ms>: SYS_U: I: <PID>: <TID>: Done with system_execute: pclose execution return status : Success(0), command exit code:(<code>)
```

**Key log patterns:**
- `######Starting STANDBY ENTRY######` — suspend entry begins
- `######Ending STANDBY ENTRY######` — all services stopped, entering SC7
- `######Starting STANDBY EXIT######` — resume from SC7 begins
- `######Ending STANDBY EXIT######` — all services restarted
- `reboot_on_suspend_exit(<0|1>)` — config value for reboot-on-exit
- `suspend execution done` — systemctl suspend command succeeded
- `Activating` — suspend state is activating (waiting)
- `failed to move to suspend state` — suspend failed
- `suspend failed, so rebooting` — fallback reboot on failure
- `Systemtime before suspend: <ms>, Systemtime after suspend: <ms>` — time tracking
- `Time spent in suspend (ms): <ms>` — duration in SC7
- `rebooting the device` — reboot after suspend exit
- `DeviceType: <type>` — device type detection
- `WOM Configuration Failed!!!` — wake-on-motion setup error
- `AON WOM Disabled, Not Configuring the IMU Device` — WOM not supported/disabled

---

## Service Flows

### Flow 1: Standby Entry — Service Stop Orchestration

**What happens:** When invoked by power_monitor, nd_suspendresume stops ~33 services in a specific order (cam_rec first, nd_sam last, then nvargus-daemon). After stopping services, it removes shared memory files (`/dev/shm/MSGQ`, `/dev/shm/nd_files_c`, `/dev/shm/*.bin`) and syncs filesystem.

**When active:** Always when nd_suspendresume is started (triggered by power_monitor)
**Frequency:** Each suspend cycle
**Cross-service impact:** ALL device services are stopped

**Services stopped (in order):** cam_rec → bagheera → awsiot → scheduler_manager → circular_buffer → diagnostic → uploader → speed → obd → ext_cam → installer_app → nd_bt → time_sync → conn_mgr → wifi_mgr → power_monitor → analyticsService → outwardAnalyticsClient → canAnalyticsClient → deviceHealthClient → inwardAnalyticsClient → unifiedAnalyticsClient → audioPlayback → HealthStatsManager → svc → service_mon → cron → haveged → systemd-resolved → apm → gps → nd_dta → nd_sam → (sleep 2) → nvargus-daemon

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1214` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1214_behaviour_crankoff.py` | Standby entry/exit with crank_shutdown_duration=1 |
| `TC_1269` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1269_behaviour_crankoff_equaltozero.py` | Standby entry/exit with crank_shutdown_duration=0 |
| `TC_1277` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1277_behaviour_crankoff_equaltofifteen.py` | Standby entry/exit with crank_shutdown_duration=15 |
| `TC_1279` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1279_timespent_in_stopping_services.py` | Total stop time ≤ 16000ms |

---

### Flow 2: Config Parsing & Suspend Mode

**What happens:** Reads `bagheera_config.ini` (with override support) for:
- `[power] reboot_on_suspend_exit` — whether to reboot after SC7 exit (default: true)
- `[power] suspend_mode` — on/off to enable/disable suspend feature
- `[apm] apm_wom_enable` — wake-on-motion enable
- `[apm] enable_ignition_based_wakeup` — ignition-based wakeup

**When active:** At startup, before suspend entry
**Frequency:** Once per invocation
**Cross-service impact:** If suspend_mode=off in power_monitor config, nd_suspendresume is never invoked

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1192` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1192_override_parse.py` | suspend_mode parsed from override file |
| `TC_1193` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1193_default_config.py` | Default suspend_mode value per device type |

---

### Flow 3: SC7 Suspend Execution

**What happens:** After stopping services:
1. Configures WOM (if supported and enabled)
2. Disables PMIC watchdog
3. Executes vendor script `/etc/init.d/sc7_entry.sh`
4. Records epoch before suspend, writes `SUSPEND_STATE: SC7_E:<epoch>` to reset_reason.txt
5. Calls `systemctl suspend` in a retry loop (max 3 retries)
6. Monitors suspend state: activating (with 60s timeout), failed, or success
7. If failed after retries → writes `SUSPEND_STATE: SC7_F:<epoch>` → reboots
8. On successful resume → logs time spent, writes `SUSPEND_STATE: SC7_X:<epoch>`

**When active:** Only when suspend_mode=on and triggered by power_monitor
**Frequency:** Each suspend cycle
**Cross-service impact:** Device enters deep sleep; all processing stops

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1214` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1214_behaviour_crankoff.py` | Full suspend/resume flow verification |
| `TC_1215` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1215_behaviour_lpw.py` | Suspend flow during LPW cycle |
| `TC_1269` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1269_behaviour_crankoff_equaltozero.py` | Immediate suspend (crank_shutdown=0) |
| `TC_1277` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1277_behaviour_crankoff_equaltofifteen.py` | Delayed suspend (crank_shutdown=15) |

---

### Flow 4: Standby Exit — Service Restart / Reboot

**What happens:** After resume from SC7:
- If `reboot_on_suspend_exit=true` (default) → logs "rebooting the device" → system_reboot()
- If `reboot_on_suspend_exit=false` → updates uptime/boottime files, enables PMIC watchdog, executes `/etc/init.d/sc7_exit.sh`, restarts all services in reverse order (haveged first, bagheera last)

**When active:** After SC7 resume
**Frequency:** Each suspend cycle exit
**Cross-service impact:** All services restarted (or device reboots entirely)

**Services started (exit order):** haveged → systemd-resolved → cron → time_sync → power_monitor → svc → service_mon → nvargus-daemon → awsiot → circular_buffer → uploader → diagnostic → speed → obd → wifi_mgr → conn_mgr → installer_app → nd_bt → ext_cam → analyticsService → outwardAnalyticsClient → canAnalyticsClient → deviceHealthClient → inwardAnalyticsClient → unifiedAnalyticsClient → audioPlayback → HealthStatsManager → scheduler_manager → apm → gps → nd_dta → nd_sam → cam_rec → bagheera

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1214` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1214_behaviour_crankoff.py` | Exit sequence and reboot |
| `TC_1215` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1215_behaviour_lpw.py` | Exit during LPW |

---

### Flow 5: Power Monitor Invocation

**What happens:** `power_monitor` detects crank-off (ignition loss) or LPW cycle end, waits `crank_shutdown_duration` seconds, then starts nd_suspendresume via `systemctl start nd_suspendresume.service`. The power_monitor log shows the invocation command.

**When active:** When suspend_mode=on in power_monitor config
**Frequency:** On each crank-off or LPW transition
**Cross-service impact:** power_monitor is the trigger; it must log the invocation

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1216` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1216_power_mon_invoke_ndsr.py` | power_mon logs "systemctl start nd_suspendresume.service" |

---

### Flow 6: Suspend Mode Off — Graceful Shutdown

**What happens:** When `suspend_mode=off`, power_monitor performs a graceful shutdown instead of invoking nd_suspendresume. No nd_suspendresume logs are generated. The power_mon log shows the shutdown sequence.

**When active:** When suspend_mode=off
**Frequency:** On crank-off events
**Cross-service impact:** Device does full shutdown instead of suspend

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1217` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1217_suspend_mode_off_behaviour.py` | power_mon shows graceful shutdown, not suspend |
| `TC_1218` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1218_disable_log_folder_check.py` | No new nd_suspendresume logs generated |

---

### Flow 7: Low Power Wakeup (LPW) Cycle

**What happens:** During LPW, the device wakes periodically based on `lowpower_wakeup_cycle_duration` and `lowpower_wakeup_duration`. On each LPW wake, power_monitor may invoke nd_suspendresume again. The suspend/resume cycle repeats with LPW counter incrementing.

**When active:** When suspend_mode=on AND LPW config is set
**Frequency:** Each LPW cycle
**Cross-service impact:** power_monitor tracks LPW count; BTFV may scan on wake

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1215` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1215_behaviour_lpw.py` | Full LPW suspend/resume cycle |
| `TC_1278` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1278_partialfile_addition_lpw.py` | Partial file on SD card during LPW suspend |

---

### Flow 8: Partial File Handling Before Suspend

**What happens:** Before suspend, the recording service (cam_rec) is stopped first. Any in-progress recording creates a partial file on the SD card. The partial file size should be < 44MB (not a full segment).

**When active:** When recording is active at time of suspend
**Frequency:** Each suspend cycle
**Cross-service impact:** cam_rec creates the partial file; circ_buff may reference it

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1262` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1262_partialfile_addition.py` | Partial file exists, size < 44MB |
| `TC_1278` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1278_partialfile_addition_lpw.py` | Partial file during LPW suspend |

---

### Flow 9: Edge Crank Scenario

**What happens:** If ignition (crank voltage) returns while nd_suspendresume is in the process of stopping services, power_monitor should detect this and postpone/cancel the shutdown. This is the "edge crank" case where the driver cranks the engine during the shutdown countdown.

**When active:** When suspend_mode=on and crank voltage changes during shutdown sequence
**Frequency:** Edge case
**Cross-service impact:** power_monitor must detect the voltage change and abort

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1285` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1285_edge_crank_scenario.py` | Shutdown postponed on crank return |

---

### Flow 10: BTFV Scan on Resume

**What happens:** After device wakes from suspend (on resume), if `driverlogin.enabled=true`, the BTFV service starts a Bluetooth scan to detect the driver's phone for login verification.

**When active:** When suspend_mode=on AND driverlogin.enabled=true
**Frequency:** Each resume from suspend
**Cross-service impact:** btfv service performs BT scan

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1261` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1261_btfv_scan.py` | btfv logs show BT scan started after resume |

---

### Flow 11: Service Status & File Permissions

**What happens:** Basic operational checks — service is active, required files exist with correct permissions.

**When active:** Always (prerequisite checks)
**Frequency:** Static checks
**Cross-service impact:** None

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks |
|---|---|---|
| `TC_1212` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1212_status.py` | nd_suspendresume service is active |
| `TC_1213` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1213_logging.py` | Log files are generated after suspend cycle |
| `TC_1288` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1288_sh_file_permission_check.py` | nd_suspendresume.sh has 755 permissions |
| `TC_1298` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1298_nd_shutdown_files_and_permissions_check.py` | nd_shutdown files exist with correct perms |
| `TC_1299` | `tests/ndsuspendresume/test_tc_ndsuspendresume_1299_nd_shutdown_status.py` | nd_shutdown service is active |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[power]` | `suspend_mode` | `on` | All suspend flows (Entry, SC7, Exit, LPW) | TC_1192, TC_1212–TC_1216, TC_1261–TC_1285 |
| `[power]` | `suspend_mode` | `off` | Graceful shutdown flow only | TC_1217, TC_1218 |
| `[power]` | `crank_shutdown_duration` | `0` | Immediate suspend on crank-off | TC_1269, TC_1262, TC_1278, TC_1279, TC_1285 |
| `[power]` | `crank_shutdown_duration` | `1` | 1s delay before suspend | TC_1213, TC_1214, TC_1215, TC_1216, TC_1261 |
| `[power]` | `crank_shutdown_duration` | `15` | 15s delay before suspend | TC_1277 |
| `[power]` | `reboot_on_suspend_exit` | `true` (default) | Reboot on SC7 exit | Most TCs |
| `[power]` | `reboot_on_suspend_exit` | `false` | Restart services on exit | — |
| `[power]` | `lowpower_wakeup_cycle_duration` | `15` | LPW cycle active | TC_1215, TC_1278 |
| `[power]` | `lowpower_wakeup_duration` | `30` | LPW wakeup window | TC_1215, TC_1278 |
| `[apm]` | `apm_wom_enable` | `true` | Wake-on-motion configured | TC_1214 (bagheera3) |
| `[driverlogin]` | `enabled` | `true` | BTFV scan on resume | TC_1261 |
| — | — | — | Status/Permission checks (always) | TC_1212, TC_1288, TC_1298, TC_1299 |

**Rules:**
- **Device type gate:** ALL nd_suspendresume TCs are NA for krait/krait2 — skip if device_type is krait or krait2
- If `suspend_mode=off` → only TC_1217, TC_1218 are valid; skip all other suspend TCs
- If `suspend_mode=on` → skip TC_1217, TC_1218; run remaining based on crank_shutdown_duration
- TC_1193 checks *default* config value — bagheera2 expects `on`, bagheera3 expects `off`
- Status/permission checks (TC_1212, TC_1288, TC_1298, TC_1299) are always runnable

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `power_monitor` | Triggers nd_suspendresume on crank-off; logs invocation command | TC_1192, TC_1214–TC_1217 |
| `cam_rec` | First service stopped; creates partial recording files | TC_1262, TC_1278 |
| `btfv` | Starts BT scan on resume when driverlogin enabled | TC_1261 |
| `circular_buffer` | References partial files on SD card | TC_1262, TC_1278 |
| `nd_shutdown` | Alternative shutdown path when suspend_mode=off | TC_1298, TC_1299 |

---

## Flow Dependency Graph

```
power_monitor detects crank-off/LPW
     → waits crank_shutdown_duration seconds
     → "systemctl start nd_suspendresume.service"
          → [Flow: Config Parse] → reads suspend_mode, reboot_on_suspend_exit, WOM
          → [Flow: Standby Entry] → stops 33+ services in order
               → cleans /dev/shm (MSGQ, nd_files_c, *.bin)
               → sync filesystem
          → [Flow: WOM Config] (if bagheera3 + wom enabled)
          → disable PMIC watchdog
          → execute /etc/init.d/sc7_entry.sh
          → write SUSPEND_STATE: SC7_E to reset_reason.txt
          → [Flow: SC7 Suspend] → systemctl suspend (retry x3)
               ├── success → device sleeps in SC7
               │              → wake event → resume
               │              → write SUSPEND_STATE: SC7_X
               │              → [Flow: Standby Exit]
               │                   ├── reboot_on_suspend_exit=true → system_reboot()
               │                   └── reboot_on_suspend_exit=false → restart services
               └── failed (3 retries) → write SUSPEND_STATE: SC7_F → system_reboot()

suspend_mode=off path:
     power_monitor → graceful shutdown (no nd_suspendresume involvement)
```

---

## Reset Reason File

nd_suspendresume writes suspend state transitions to `/home/ubuntu/.nddevice/reset_reason.txt`:
- `SUSPEND_STATE: SC7_E:<epoch_seconds>` — entering SC7
- `SUSPEND_STATE: SC7_X:<epoch_seconds>` — exiting SC7 (successful resume)
- `SUSPEND_STATE: SC7_F:<epoch_seconds>` — SC7 failed
- `SUSPEND_STATE: REBOOT:<epoch_seconds>` — rebooting after exit
- Wake reason string (from PMIC register)
- `VALUE: <power_on_off_reason>` — PMIC register value

---

## Validation Instructions for the Agent

1. **Check device type** — if krait/krait2, mark ALL nd_suspendresume TCs as NA
2. **Read device config** from `device_data/device_<ID>_config.ini`
3. **Check `[power] suspend_mode`** — determines which test set applies
4. **For suspend_mode=on flows**, search `device_logs/<device_id>/nd_suspendresume.log` for:
   - "Starting STANDBY ENTRY" / "Ending STANDBY ENTRY"
   - Service stop commands (SYS_U lines)
   - "suspend execution done" or "failed to move to suspend state"
   - "Time spent in suspend (ms):" for duration validation
5. **For power_monitor cross-checks**, search `power_mon.log` for:
   - "systemctl start nd_suspendresume.service"
   - "suspend_mode" parsing
6. **For partial file checks**, verify SD card file existence and size
7. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED / NA
