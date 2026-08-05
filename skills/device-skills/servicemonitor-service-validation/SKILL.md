---
name: servicemonitor-service-validation
description: "Use when: validating Service Monitor (service_mon) service behavior from device logs. Covers initialization, config-driven enable/disable, message queue creation, service start/stop/error event logging, critical event JSON persistence, and per-service status checks."
argument-hint: "device ID (e.g., /servicemonitor-service-validation 103432407294)"
---

# Service Monitor (`service_mon`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the Service Monitor —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads test case files for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

---

## Service Overview

`service_mon` is a critical system service that acts as the centralized health event recorder for all Netradyne device services. It receives start, stop, and error messages from other services via a shared message queue and logs them. Error events are also persisted to a JSON file (`sm_critical_events.json`) for later upload. The service can be fully disabled via config, in which case it sleeps indefinitely.

**Process name:** `service_mon`
**Log tag:** `SM`
**Log folder:** `service_mon/` (path defined per device type — see log_paths below)
**Critical events JSON:** `/home/ubuntu/.nddevice/log/sm_critical_events.json`
**Primary config section:** `[process_mon]`
**Message queue name:** `SM` (at `/dev/shm/MSGQ/SM`)
**Log split interval:** Every 30 minutes

---

## Log Paths by Device Type

| Device Type | Log Root |
|---|---|
| bagheera2, bagheera3, bagheera4, octo | `/home/ubuntu/.nddevice/log` |
| krait, krait2 | `/data/nd_files/log` |

Service-specific logs at: `<log_root>/service_mon/`

---

## Log Format

```
<epoch_ms>: <uptime_ms>: SM: <LEVEL>: <PID>: <TID>: <message>
```

**Key log patterns:**
- `sm_enabled: 1` — service monitor enabled
- `sm_enabled: 0` — service monitor disabled
- `Config load success` — config parsed successfully
- `Message queue created` — msgq initialized
- `Message queue server created SM` — server queue ready
- `Service started: <SNAME> : <timestamp>` — service start event received
- `Service stopped: <SNAME> : <timestamp>` — service stop event received
- `Service error: <SNAME> : <timestamp> : <code> : <aux_code> : <desc> : <uptime>` — error event
- `Service monitor disabled by config` — last line when disabled

---

## Service Flows

### Flow 1: Initialization & Logging Setup

**What happens:** On start, `service_mon` initializes its logger (routes to file), reads `bagheera_config.ini` to check if `[process_mon] error_report` is enabled. If config parse fails, defaults are used (sm_enabled=true). Creates log folders `service_mon/` and `service_mon_c/`.

**When active:** Always (at boot/restart)
**Frequency:** Once at service start
**Cross-service impact:** None — self-contained init

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_servicemonitor_476` | `tests/servicemonitor/test_tc_servicemonitor_476_logging.py` | Log folders exist, .log files present | — |
| `TC_servicemonitor_479` | `tests/servicemonitor/test_tc_servicemonitor_479_sm_enabled.py` | `sm_enabled: 1` appears in logs after restart | `BG4-957`, `BG4-948`, `BG4-878`, `DT-3835` |

---

### Flow 2: Config-Driven Enable/Disable

**What happens:** Reads `[process_mon] error_report` from `bagheera_config.ini`. If value is `"false"`, sets `sm_enabled=false`, logs `"sm_enabled: 0"` and `"Service monitor disabled by config"`, then enters infinite sleep. If `"true"` or absent, proceeds with normal operation.

**When active:** Always checked at startup
**Frequency:** Once at boot
**Cross-service impact:** When disabled, no service events are recorded — all other services lose health monitoring

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_servicemonitor_478` | `tests/servicemonitor/test_tc_servicemonitor_478_config.py` | `error_report=true` in config | `BG4-948` |
| `TC_servicemonitor_479` | `tests/servicemonitor/test_tc_servicemonitor_479_sm_enabled.py` | `sm_enabled: 1` when enabled | `BG4-957`, `BG4-948`, `BG4-878`, `DT-3835` |
| `TC_servicemonitor_481` | `tests/servicemonitor/test_tc_servicemonitor_481_sm_disabled.py` | `sm_enabled: 0` and disabled message when `error_report=false` | — |

---

### Flow 3: Message Queue Creation

**What happens:** Creates a server-mode message queue named `"SM"` using `nd_msgq_t`. Other services connect as clients to send start/stop/error messages. The queue file appears at `/dev/shm/MSGQ/SM`. If creation fails, the service exits after a 60-second delay (allowing systemd restart).

