---
name: speed-service-validation
description: "Use when: validating Speed (speed) service behavior from device logs. Covers initialization, client speed/idle registration, per-second speed processing, speed-threshold detection, idle detection, out-of-idle soak transition, ignition handling, GPS caching, and service_mon registration."
argument-hint: "device ID (e.g., /speed-service-validation 103452403664)"
---

# Speed (`speed`) — Service Knowledge Skill

> **Purpose**: Gives the log-validator agent domain knowledge about the speed service —
> what it does, how its flows relate to each other, and which config keys activate
> which flows. The agent reads test case files for actual log patterns,
> device-type paths, and acceptance criteria — this skill does NOT duplicate those.
>
> **Evidence**: `nd_device_services` @ `dev_6.14` (commit `d44f1bc83`), source
> `speed/src/speed.cpp`; every pattern verified against 4 real device log bundles
> (103432407294, 103452403510, 103452403525, 103452403664).

---

## Service Overview

`speed` is a critical always-running service that computes a single authoritative vehicle-speed value every second and distributes speed/idle state to registered client services.
It handles GPS speed consumption from the NDMB message bus (`TOPIC_GPS_DATA`), OBD/VBUS speed fallback (`TOPIC_OBD_VEH_SPEED`) when GPS is invalid, speed zeroing on bad GPS accuracy or ignition-off, client registration for speed-threshold and idle events, and privacy-mode signaling to ndcentral.
The service interacts with `ndcentral`, `wifi_mgr`, `btfv` (nd_bt), `power_mon`, and `service_mon` via POSIX message queues (queue name `SPEED`) and the NDMB message bus.

**Process name:** `speed` (log TAG `SPD`, message queue `SPEED`)
**Log file:** `speed` logs in `log_<epoch_ms>.log` format under `<log_root>/speed/` (bundled as `device_logs/<id>/speed.log`)
**Primary config sections:** `[speed]`, `[gps]` (read by speed itself); `[privacy_mode_activate]`, `[privacy_mode_deactivate]` (read by ndcentral, which converts them into its registrations with speed)

---

## Device-Type-Specific Paths

| Resource | krait / krait2 | bagheera2 / bagheera3 / bagheera4 / octo |
|----------|---------------|---------------------------------------------|
| Log root | `/data/nd_files/log` | `/home/ubuntu/.nddevice/log` |
| Speed log folder | `<log_root>/speed/` | `<log_root>/speed/` |
| Config file read by speed | `/home/ubuntu/.nddevice/latest/bagheera_config.ini` (hardcoded in source) | same |
| Override file used by tests | `bagheera_override.ini` | same |
| Current speed output | `/dev/shm/speed.info` | same |
| GPS position cache | `/home/ubuntu/.nddevice/gps_cache.json` | same |
| Speed injection file (automation builds only) | `/dev/shm/SPEED` | same |

---

## Service Flows

### Flow 1: Service Initialization

**What happens:** On start, the service initializes the logger, logs `starting...`, reads `[gps] lat_long_retention_duration`, creates the `SPEED` message queue, sends `SPEED_SERVICE_STARTED` to `q_nd_central` (so ndcentral re-registers if it started first), reads `[speed]` config keys, subscribes to NDMB GPS and VBUS topics, registers a 1-second processing timer, and enters the message loop.

**When active:** Always — runs once at every service start/restart
**Frequency:** Once at boot or service restart
**Cross-service impact:** `SPEED_SERVICE_STARTED` nudges ndcentral to (re-)register; service_mon records the start

