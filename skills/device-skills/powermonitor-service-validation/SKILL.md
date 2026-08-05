---
name: powermonitor-service-validation
description: "Use when: validating Power Monitor (power_monitor) service behavior from device logs. Covers service startup & initialization, ignition / crank-change detection, ignition broadcast to peer services, crank-off shutdown arbitration with postpones, cyclic reboot, low power wakeup (LPW) cycle, bad-battery / voltage threshold shutdown, peer-initiated reboot requests (AWSIOT / SVC / installer / camera-crash / analytics / MSP), back-to-back reboot delay enforcement, ka_minified keep-alive to cloud, POWERSTATES DB read/write, cross-service uptime / keepalive correlation, graceful modem (Sierra) shutdown, DHUB sync at crank-off, and WOM (wake-on-motion) interactions."
argument-hint: "device ID (e.g., /powermonitor-service-validation 440073)"
---

# Power Monitor (`power_monitor`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the Power Monitor service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads test cases for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.

**Log files:** `power_mon.log` (info+) and `power_mon_c.log` (critical-only) — path defined per device type in device config

## Service Overview

`power_monitor` is the device's power state machine and shutdown arbiter. It
owns ignition / crank GPIO detection (CRANK_HIGH, CRANK_LOW), an ADC-driven
battery-voltage thread, a priority-ranked shutdown reason arbiter, the
`power_mon.db` SQLite event log (`POWERSTATES` table), the low-power-wakeup
(LPW) scheduler with RTC alarms, and the `ka_minified` keep-alive payload
that publishes the device's ignition state and previous-shutdown-reason to
cloud. It is the single source of truth for "why did the device reboot/shut
down" and the gatekeeper for every peer-initiated reboot request (awsiot,
svc, installer app, ext_cam, analytics, MSP).

It interacts with `awsiot` (cloud-initiated reboots), `svc` (keepalive
watchdog reboot), `ext_cam` (cam-crash reboot), `installer_app` (in-field
reboots), `nd_central` / `btfv` / `wifi_mgr` / `unifieduploader` / `obd` /
`speed` (ignition broadcast fan-out), `time_sync` (LPW count
synchronization), `keep_alive_manager` (zipped logs on shutdown), and the
Sierra LTE modem (`AT!POWERDOWN` graceful power-down on every planned
shutdown).

**Process name:** `power_monitor`
**Log files:** `power_mon.log` (info+) and `power_mon_c.log` (critical-only) — path defined per device type in device config
**Primary config sections:** `[power]`, `[wom]`, `[supercap]`

---

## Service Flows

### Flow 1: Service Startup & Initialization