**When active:** Only when `sm_enabled=true`
**Frequency:** Once at boot (after config check)
**Cross-service impact:** All services depend on this queue to report their status events

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_servicemonitor_482` | `tests/servicemonitor/test_tc_servicemonitor_482_msgq_creation.py` | "Message queue created" in logs + SM file at `/dev/shm/MSGQ/` | — |

---

### Flow 4: Service Start Event Handling

**What happens:** When a service sends `REQ_SM_START` (msg type 800), service_mon logs `"Service started: <SNAME> : <timestamp>"`. The service name (SNAME) is the internal tag used by each service (e.g., SVC, NDC, IOT, CB, WIFI_MGR, etc.).

**When active:** Only when `sm_enabled=true` and msgq is running
**Frequency:** On every service start event received
**Cross-service impact:** All monitored services send this on startup

**Service name mapping (systemd → SM log tag):**
| Systemd Service | SM Log Tag |
|---|---|
| svc | SVC |
| bagheera | NDC |
| awsiot | IOT |
| circular_buffer | CB |
| nd_bt_man | nd_bt_man |
| timesync | TIME_SYNC |
| wifi_mgr | WIFI_MGR |
| apm | APM |
| diagnostic | DIAG |
| fan_control | FAN |
| obd | OBD |
| power_monitor | PWR |
| scheduler_manager | SCHED_M |
| speed | SPEED |
| uploader | UPLOADER |
| nd_sam | ND_SAM |
| otacheck | OTA |
| inference | INF |
| inference_inertial | INF_I |
| scheduler | SCHED |
| deleteMetadata | DEL_META |
| keep_alive_manager | KAM |
| outwardAnalyticsClient | outwardAnalyticsClient |
| analyticsService | AnalyticsService |

**Test cases that validate this flow:**
| Test Case ID | File Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_servicemonitor_516` | `tests/servicemonitor/test_tc_servicemonitor_516_status_check_svc.py` | "Service started: SVC :" logged with correct timestamp | — |
| `TC_servicemonitor_520` | `tests/servicemonitor/test_tc_servicemonitor_520_status_check_bagheera.py` | "Service started: NDC :" logged | — |
| `TC_servicemonitor_523` | `tests/servicemonitor/test_tc_servicemonitor_523_status_check_awsiot.py` | "Service started: IOT :" logged | — |
| `TC_servicemonitor_524` | `tests/servicemonitor/test_tc_servicemonitor_524_status_check_circular_buffer.py` | "Service started: CB :" logged | — |
| `TC_servicemonitor_525` | `tests/servicemonitor/test_tc_servicemonitor_525_status_check_diagnostic.py` | "Service started: DIAG :" logged | — |
| `TC_servicemonitor_527` | `tests/servicemonitor/test_tc_servicemonitor_527_status_check_apm.py` | "Service started: APM :" logged | — |
| `TC_servicemonitor_528` | `tests/servicemonitor/test_tc_servicemonitor_528_status_check_fan_control.py` | "Service started: FAN :" logged | — |
| `TC_servicemonitor_529` | `tests/servicemonitor/test_tc_servicemonitor_529_status_check_obd.py` | "Service started: OBD :" logged | `DT-3522` |
| `TC_servicemonitor_530` | `tests/servicemonitor/test_tc_servicemonitor_530_status_check_power_monitor.py` | "Service started: PWR :" logged | — |
| `TC_servicemonitor_531` | `tests/servicemonitor/test_tc_servicemonitor_531_status_check_scheduler_manager.py` | "Service started: SCHED_M :" logged | — |
| `TC_servicemonitor_532` | `tests/servicemonitor/test_tc_servicemonitor_532_status_check_speed.py` | "Service started: SPEED :" logged | — |
| `TC_servicemonitor_533` | `tests/servicemonitor/test_tc_servicemonitor_533_status_check_timesync.py` | "Service started: TIME_SYNC :" logged | — |
| `TC_servicemonitor_534` | `tests/servicemonitor/test_tc_servicemonitor_534_status_check_uploader.py` | "Service started: UPLOADER :" logged | — |
| `TC_servicemonitor_535` | `tests/servicemonitor/test_tc_servicemonitor_535_status_check_wifi_mgr.py` | "Service started: WIFI_MGR :" logged | — |
| `TC_servicemonitor_536` | `tests/servicemonitor/test_tc_servicemonitor_536_status_check_nd_sam.py` | "Service started: ND_SAM :" logged | — |
| `TC_servicemonitor_539` | `tests/servicemonitor/test_tc_servicemonitor_539_status_check_nd_bt.py` | "Service started: nd_bt_man :" logged | — |
| `TC_servicemonitor_540` | `tests/servicemonitor/test_tc_servicemonitor_540_status_check_otacheck.py` | "Service started: OTA :" logged | — |
| `TC_servicemonitor_541` | `tests/servicemonitor/test_tc_servicemonitor_541_status_check_inference.py` | "Service started: INF :" logged | — |
| `TC_servicemonitor_542` | `tests/servicemonitor/test_tc_servicemonitor_542_status_check_inference_inertial.py` | "Service started: INF_I :" logged | — |
| `TC_servicemonitor_543` | `tests/servicemonitor/test_tc_servicemonitor_543_status_check_scheduler.py` | "Service started: SCHED :" logged | — |
| `TC_servicemonitor_544` | `tests/servicemonitor/test_tc_servicemonitor_544_status_check_deletemetadata.py` | "Service started: DEL_META :" logged | — |
| `TC_servicemonitor_545` | `tests/servicemonitor/test_tc_servicemonitor_545_status_check_keepalivemanager.py` | "Service started: KAM :" logged | — |
| `TC_servicemonitor_2762` | `tests/servicemonitor/test_tc_servicemonitor_2762_status_check_outwardanalyticsclient.py` | "Service started: outwardAnalyticsClient :" logged | — |
| `TC_servicemonitor_2831` | `tests/servicemonitor/test_tc_servicemonitor_2831_status_check_analyticsservice.py` | "Service started: AnalyticsService :" logged | — |