**Key log patterns:**
- `starting...` — service startup confirmation
- `lat_long_retention_duration is <N>` — GPS cache config loaded
- `Message queue created` — SPEED MSGQ ready
- `out_of_idle_soak_timeout adjusted to minimum value of 5` — only when configured below 5
- `subscribe for GPS data` — NDMB GPS subscription
- `Registering speed processing timer (interval: 1 sec)` — 1 Hz processing started
- `Cannot create message queue` / `unable to init logger` — init failures
- ⚠ Do NOT use `Send GPS registration message` / `GPS updates registration success` — dead code in dev_6.14 (`do_regs()` is never called); 0/4 devices show it

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_speed_460` | `tests/speed/test_tc_speed_460_speed_to_service_mon.py` | "start" timestamp in speed log vs service_mon registration ≤ 500ms | `DT-3942`, `DT-3168` |

---

### Flow 2: Client Speed Registration (REQ_SPEED_REG)

**What happens:** A client sends `REQ_SPEED_REG` with `client_id`, `speed` threshold, and `contig_secs`. The service clamps `contig_secs` to at least `out_of_idle_soak_timeout`, stores the registration keyed by client+speed+contig_secs+type, and acks with `RES_SPEED_REG`. Duplicate registrations refresh the handle. Invalid requests raise `SM_E_SPD_REG_FAIL` to health stats.

**When active:** Always (client-driven). `q_nd_central` speed-registers only when `[privacy_mode_deactivate] speed_based = true` AND bagheera is alive
**Frequency:** On client request (boot, service restart, re-registration)
**Cross-service impact:** Registrations arrive from WIFI_MGR, BTFV, q_nd_central

**Key log patterns:**
- `Spped Reg Key = <client>_<speed>_<contig>_<type>` — key creation (note source typo "Spped")
- `Speed Registration With Key: <N>` — stored
- `Speed Registration Done: <client> <speed> <contig_secs>` — success (observed clients: WIFI_MGR, BTFV, q_nd_central)
- `Duplicate Speed Registration: <client> <speed> <contig_secs>` — duplicate refresh
- `Speed registration failed: <client><speed><contig>` — invalid request
- `Speed unregistration success: <client> <handle>` — unregistration

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_speed_259` | `tests/speed/test_tc_speed_259_ndcentral_idle_registration.py` | "Speed Registration Done: WIFI_MGR" after reboot with speed-based privacy | `BG4-1064`, `DT-3850` |
| `TC_speed_268` | `tests/speed/test_tc_speed_268_service_restart_behaviour.py` | All clients re-register after restarting speed + dependent services | `BG4-1064` |
| `TC_speed_582` | `tests/speed/test_tc_speed_582_dependent_services.py` | q_nd_central does NOT register while bagheera is killed; re-registers after reboot (krait/krait2/octo only) | — |

---

### Flow 3: Client Idle Registration (REQ_IDLE_REG)

**What happens:** A client sends `REQ_IDLE_REG` with `client_id`, `speed` threshold, and `idle_secs`. The service stores the registration and acks with `RES_IDLE_REG`. `q_power_monitor` is automatically marked for periodic idle events. A duplicate registration re-sends the current idle state to the client so it never misses state after a restart. External camera must NOT idle-register (negative requirement).

**When active:** Always (client-driven). `q_nd_central` idle-registers only when `[privacy_mode_activate] speed_based = true` AND bagheera is alive
**Frequency:** On client request
**Cross-service impact:** Registrations arrive from q_power_monitor, BTFV, q_nd_central