**What happens:** On boot the service prints `#####STARTING POWER MONITOR#####`,
opens the IPC queue `q_power_monitor` (`MSGQ` tag), opens/creates the
`POWERSTATES` table in `power_mon.db` (idempotent — benign
`table POWERSTATES already exists` is expected), parses `[power]` from
`bagheera_config.ini` + override, dumps every effective config value as a
PWR line (one per knob — this is the canonical "what is this device
actually configured with"), reads `battery_config` and sets 12 V / 24 V
voltage thresholds, starts six worker threads
(`power_monitor_msg_loop`, `gpio<N>_interrupt_thread_fn`,
`direct_poling_thread_fn`, `shutdown_poling_thread_fn`,
`Monitor Ignition GPIO Status Thread`, `keepalive_powerstate_thread_fn`),
subscribes to GPS, broadcasts the current ignition status to BTFV / WIFI_MGR
clients, and reads the previous shutdown reason from
`POWERSTATES`.

**When active:** Always (every boot)
**Frequency:** Once per boot
**Cross-service impact:** Must complete before any peer reboot request is
honoured; `time_sync`, `ext_cam`, `svc` may log
`Error creating token … No such file or directory` against `q_power_monitor`
until this flow finishes (benign retry).

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_92`  | Service active / running | — |
| `TC_pm_708` | Service logging present | — |
| `TC_pm_721` | `q_power_monitor` MSGQ token created | — |
| `TC_pm_722` | Logger init at startup | — |
| `TC_pm_770` | DB creation logged | — |
| `TC_pm_794` | nd_device object instantiated | — |
| `TC_pm_103` | Service uptime sane after boot | — |

---

### Flow 2: Config Parsing & Override

**What happens:** Parses `[power]` section keys from `bagheera_config.ini`
with `bagheera_override.ini` applied on top. Each effective value is
printed via the `CFG_PRSR` / `PWR` tags as part of the boot config dump
(see Flow 1). Keys include `crank_shutdown_duration`,
`enable_lowpowermode`, `lowpower_wakeup_*`, `cyclic_reboot_duration`,
`min_voltage_limit*`, `max_voltage_limit*`, `delay_reboot_time`,
`max_B2B_reboot_allowed`, `max_postpone_shutdown_time_uploader_activity`,
`dhub_status_check_enabled`, `ignition_on_idle_audio_alert*`,
`safety_wakeup*`, `master_shutdown_enable`, `frequent_low_power_wakeup`.
WOM-related parsing reads `[wom]`; supercap thresholds read `[supercap]`.

**When active:** Always
**Frequency:** Once per boot (no live reload)
**Cross-service impact:** None directly; values affect every other flow.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_93`  | Override config content applied | — |
| `TC_pm_809` | All `[power]` keys parsed | — |
| `TC_pm_107` | `cyclic_reboot_duration` read correctly | `DT-3503` |
| `TC_pm_2817`| Supercap voltage config in ka payload | `BG4-846` |

---

### Flow 3: Crank-high → Ignition-ON Broadcast

**What happens:** GPIO171 ISR (`gpio<N>_interrupt_thread_fn`) or the
direct-polling thread observes a rising crank edge. After
`max_allowed_crank_changes` / `max_crank_events_to_debounce` debounce, the
msg-loop posts `POWERMON_CRANK_CHANGE` to itself, then fans out
`POWERMON_IGNITION` (status=IGNITION_ON) to the seven fixed clients:
`Q_NDCENTRAL, Q_BTFV, Q_EXT_CAM, Q_UPL, Q_WIFI, Q_OBD, Q_SPD` (`Q_OBD`
suppressed when `vehicle_data_enabled=false`). Each peer logs
`Ignition status sent to <Q_NAME>` (success) or
`Sending Ignition status : sent to <Q_NAME> failed` (peer not up).

**When active:** Always
**Frequency:** On every rising crank edge
**Cross-service impact:** `nd_central`, `btfv`, `wifi_mgr`,
`unifieduploader`, `ext_cam`, `obd`, `speed` consume the ignition message.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_94`  | ka_minified fires on IGN-ON | `DT-3588` |
| `TC_pm_109` | Immediate cyclic reboot when uptime already > threshold at IGN-ON | `DT-4247`, `DT-3503` |
| `TC_pm_768` | Crank toggle reaches APM | — |
| `TC_pm_842` | ka_minified on back-to-back crank | — |
| `TC_pm_843` | Uptime accounting across crank toggles | — |

---

### Flow 4: Crank-low → Shutdown Arbitration

**What happens:** On falling crank edge the arbiter raises
`SHUTDOWN_FOR_IGNITION_OFF` with the `crank_shutdown_duration` timer
(default 900 s on bench). While the timer runs any of these cancel /
postpone the shutdown:
- `POSTPONE_FOR_IGNITION_ON` (crank goes high again)
- `POSTPONE_FOR_UPLOADER_ACTIVITY` (uploader busy, capped by
  `max_postpone_shutdown_time_uploader_activity`, default 45 min)
- `POSTPONE_FOR_DHUB_STATUS_CHECK` (DVR sync, default 26 s wakeup)
- `POSTPONE_FOR_NORMAL_RUN` (steady-state heartbeat)
The chosen reason is logged as
`shutdown reason ::@ time <N> Shuttingdown because of SHUTDOWN_FOR_IGNITION_OFF ::`
and the matching DB row is written via `add_event_db for DBSTATE_SHUTDOWN_CRANKOFF`.

**When active:** Always
**Frequency:** On every falling crank edge
**Cross-service impact:** `unifieduploader` postpone signal,
`circular_buffer` for DHUB sync window.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_95`  | ka_minified on IGN-OFF | `DT-3476`, `DT-3553`, `DT-3588` |
| `TC_pm_106` | Shutdown reason = crank-low | — |
| `TC_pm_252` | Postpone shutdown during LPM transitions | `DT-3882`, `DT-3669` |
| `TC_pm_729` | Service state across crank-off | — |
| `TC_pm_730` | Uptime tracking pauses on crank-low | — |
| `TC_pm_810` | Reboot during crank-off window | — |
| `TC_pm_846` | 10-min crank-off shutdown timing | `DT-3476`, `DT-3553` |
| `TC_pm_847` | 20-min crank-off shutdown timing | `DT-3483` |

---

### Flow 5: Cyclic Reboot

**What happens:** When `uptime_secs > cyclic_reboot_duration` AND
`enable_cyclic_reboot=true`, the steady-state heartbeat (`check_uptime`)
posts `SHUTDOWN_FOR_CYCLIC_REBOOT`. The reboot is recorded as
`DBSTATE_SHUTDOWN_CYCLIC` in `POWERSTATES`, and the *next* boot's
`ka_minified` payload publishes
`previousShutdownReason: "DBSTATE_SHUTDOWN_CYCLIC:REBOOT : BATTERY_ACTIVE"`.

**When active:** Only when `[power] enable_cyclic_reboot = true` (default `true`)
**Frequency:** Every `cyclic_reboot_duration` (default 60 min upstream;
production values vary by product line — D215: 480 min / 8 h, D210: 360 min
/ 6 h, D450/D430: 960 min / 16 h)
**Cross-service impact:** All services restart after the reboot.

> _Updated for x.6.15.rc.1 (DT-4129, DT-4191):_ Reduced `cyclic_reboot_duration`
> from 960→480 min on D215 and 960→360 min on D210 to mitigate camera crash risk
> on long-running devices (>30 h continuous uptime). D450/D430 unchanged.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_107` | Config value matches duration enforced | `DT-3503` |
| `TC_pm_108` | Cyclic reboot honoured during IGN-OFF | `DT-3503` |
| `TC_pm_109` | Reboot fires immediately when threshold already exceeded at IGN-ON | `DT-4247`, `DT-3503` |
| `TC_pm_799` | Uptime resets across cyclic reboot | — |
| `TC_pm_826` | Service active after cyclic reboot | `DT-3503` |
| `TC_pm_689` | `max_uptime_secs` enforced | — |
| `TC_pm_3495`| `max_uptime_secs` (regression) | — |
| `TC_pm_694` | 30 s uptime granularity | — |
| `TC_pm_4200`| Production config value (480/360) correctly parsed on D21X | `DT-4207` |
| `TC_pm_4201`| Cyclic reboot triggers cleanly without preceding camera crash | — |

---

### Flow 6: Low Power Wakeup (LPW) Cycle

**What happens:** When ignition has stayed off long enough AND
`enable_lowpowermode=true` AND `lpw_count < max_lowpower_wakeups`, the
service schedules an RTC alarm `lowpower_wakeup_cycle_duration` seconds
out (default 1800 s on bench), shuts down, then wakes on RTC, runs for
`lowpower_wakeup_duration` seconds, writes `DBSTATE_LOWPOWER_WAKEUP` to
`POWERSTATES`, increments the LPW counter, and shuts back down. After
`lowpower_wakeup_long_cycle_threshold=32` short cycles, switches to a long
cycle `lowpower_wakeup_long_cycle_duration` (default 24 h). Hard ceiling:
`max_lowpower_wakeups=240`. Safety wakeup at `safety_wakeup` minutes fires
even if scheduling logic fails. `time_sync` reads the LPW count from
`POWERSTATES` on boot via `LOW_POWER_WAKEUP_CNT_UPDATE`.

**When active:** Only when `[power] enable_lowpowermode = true` (default `true`)
**Frequency:** Every `lowpower_wakeup_cycle_duration` after ignition stays off
**Cross-service impact:** `time_sync` (reads count), all services restart on each wake.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_226` | LPW cycle triggers | `DT-3669`, `DT-3739`, `OCTO-1984` |
| `TC_pm_238` | LPM count increments | `BG4-616`, `BG4-618`, `BG4-1033`, `DT-3705` |
| `TC_pm_239` | ka_minified on IGN-ON after LPM | `DT-3669`, `DT-3739` |
| `TC_pm_240` | Video recording state in LPM | — |
| `TC_pm_241` | Upload state to cloud in LPM | — |
| `TC_pm_242` | Second LPW cycle | — |
| `TC_pm_700` | DB updated with LPW row | — |
| `TC_pm_706` | Manual LPW DB row | — |
| `TC_pm_709` | Service active in LPM | `OCTO-1984` |
| `TC_pm_724` | `time_sync` reads LPW count | — |
| `TC_pm_731` | Uptime tracking off in LPM | — |
| `TC_pm_804` | Shutdown timestamp logged for LPM | — |
| `TC_pm_864` | Long-cycle LPW after 32 short cycles | — |
| `TC_pm_2368`| `max_lowpower_wakeups` cap not crossed | `BG4-616`, `BG4-618` |

---

### Flow 7: Bad-Battery / Voltage Shutdown

**What happens:** `adc_voltage_thread` samples ADC channel 2. If the
reading sits outside `[min_voltage_limit, max_voltage_limit]` (or the
24 V counterparts) for `abnormal_voltage_wait_duration` consecutive
seconds (default 6 s), the arbiter posts
`POWERMON_BAD_BATTERY_VOLTAGE` then `SHUTDOWN_FOR_BAD_VOLTAGE`. The
device **does not** schedule an LPW — it stays down until ignition or
supercap discharge brings it back. Boot following a bad-battery shutdown
logs `Previous shutdown reason: DBSTATE_SHUTDOWN_BADVOLTAGE`. Voltage
normalization clears the latch via `POWERMON_BAD_BATTERY_CLEAR received`.

**When active:** Always (gated by voltage actually crossing the threshold)
**Frequency:** On event (sustained out-of-range reading ≥ `abnormal_voltage_wait_duration`)
**Cross-service impact:** `keep_alive_manager` raises a bad-battery alert.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_2588`| Bootup with invalid voltage | `DT-3241`, `DT-3670`, `OCTO-1970`, `DT-4001` |
| `TC_pm_2817`| Supercap / voltage values surface in ka payload | `BG4-846` |

---

### Flow 8: Peer-Initiated Reboot Requests

**What happens:** Other services send typed reboot requests over MSGQ.
Each request name in the upstream source carries the typo `recieved`
(`power_monitor.cpp:3950+`) — preserve it verbatim when matching:

| Inbound message | Source service | Resulting reason |
|---|---|---|
| `REQ_POWERMON_TO_AWSIOT_REBOOT recieved` | `awsiot` | `SHUTDOWN_FOR_AWSIOT` |
| `REQ_POWERMON_TO_SVC_REBOOT recieved` | `svc` keepalive watchdog | `SHUTDOWN_FOR_SVC_REBOOT` |
| `REQ_POWERMON_TO_CAM_CRASH_REBOOT recieved` | `ext_cam` | `SHUTDOWN_FOR_CAM_CRASH` |
| `REQ_POWERMON_TO_INSTALLER_APP_REBOOT recieved` | `installer_app` | `SHUTDOWN_FOR_INSTALLER_APP` |
| `REQ_POWERMON_INSTALLER_APP_CRASH_TO_REBOOT recieved` | installer crash watcher | `SHUTDOWN_FOR_INSTALLER_APP_CRASH` |
| `REQ_POWERMON_ANALYTICS_TO_REBOOT recieved` | analytics | `SHUTDOWN_FOR_ANALYTICS` |
| `REQ_POWERMON_MSP_FAIL_TO_REBOOT recieved` | MSP supervisor | `SHUTDOWN_FOR_MSP_FAIL_REBOOT` |
| `REQ_POWERMON_SDCARD_RO_REBOOT recieved` | filesystem watcher | `SHUTDOWN_FOR_SDCARD_RO_RECOVERY` |

After accepting any peer request the arbiter waits `delay_reboot_time`
(default 900 s) before actually rebooting unless overridden by a higher-
priority reason.

**When active:** Always (gated on the peer actually sending the request)
**Frequency:** On event
**Cross-service impact:** Requesting service triggers the reboot; the
peer's own log shows the request being sent.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_710` | Service alive after AWSIOT reboot | `BG4-894` |
| `TC_pm_823` | Service alive after cam-crash reboot | `OCTO-2146`, `BG4-900` |
| `TC_pm_761` | SVC reboot path: no ka_minified expected | `DT-4247` |
| `TC_pm_950` | SVC-timeout reason on NDC failure | `DT-3963` |
| `TC_pm_955` | SVC-timeout reason on PM failure | `DT-4247` |
| `TC_pm_956` | Watchdog-timeout reason recorded | — |
| `TC_pm_1014`| Recorded reason never 'NA' | `DT-3950` |
| `TC_pm_2582`| PM kill triggers exactly one reboot | — |

---

### Flow 9: Back-to-Back Reboot Delay

**What happens:** The arbiter caps repeated reboots within
`delay_reboot_time` at `max_B2B_reboot_allowed` (default 10,
`power_monitor.cpp:116`). When two reboot requests of the same class
arrive in quick succession, the second one is held until the delay
window expires, preventing reboot storms after cam-crash or svc-reboot.

**When active:** Always
**Frequency:** On event (back-to-back requests)
**Cross-service impact:** Source service sees a delayed reboot.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_1018`| Back-to-back cam-crash reboot delay | `DT-4247`, `OCTO-2146` |
| `TC_pm_1020`| Back-to-back SVC reboot delay | — |

---

### Flow 10: ka_minified Keep-Alive to Cloud

**What happens:** `keepalive_powerstate_thread_fn` builds the
`ka_minified` payload (current ignition state, `previousShutdownReason`
string, current GPS location) and publishes via the standard cloud
keep-alive channel. The thread fires on every power-state edge and on a
heartbeat; each retry increments `ka_retry_cnt` (logged as
`Change in power state; notifying to cloud ka_retry_cnt: <N>`). The
`previousShutdownReason` string is one of the suffixes
`:REBOOT : BATTERY_ACTIVE` / `:SHUTDOWN : BATTERY_ACTIVE` /
`:SHUTDOWN : SUPERCAP_ACTIVE` / `:POWER_DOWN:SW_REBOOT : BATTERY_ACTIVE`
appended to a `DBSTATE_SHUTDOWN_*` prefix.

**When active:** Always
**Frequency:** On every ignition transition + heartbeat (~30 s cadence)
**Cross-service impact:** `conn_mgr` carries the payload to cloud;
`keep_alive_manager` records analytics.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_94`  | Trigger on IGN-ON | `DT-3588` |
| `TC_pm_95`  | Trigger on IGN-OFF | `DT-3476`, `DT-3553`, `DT-3588` |
| `TC_pm_100` | Required fields present | — |
| `TC_pm_102` | Trigger cadence | — |
| `TC_pm_239` | Payload after IGN-ON post-LPM | `DT-3669`, `DT-3739` |
| `TC_pm_842` | Back-to-back crank produces correct ka | — |

---

### Flow 11: POWERSTATES DB Read/Write

**What happens:** Every state-machine event (crank high/low, LPW, all
`DBSTATE_SHUTDOWN_*`) is appended to the `POWERSTATES` table:
columns `INDEXID` (autoinc PK), `BOOTTIME`, `PIDNUM`, `EVENTTIME`,
`EVENT` (one of `power_dbstate_enum_t`), `ACTION`
(`NA` | `REBOOT` | `SHUTDOWN`). The DB layer logs under tag `MP_SQL`
with `inside add_event_db for <EVENT>` → `node received : index … event …`
pairs. On boot, the most-recent SHUTDOWN/REBOOT row is read to populate
`previousShutdownReason`. DB corruption falls back to schema re-create.

**When active:** Always
**Frequency:** Per state event; one boot-time read; one daily-ish vacuum
**Cross-service impact:** `time_sync` reads `DBSTATE_LOWPOWER_WAKEUP`
count via dedicated message.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_699` | DB present and openable | — |
| `TC_pm_700` | LPW row inserted | — |
| `TC_pm_702` | Multiple crank-low rows | — |
| `TC_pm_706` | Manual LPW DB row | — |
| `TC_pm_763` | DB row content correct | — |
| `TC_pm_764` | Recovery from corruption | — |
| `TC_pm_770` | DB creation logged | — |

---

### Flow 12: Graceful Modem Shutdown & DHUB Sync at Crank-Off

**What happens:** Before any planned shutdown the service sends
`AT!POWERDOWN` to `/dev/ttyUSB<N>` under tag `SIERRA_PWR_DWN` and waits
for `OK` (or times out). A paired `SIERRA_SIM_LPM` step issues
`AT+CFUN=<N>`. In parallel, when `dhub_status_check_enabled=true` and a
crank-off is in flight, the arbiter holds the shutdown for
`dhub_wakeup_sync_extra` seconds (default 26 s) to let DVR sync drain
(`POSTPONE_FOR_DHUB_STATUS_CHECK`). If the modem times out
(`Err: Timeout!!`) the shutdown still proceeds.

**When active:** Modem: every planned shutdown. DHUB: only when
`[power] dhub_status_check_enabled = true`.
**Frequency:** Per shutdown
**Cross-service impact:** LTE modem, `circular_buffer` (DHUB sync drain).

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_243` | Modem `AT!POWERDOWN` issued | — |
| `TC_pm_788` | Full shutdown completes | — |
| `TC_pm_846` | 10-min DHUB-aware crank-off | `DT-3476`, `DT-3553` |
| `TC_pm_847` | 20-min DHUB-aware crank-off | `DT-3483` |
| `TC_pm_762` | Logs zipped before shutdown | `BG4-878` |

---

### Flow 13: WOM (Wake-on-Motion) Interactions

**What happens:** When `[wom] enabled = true`, ignition/LPW behavior is
augmented by IMU-driven wake events. The shutdown arbiter respects WOM
events (`nlpm_on_imu` / `wake_on_imu` knobs), the ka_minified payload
reports WOM-state, and crank-off timing is gated by IMU activity.

**When active:** Only when `[wom] enabled = true`
**Frequency:** On event (IMU motion) + on every IGN transition
**Cross-service impact:** `nd_central` IMU pipeline.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_812` | WOM master enable read | `DT-3668`, `DT-3444` |
| `TC_pm_830` | ka_minified shape when WOM enabled | — |
| `TC_pm_831` | APM ignition path under WOM | `DT-3882`, `DT-3884`, `DT-3668` |
| `TC_pm_834` | LPW count + timesync under WOM | — |
| `TC_pm_841` | Crank-change broadcast under WOM | `OCTO-2151` |
| `TC_pm_849` | 10-min crank-off + WOM | — |
| `TC_pm_850` | 20-min crank-off + WOM | `DT-3483` |

---

### Flow 14: Cross-Service Uptime Correlation

**What happens:** `power_monitor` exposes its own uptime via heartbeat
logs (`check_uptime max_uptime <N> uptime_secs <N>`) and broadcasts the
current ignition status to peers at startup
(`send_current_ignition_satus_to_client called for client_id: <X>` —
typo `satus` is upstream). Tests use this to confirm that every peer
service's uptime stays within tolerance of `power_monitor`'s and that
peer services see ignition broadcasts within seconds of PM coming up.

**When active:** Always
**Frequency:** Heartbeat (~30 s)
**Cross-service impact:** `svc`, `nd_central`, `btfv`, `wifi_mgr`,
`ext_cam`, `unifieduploader`, `time_sync`, `apm`.

**Test cases that validate this flow:**

| Test Case ID | What it checks | Related Bugs |
|---|---| --- |
| `TC_pm_725` | SVC keepalive 30 s cadence | `DT-3723` |
| `TC_pm_733` | SVC vs PM uptime diff | — |
| `TC_pm_735` | time_sync vs PM uptime diff | — |
| `TC_pm_767` | APM vs PM uptime diff | — |
| `TC_pm_798` | NDC/BTFV/WIFI/EXT/UPL uptime diff | — |

---

## Config-Driven Flow Activation

The agent MUST read the device config from `device_data/device_<ID>_config.ini`
before selecting test cases. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| — | — | — | Startup & Init (always) | TC_92, TC_103, TC_708, TC_721, TC_722, TC_770, TC_794 |
| — | — | — | Config parsing (always) | TC_93, TC_809, TC_107, TC_2817 |
| — | — | — | Crank-high broadcast (always) | TC_94, TC_109, TC_768, TC_842, TC_843 |
| — | — | — | Crank-low arbitration (always) | TC_95, TC_106, TC_252, TC_729, TC_730, TC_810, TC_846, TC_847 |
| `[power]` | `enable_cyclic_reboot` | `true` (default `true`) | Cyclic Reboot | TC_107, TC_108, TC_109, TC_799, TC_826, TC_689, TC_3495, TC_694 |
| `[power]` | `cyclic_reboot_duration` | `<N>` min (default 60) | Cyclic reboot cadence | TC_107, TC_799 |
| `[power]` | `enable_lowpowermode` | `true` (default `true`) | LPW Cycle | TC_226, TC_238, TC_239, TC_240, TC_241, TC_242, TC_700, TC_706, TC_709, TC_724, TC_731, TC_804, TC_864, TC_2368 |
| `[power]` | `lowpower_wakeup_cycle_duration` | `<N>` s (default 180) | LPW interval | TC_226, TC_242 |
| `[power]` | `lowpower_wakeup_long_cycle_threshold` | `32` | Long-cycle transition | TC_864 |
| `[power]` | `max_lowpower_wakeups` | `240` | LPW cap | TC_2368 |
| `[power]` | `min_voltage_limit*` / `max_voltage_limit*` | float V | Bad-battery shutdown | TC_2588, TC_2817 |
| `[power]` | `abnormal_voltage_wait_duration` | `6` s | Bad-battery debounce | TC_2588 |
| `[power]` | `dhub_status_check_enabled` | `true` | DHUB sync postpone | TC_846, TC_847 |
| `[power]` | `delay_reboot_time` | `900` s | B2B reboot window | TC_1018, TC_1020 |
| `[power]` | `max_B2B_reboot_allowed` | `10` | B2B reboot cap | TC_1018, TC_1020 |
| `[power]` | `max_postpone_shutdown_time_uploader_activity` | `45` min | Uploader postpone cap | TC_252 |
| `[wom]` | `enabled` | `true` | WOM Flow | TC_812, TC_830, TC_831, TC_834, TC_841, TC_849, TC_850 |
| — | — | — | Peer reboot requests (always; gated by peer sending) | TC_710, TC_823, TC_761, TC_950, TC_955, TC_956, TC_1014, TC_2582 |
| — | — | — | ka_minified (always) | TC_94, TC_95, TC_100, TC_102, TC_239, TC_842 |
| — | — | — | POWERSTATES DB (always) | TC_699, TC_700, TC_702, TC_706, TC_763, TC_764, TC_770 |
| — | — | — | Modem graceful shutdown (always) | TC_243, TC_788, TC_846, TC_847, TC_762 |
| — | — | — | Cross-service uptime (always) | TC_725, TC_733, TC_735, TC_767, TC_798 |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by a config key → run only if the device config has that key set to the activating value
- If the config key is missing from the device config → use the default value listed above
- Config values in `device_list_config.csv` take precedence if present (they reflect live production config)

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `awsiot` | Sends cloud-initiated reboot requests via `REQ_POWERMON_TO_AWSIOT_REBOOT` | Validating Flow 8 (AWSIOT branch) — TC_710 |
| `svc` | Sends watchdog/keepalive reboot, owns 30 s keepalive cadence | Validating Flow 8 (SVC branch) and Flow 14 — TC_725, TC_733, TC_761, TC_950, TC_955 |
| `ext_cam` | Sends `REQ_POWERMON_TO_CAM_CRASH_REBOOT recieved` on camera crash | Validating Flow 8 (cam-crash) — TC_823, TC_1018 |
| `installer_app` | Sends installer reboot requests | Validating Flow 8 (installer branch) |
| `unifieduploader` | Receives `POWERMON_IGNITION`; postpones shutdown via uploader-activity signal | Validating Flow 3 and Flow 4 postpones — TC_252 |
| `nd_central` | Consumes ignition broadcast; reports SVC timeout that surfaces in PM's shutdown reason | Validating Flow 3 broadcast + Flow 8 (`TC_pm_950`) |
| `btfv`, `wifi_mgr`, `obd`, `speed` | Consume ignition broadcast | Validating Flow 3 fan-out + Flow 14 (`TC_pm_798`) |
| `time_sync` | Reads LPW count via `LOW_POWER_WAKEUP_CNT_UPDATE` | Validating Flow 6 — TC_724, TC_735, TC_834 |
| `apm` | Mirrors ignition state; uptime correlated | Validating Flow 14 — TC_767, TC_768, TC_831 |
| `keep_alive_manager` | Zips logs before shutdown; consumes ka_minified analytics | Validating Flow 12 — TC_762 |
| `circular_buffer` | DHUB sync window observed during crank-off postpone | Validating Flow 12 — TC_846, TC_847 |
| LTE modem (Sierra) | `AT!POWERDOWN` / `AT+CFUN` during graceful shutdown (`SIERRA_*` tags) | Validating Flow 12 — TC_243 |

---

## Flow Dependency Graph

```
boot
 ├─ MSGQ: q_power_monitor created
 ├─ #####STARTING POWER MONITOR#####
 ├─ open POWERSTATES (idempotent)
 ├─ parse [power] / [wom] / [supercap]  → CFG dump
 ├─ read battery_config → set 12V/24V voltage thresholds
 ├─ read previous shutdown reason from POWERSTATES
 ├─ threads: msg_loop, gpio_isr, direct_poll, shutdown_poll,
 │           ign_gpio_monitor, keepalive_powerstate, adc_voltage
 ├─ subscribe GPS
 └─ broadcast current ignition status → BTFV, WIFI_MGR (initial)

main msg_loop:
 ├─ POWERMON_CRANK_CHANGE (rising)  → fan-out POWERMON_IGNITION → 7 peers
 │                                  → ka_minified(IGN=1)
 ├─ POWERMON_CRANK_CHANGE (falling) → SHUTDOWN_FOR_IGNITION_OFF (timer)
 │                                  → postpone: IGN_ON | UPLOADER | DHUB | NORMAL_RUN
 │                                  → on commit: add_event_db(DBSTATE_SHUTDOWN_CRANKOFF)
 ├─ POWERMON_NORMAL_RUN (heartbeat) → check_uptime
 │                                  → uptime > cyclic_reboot_duration ⇒ SHUTDOWN_FOR_CYCLIC_REBOOT
 ├─ POWERMON_BAD_BATTERY_VOLTAGE   → SHUTDOWN_FOR_BAD_VOLTAGE  (no LPW)
 ├─ REQ_POWERMON_TO_AWSIOT_REBOOT  → SHUTDOWN_FOR_AWSIOT
 ├─ REQ_POWERMON_SVC_TO_REBOOT     → SHUTDOWN_FOR_SVC_REBOOT
 ├─ REQ_POWERMON_TO_CAM_CRASH_REBOOT → SHUTDOWN_FOR_CAM_CRASH
 ├─ REQ_POWERMON_TO_INSTALLER_APP_REBOOT       → SHUTDOWN_FOR_INSTALLER_APP
 ├─ REQ_POWERMON_INSTALLER_APP_CRASH_TO_REBOOT → SHUTDOWN_FOR_INSTALLER_APP_CRASH
 ├─ REQ_POWERMON_ANALYTICS_TO_REBOOT  → SHUTDOWN_FOR_ANALYTICS
 ├─ REQ_POWERMON_MSP_FAIL_TO_REBOOT   → SHUTDOWN_FOR_MSP_FAIL_REBOOT
 ├─ REQ_POWERMON_SDCARD_RO_REBOOT     → SHUTDOWN_FOR_SDCARD_RO_RECOVERY
 ├─ LOW_POWER_WAKEUP_CNT_UPDATE (from time_sync) → POWERSTATES read
 └─ on any accepted shutdown:
     ├─ enforce B2B delay (delay_reboot_time / max_B2B_reboot_allowed)
     ├─ SIERRA_PWR_DWN: AT!POWERDOWN, SIERRA_SIM_LPM: AT+CFUN
     ├─ add_event_db(DBSTATE_SHUTDOWN_*)
     ├─ schedule RTC (LPW or safety_wakeup)
     └─ reboot / power down
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **For each active flow**, look up the mapped test cases for `power_monitor`
4. **From each test case**, use `acceptance_criteria` for log patterns and device-type paths from device config
5. **Search device logs** in `device_logs/<device_id>/power_mon.log` (info+) — fall back to `power_mon_c.log` only for critical-event triage
6. **For cross-service checks**, also search logs of related services listed in Cross-Service Dependencies
7. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
8. **Note:** Several upstream log strings carry typos that must be matched verbatim: `recieved` (peer reboot requests), `satus` (initial ignition broadcast), `happned` (NORMAL_RUN postpone), `TIMOUT` (`POWERMON_AVOID_TIMOUT`). Do not "fix" them in patterns.
9. **Note:** The log format is: `<epoch_ms>: <uptime_ms>: <TAG>: <LEVEL>: <PID>: <TID>: <message>` — TAG values include `PWR`, `MP_SQL`, `MSGQ`, `CFG_PRSR`, `DEVB3`, `SVC_U`, `SIERRA_PWR_DWN`, `SIERRA_SIM_LPM`, `AUDIO`, `NDMBC`, `NDMBS`.