---

### Flow 5: Service Stop Event Handling

**What happens:** When a service sends `REQ_SM_STOP` (msg type 801), service_mon logs `"Service stopped: <SNAME> : <timestamp>"`. Used to detect graceful shutdowns (SIGTERM).

**When active:** Only when `sm_enabled=true` and msgq is running
**Frequency:** On every service stop event received
**Cross-service impact:** Monitored services send this before exiting

**Test cases that validate this flow:**
All status check TCs (TC_516 through TC_2831) also verify stop events via SIGTERM kill of the target service.

---

### Flow 6: Service Error Event Handling & JSON Persistence

**What happens:** When a service sends `REQ_SM_ERR` (msg type 802), service_mon:
1. Logs `"Service error: <SNAME> : <timestamp> : <code> : <aux_code> : <desc> : <uptime>"`
2. Calls `add_err_log()` which persists the error to `sm_critical_events.json`
3. JSON deduplication: if same (code, aux_code, process_name, timestamp/1min) exists, increments count instead of adding new entry

**JSON fields:** `timestamp`, `process_name`, `code`, `code_aux`, `sys_uptime`, `desc`, `count`

**When active:** Only when `sm_enabled=true` and msgq is running
**Frequency:** On every error event received
**Cross-service impact:** APM, SVC, WIFI_MGR, and others send error reports with device state info

**Test cases that validate this flow:**
All status check TCs (TC_516 through TC_2831) validate error event handling via SIGABRT crash simulation.

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[process_mon]` | `error_report` | `true` (default) | All flows (Init, MsgQ, Event Handling) | TC_476, TC_479, TC_482, TC_516–TC_2831 |
| `[process_mon]` | `error_report` | `false` | Disable flow only (logs disabled message) | TC_481 |
| — | — | — | Init & Logging (always active) | TC_476 |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- If `error_report` is `false` → only TC_481 (disabled behavior) is valid; skip all status check TCs
- If `error_report` is `true` or missing → run all TCs except TC_481
- TC_478 is a config verification/fix TC — always runnable

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| All monitored services (svc, bagheera, awsiot, etc.) | Send start/stop/error messages to SM queue | When validating status check TCs |
| `systemd` | Manages service lifecycle; provides ActiveEnterTimestamp | When comparing log timestamps |
| `bagheera_config.ini` | Controls `error_report` enable/disable | When validating TC_478, TC_479, TC_481 |

---

## Flow Dependency Graph

```
boot → [Flow: Init & Logging Setup] → log folders created, logger routed
     → [Flow: Config Check] → reads [process_mon] error_report
           ├── error_report=false → "Service monitor disabled by config" → sleep forever
           └── error_report=true  → [Flow: MsgQ Creation] → /dev/shm/MSGQ/SM created
                                  → [Flow: Message Loop] (infinite)
                                        ├── REQ_SM_START (800) → "Service started: <SNAME>"
                                        ├── REQ_SM_STOP  (801) → "Service stopped: <SNAME>"
                                        └── REQ_SM_ERR   (802) → "Service error: ..." → JSON persist
```

---

## Status Check Test Pattern

All status check TCs (TC_516 through TC_2831) follow the same structure:
1. Verify `service_mon` and target service are active
2. Restart target service → verify "Service started: <TAG>" logged with correct timestamp (±15s of systemd)
3. Kill target service with SIGABRT → verify "Service error: <TAG>" logged (crash event)
4. Kill target service with SIGTERM → verify "Service stopped: <TAG>" logged (graceful stop)
5. Restart target service to restore state

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Check `[process_mon] error_report`** — if `false`, only validate TC_481 behavior
3. **For each active flow**, read the mapped test case files from `tests/servicemonitor/`
4. **Search device logs** in `device_logs/<device_id>/service_mon.log` using the key log patterns above
5. **For status check TCs**, verify "Service started: <TAG>" entries exist for each monitored service
6. **For error events**, verify JSON structure in `sm_critical_events.json` if accessible
7. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