**Key log patterns:**
- `Idle Reg Key = <client>_<speed>_<idle_secs>_<type>` — key creation
- `IDLE Registration With Key: <N>` — stored
- `IDLE Registration Done: <client> <speed>` — success (observed clients: BTFV, q_power_monitor, q_nd_central)
- `idle registration done: <client> <N> mph <N> s` — summary with thresholds (e.g., `q_power_monitor 0 mph 60 s`)
- `Resending idle state (idle_on=<0|1>) to duplicate registration for client <client>` — duplicate handling
- `idle registration failed: <client><speed><idle_secs>` — invalid request
- ⚠ `Speed registration done: Ext_cam` must be ABSENT — source casing is `Speed Registration Done:` with client `EXT_CAM`, so match case-insensitively

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_speed_259` | `tests/speed/test_tc_speed_259_ndcentral_idle_registration.py` | "IDLE Registration Done:" for q_power_monitor, BTFV, q_nd_central | `BG4-1064`, `DT-3850` |
| `TC_speed_268` | `tests/speed/test_tc_speed_268_service_restart_behaviour.py` | Same patterns after service restarts | `BG4-1064` |
| `TC_speed_271` | `tests/speed/test_tc_speed_271_ext_cam_idle_registration.py` | NEGATIVE: ext_cam registration absent from speed logs (skips octo) | — |
| `TC_speed_582` | `tests/speed/test_tc_speed_582_dependent_services.py` | q_nd_central idle registration depends on bagheera being alive | — |

---

### Flow 4: Per-Second Speed Processing & Logging

**What happens:** A 1 Hz timer snapshots cached GPS data (marked stale after 2000ms) and processes it. If GPS is invalid and a VBUS/OBD sample is fresher than 2000ms, VBUS speed is substituted. Speed is forced to 0 when accuracy is bad with low speed, or whenever ignition is off. The processed value is logged every second and written to `/dev/shm/speed.info`. On automation (DTA) builds, a value echoed to `/dev/shm/SPEED` overrides the speed — this is the injection mechanism the test cases use.

**When active:** Always
**Frequency:** Every 1s
**Cross-service impact:** None directly — feeds Flows 5–7

**Key log patterns:**
- `Speed = <F>` — per-second processed speed (e.g., `Speed = 0.000000`; ~1 entry/second, 50,800 in one device's bundle)
- `GPS Update: raw_speed: <F> accuracy: <F> timestamp: <N> ignition_status: <0|1>` — logged when speed is zeroed (bad accuracy or ignition off)
- `VBUS speed: <N> timestamp: <N> current_time: <N> delta : <N>` — OBD fallback in use (NOT observed in current bundles — requires GPS-invalid + fresh VBUS window)

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_speed_275` | `tests/speed/test_tc_speed_275_log_for_every_second.py` | Inject 20 / 40 / 55.75 via /dev/shm/SPEED → "raw_speed: NN.NNNNNN" logged each second | `DT-3837` |

---

### Flow 5: Speed-Threshold Detection ("Speed Limit hit")

**What happens:** For each speed registration, every second with valid speed above the registered threshold increments a per-client counter; any second at/below resets it. When the counter reaches `contig_secs` the service logs the hit and sends `RES_SPEED_UPDATE` to the client. If the client is `q_nd_central`, privacy is switched OFF and a `PRIVACY_MODE_UPDATE` is pushed to ndcentral (privacy deactivation while driving).

**When active:** Whenever speed registrations exist; ndcentral leg requires `[privacy_mode_deactivate] speed_based = true`
**Frequency:** Evaluated every 1s
**Cross-service impact:** ndcentral logs "Speed update received from speed" and deactivates privacy

**Key log patterns:**
- `Speed Limit hit` — threshold sustained for contig_secs, RES_SPEED_UPDATE sent
- ndcentral side: `Speed update received from speed` — update consumed
- `sending privacy mode to ndcentral failed` — privacy push failure

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_speed_3537` | `tests/speed/test_tc_speed_3537_contig_secs_default_values.py` | Default disable_time=5 → ndcentral speed update within 4–5s of first speed log | — |
| `TC_speed_3551` | `tests/speed/test_tc_speed_3551_contig_secs_more_than_soak.py` | disable_time=20 > soak=5 → ndcentral update arrives ~20s after first speed | — |
| `TC_speed_3552` | `tests/speed/test_tc_speed_3552_soak_idle_more_than_contig.py` | soak=20 > disable_time=5 → contig clamped to soak; update ~20s | — |

---

### Flow 6: Idle Detection & Privacy Activation

**What happens:** For each idle registration, every second with invalid GPS or speed at/below the registered threshold increments the idle counter and resets the out-of-idle soak state. When the counter reaches `idle_secs`, the service logs idle detection and sends `RES_IDLE_UPDATE(idle_on=true)`. For one-shot clients this fires once; for `q_nd_central` and periodic clients (`q_power_monitor`) the counter resets so the event repeats every `idle_secs`. For `q_nd_central` privacy is switched ON and `PRIVACY_MODE_UPDATE` is pushed (privacy activation when parked).

**When active:** Whenever idle registrations exist; ndcentral leg requires `[privacy_mode_activate] speed_based = true` (its `idle_secs` comes from `enable_time`, default 30)
**Frequency:** Evaluated every 1s; q_nd_central re-notified every ~30s, q_power_monitor every ~60s while idle
**Cross-service impact:** btfv, power_mon, ndcentral consume RES_IDLE_UPDATE; ndcentral activates privacy mode

**Key log patterns:**
- `Vehicle idle detected for client <client>` — idle threshold reached (clients observed: q_nd_central, q_power_monitor, BTFV)
- btfv side: `Received RES_IDLE_UPDATE msg from: SPEED`, `Idle Update received for Driver Login in idle state with idle status: 1`, `Vehicle is in Driver Login Idle`
- power_mon side: `POWERMON RES_IDLE_UPDATE received`
- ndcentral side: `Idle update received from speed`, `Privacy Mode is Activated`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_speed_259` | `tests/speed/test_tc_speed_259_ndcentral_idle_registration.py` | After 420s idle: q_nd_central interval ≤ ~30s (0–32000ms), q_power_monitor ≤ ~60s (0–62000ms) | `BG4-1064`, `DT-3850` |
| `TC_speed_2809` | `tests/speed/test_tc_speed_2809_idle_update_to_ndc_post_ooi.py` | Idle update to q_nd_central after OOI→idle; ndcentral "Privacy Mode is Activated" 28000–32000ms after "Speed = 0.000000" | `AN-32068` |

---

### Flow 7: Out-of-Idle Soak Transition

**What happens:** While an idle-registered client is idle and speed rises above its threshold, the out-of-idle counter increments and a soak countdown decrements every second. Only when the soak expires does the service declare out-of-idle, send `RES_IDLE_UPDATE(idle_on=false)`, and reset the soak to default. Any second back at/below the threshold resets the soak before expiry — brief speed spikes never complete an OOI transition. `out_of_idle_soak_timeout` below 5 is clamped to 5 at config read, and `contig_secs` of speed registrations is clamped to at least the soak value.

**When active:** Whenever idle registrations exist
**Frequency:** Evaluated every 1s during above-threshold periods
**Cross-service impact:** Same RES_IDLE_UPDATE consumers as Flow 6

**Key log patterns:**
- `Out if Idle Count is <N>` — soak countdown value (note source typo "if", not "of")
- `Decrementing Out Of Idle Soak Count` — countdown tick
- `Vehicle 'out of idle' detected <client>` — OOI confirmed, idle_on=false sent
- `Resetting idle_soak_timeout to Default` — transition completed (absence after a spike+drop is the TC_3553 phase-1 pass condition)
- `out_of_idle_soak_timeout adjusted to minimum value of 5` — config below minimum clamped (boot-time)

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_speed_3536` | `tests/speed/test_tc_speed_3536_ooi_soak_default_5s.py` | soak=3 → "adjusted to minimum value of 5" logged; soak reset 5–6s after first speed | — |
| `TC_speed_3537` | `tests/speed/test_tc_speed_3537_contig_secs_default_values.py` | Defaults (5/5): soak reset 5–6s, ndcentral update 4–5s | — |
| `TC_speed_3538` | `tests/speed/test_tc_speed_3538_contig_secs_lower_than_soak.py` | disable_time=2 < soak=5 → soak still governs at 5s | — |
| `TC_speed_3549` | `tests/speed/test_tc_speed_3549_both_contig_ooi_below_default.py` | disable_time=2 and soak=3 → both clamped to 5s | — |
| `TC_speed_3551` | `tests/speed/test_tc_speed_3551_contig_secs_more_than_soak.py` | soak resets at 5s; ndcentral update at ~20s | — |
| `TC_speed_3552` | `tests/speed/test_tc_speed_3552_soak_idle_more_than_contig.py` | soak=20 governs: both events ~20s | — |
| `TC_speed_3553` | `tests/speed/test_tc_speed_3553_speed_spike_and_drop.py` | Phase 1: spike+drop → NO "Resetting idle_soak_timeout to Default"; Phase 2: sustained speed → OOI completes after 5s | — |

---

### Flow 8: Ignition Status Handling

**What happens:** power_monitor sends `POWERMON_IGNITION` messages; the service stores the status atomically. Ignition OFF forces processed speed to 0 regardless of GPS values, which drives idle detection while parked. Invalid status values are logged as errors.

**When active:** Always
**Frequency:** On ignition change events
**Cross-service impact:** Consumes power_monitor messages; indirectly drives Flow 6

**Key log patterns:**
- `ignition_status: <0|1>` — field inside the `GPS Update: raw_speed: ...` line (Flow 4)
- `Invalid ignition status received: <N>` — bad message
- `Failed to handle ignition status change: received null powermon message` — null message

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| — | — | No dedicated test case currently |

---

### Flow 9: GPS Caching

**What happens:** Each valid GPS sample from NDMB is cached in memory; every Nth valid sample (N = `lat_long_retention_duration`, default 5) latitude/longitude are written to `gps_cache.json` with fsync so other services can read last-known position after restarts. The key is bounds-checked (1–600) at startup.

**When active:** Always (key has a default)
**Frequency:** Every Nth valid GPS sample
**Cross-service impact:** Other services read the cached last-known position

**Key log patterns:**
- `lat_long_retention_duration is <N>` — config loaded at boot
- `Out of bound value is found setting to default value 5` — invalid config reset
- `Failed to open gps_cache.json for fsync` — write failure
- `GPS Cache : Latitude: <F> Longitude: <F>` — cache write (DEBUG level, may be absent in INFO-level logs)

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| — | — | No dedicated test case currently |

---

### Flow 10: service_mon Registration & Error Reporting

**What happens:** At start, the service announces itself to service_mon (via `NDService`), which logs the registration with a timestamp. Critical errors during operation (logger init failure, registration failures, unregistration failures) are reported to service_mon/health-stats with dedicated error codes.

**When active:** Always
**Frequency:** Once at start; error events as they occur
**Cross-service impact:** service_mon tracks liveness/restarts of speed

**Key log patterns:**
- service_mon side: `Service started: SPD : <epoch_ms>` — registration recorded (present only when speed [re]starts inside the log window)
- Error codes raised by speed: `SM_E_SPD_LOG_INIT_FAIL`, `SM_E_SPD_REG_FAIL`, `SM_E_SPD_IDLE_REG_FAIL`, `SM_E_SPD_UNREG_FAIL`

**Test cases that validate this flow:**
| Test Case ID | Path | What it checks | Related Bugs |
|---|---|---| --- |
| `TC_speed_460` | `tests/speed/test_tc_speed_460_speed_to_service_mon.py` | service_mon "Service started: SPD" within 500ms of speed init log | `DT-3942`, `DT-3168` |

---

## Config-Driven Flow Activation

The agent MUST read the device config before selecting test cases. speed reads `[speed]` and `[gps]` from `bagheera_config.ini` (test preconditions append overrides to `bagheera_override.ini` and reboot). The `[privacy_mode_*]` sections are read by ndcentral, which converts them into its registrations with speed. Use the following mapping:

| Config Section | Config Key | Value | Activates Flow(s) | Test Cases Affected |
|---|---|---|---|---|
| `[speed]` | `out_of_idle_soak_timeout` | `<int>` (default 5, min 5 enforced) | OOI soak duration (Flow 7); clamps speed-reg `contig_secs` (Flow 2/5) | `TC_speed_3536`, `3537`, `3538`, `3549`, `3551`, `3552`, `3553` |
| `[speed]` | `invalid_speed_threshold` | `<float>` (default 5.0) | Speed zeroing rule (Flow 4) | indirect (all timing TCs) |
| `[speed]` | `invalid_accuracy_threshold` | `<float>` (default 10.0) | Speed zeroing rule (Flow 4) | indirect |
| `[gps]` | `lat_long_retention_duration` | `<int>` (default 5, valid 1–600) | GPS cache cadence (Flow 9) | — |
| `[privacy_mode_activate]` | `speed_based` | `true` (default true) | q_nd_central idle registration (Flows 3, 6) | `TC_speed_259`, `268`, `582`, `2809` |
| `[privacy_mode_activate]` | `enable_speed` / `enable_time` | `0` / `30` (defaults) | q_nd_central idle threshold / idle_secs (Flow 6) | `TC_speed_2809` (28–32s expectations) |
| `[privacy_mode_deactivate]` | `speed_based` | `true` (default true) | q_nd_central speed registration (Flows 2, 5) | `TC_speed_259`, `268`, `582`, `2809` |
| `[privacy_mode_deactivate]` | `disable_speed` / `disable_time` | `5` / `5` (defaults) | q_nd_central speed threshold / contig_secs (Flow 5) | `TC_speed_3537`, `3538`, `3549`, `3551`, `3552`, `3553` |
| `[driverlogin_v2]` | `enabled` | `true` | BTFV driver-login idle handling (btfv side of Flows 3, 6) | `TC_speed_259`, `268` |
| `[ext_cam]` (bagheera_config.ini) | `ch1_enabled`…`ch4_enabled` | device-specific | TC_271 precondition — ext_cam must NOT idle-register | `TC_speed_271` |
| `[ext_cam_config]` (mdvr_config.ini) | `enabled` | device-specific | Same as above | `TC_speed_271` |
| — | — | — | Init, per-second processing, ignition, GPS cache, service_mon (Flows 1, 4, 8, 9, 10 — always active) | `TC_speed_275`, `TC_speed_460` |

**Rules:**
- Flows marked "always active" → run their test cases unconditionally
- Flows gated by a config key → run only if the device config has that key set to the activating value
- If the config key is missing from the device config → use the default value listed above
- `TC_speed_582` only applies to `krait`, `krait2`, `octo`; `TC_speed_271` skips `octo`
- Speed injection (`echo N > /dev/shm/SPEED`) only works on automation/DTA-enabled builds — injection TCs are NOT_TRIGGERED otherwise

---

## Cross-Service Dependencies

| Related Service | Why | When to check its logs |
|---|---|---|
| `ndcentral` | Registers with speed for idle+speed privacy events; receives PRIVACY_MODE_UPDATE; logs "Registered with speed for idle/speed privacy events", "Idle update received from speed", "Speed update received from speed", "Privacy Mode is Activated" | When validating Flows 2, 3, 5, 6 (privacy timing in `TC_speed_2809`) |
| `wifi_mgr` | Speed-registers with speed; logs "Speed Service Registration Successfull" (note source spelling) | When validating Flow 2 (`TC_speed_259`, `268`) |
| `btfv` (nd_bt) | Idle+speed registers for driver-login; logs "Received RES_IDLE_UPDATE msg from: SPEED", "Vehicle is in Driver Login Idle" | When validating Flows 3, 6 (`TC_speed_259`, `268`) |
| `power_mon` | Periodic idle consumer ("POWERMON RES_IDLE_UPDATE received"); source of ignition status messages | When validating Flows 3, 6, 8 (`TC_speed_259`) |
| `bagheera` | q_nd_central's registration with speed requires bagheera alive | When validating Flows 2, 3 (`TC_speed_582`) |
| `service_mon` | Logs "Service started: SPD : <ts>"; monitors speed liveness and receives its error codes | When validating Flows 1, 10 (`TC_speed_460`) |

---

## Flow Dependency Graph

```
boot/restart → [Flow 1: Init] → SPEED_SERVICE_STARTED → ndcentral re-registers
                              → [Flow 10: service_mon "Service started: SPD"]
                              → NDMB subscribe (GPS, VBUS) → [Flow 4: 1 Hz processing]
                              → [Flow 9: GPS cache every Nth valid sample]

clients (ndcentral / wifi_mgr / btfv / power_mon)
        → [Flow 2: REQ_SPEED_REG] ┐
        → [Flow 3: REQ_IDLE_REG]  ┴─ registrations stored

[Flow 4] every 1s:
  ignition OFF (Flow 8) or bad accuracy → speed := 0
  speed > reg.speed for contig_secs → [Flow 5: "Speed Limit hit"] → RES_SPEED_UPDATE
        └ q_nd_central → privacy OFF → ndcentral deactivates privacy
  speed ≤ reg.speed for idle_secs → [Flow 6: "Vehicle idle detected"] → RES_IDLE_UPDATE(on)
        └ q_nd_central → privacy ON → ndcentral "Privacy Mode is Activated" (~30s, TC_2809)
  idle → speed rises → soak countdown → [Flow 7: "Vehicle 'out of idle' detected"] → RES_IDLE_UPDATE(off)

config ([speed] out_of_idle_soak_timeout < 5) → clamped to 5 at boot (Flow 1 log)
event (bagheera killed) → q_nd_central cannot register (TC_582)
```

---

## Log Format

All speed logs use epoch-millisecond format:
```
<epoch_ms>: <uptime_ms>: SPD: <LEVEL>: <PID>: <TID>: <message>
```

Examples (from real device logs):
```
1779424935136: 184: SPD: I: 6129: 6129: starting...
1779424939774: 4822: SPD: I: 6129: 6129: Speed Registration Done: WIFI_MGR 0 10
1779424935863: 911: SPD: I: 6129: 6129: idle registration done: q_power_monitor 0 mph 60 s
1780383972238: 31766: SPD: I: 6206: 6436: Vehicle idle detected for client q_nd_central
1779401841957: 2453343: SPD: I: 6046: 6597: Vehicle 'out of idle' detected q_power_monitor
1779402112368: 2723754: SPD: I: 6046: 6597: GPS Update: raw_speed: 0.000000 accuracy: 3.540000 timestamp: 1779402112000 ignition_status: 0
1779401762890: 2374276: SPD: I: 6046: 6597: Speed = 0.000000
```

Cross-service example lines (for correlation):
```
1779399392367: 375: WIFI_MGR: I: 6697: 6818: Speed Service Registration Successfull
1780383951895: 9633: NDC: I: 6503: 6503: Registered with speed for idle privacy events
1779424111764: 1389: SM: I: 5889: 5889: Service started: SPD : 1779424111723
```

**Tags:** `SPD` (speed — single-tag service); related logs use `NDC` (ndcentral), `WIFI_MGR`, `SM` (service_mon)

**Levels:** `C` (Critical), `E` (Error), `W` (Warning), `I` (Info), `D` (Debug)

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini` (plus `bagheera_override.ini` overrides if captured)
2. **Determine active flows** using the Config-Driven Flow Activation table above
3. **For each active flow**, read the mapped test case files from `tests/speed/`
4. **From each test**, use assertion patterns, device-type log roots, and timing thresholds (500ms, 4–5s, 5–6s, ~20s, 0–32000ms, 0–62000ms, 28000–32000ms) for log searches
5. **Search device logs** in `device_logs/<device_id>/speed.log` using patterns from this skill
6. **For cross-service checks**, also search `ndcentral.log`, `wifi_mgr.log`, `btfv.log`, `power_mon.log`, `service_mon.log` as listed above
7. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED
8. **Time filtering**: Parse the leading `<epoch_ms>:` field to filter logs to the relevant window and to compute interval assertions
9. **Skip rules**: q_nd_central flows are NOT_TRIGGERED when `speed_based` privacy keys are false; injection TCs (`TC_speed_275`, soak/contig TCs) are NOT_TRIGGERED on non-automation builds; `TC_speed_582` requires krait/krait2/octo; `TC_speed_271` skips octo
10. **Pattern caveats**: grep "Out if Idle Count" (source typo); match ext_cam negative check case-insensitively; never use the dead patterns "Send GPS registration message" / "GPS updates registration success"
