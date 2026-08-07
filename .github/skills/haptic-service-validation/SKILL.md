---
name: haptic-service-validation
description: "Use when: validating the haptic_feedback service. Index of the haptic flow-specific skills — service status/lifecycle, audio/ignition behavior, config, stability/reboot recovery, database operations, metadata/session, alert-session-flow, and livestreaming. Load the relevant sub-skill for the flow you need instead of this file alone."
argument-hint: "device ID (e.g., /haptic-service-validation 440073)"
---

# Haptic (`haptic_feedback`) — Service Knowledge Skill

> **Purpose**: Grounded service-level reference for haptic_feedback validation. Keep
> this file for service behavior, config, dependencies, and agent guidance. Atomic
> flow skills are linked elsewhere and do not need to be indexed here.

---

## Service Overview

`haptic_feedback` (`HPTC` in service_mon logs) is a companion service that delivers vibration
alerts (haptic motor via GPIO/USB hub) when DMS/alert events are raised. It listens on
TCP `127.0.0.1:6393`, receives `SessionInfo` messages from `ndcentral`, and writes
per-session `haptic_events.json` files to `/home/ubuntu/autocam/<session>/`. On every
fresh ignition-on event it may run a self-test (motor pulse + audio cue), and it runs a
periodic GPIO health check independent of ignition state. It reads its accessory serial
number from `accessory.db` and reports health status (`haptic_motor` section) to
`HealthStatsManager`.

**Process name:** `haptic_feedback` (`HPTC` label in `service_mon.log`)
**Log file:** `haptic_feedback.log` → `/home/ubuntu/.nddevice/log/haptic_feedback/*`
**Related log files:** `service_mon.log`, `ndcentral.log`, `cam_rec.log`, `audio.log`, `awsiot.log`, `power_mon.log`, `inference.log`
**Primary config sections:** `[haptic_feedback]`, `[haptic_motor_health_monitoring]`, `[GPIO]`, `[live_streaming]`
**Supported device types:** `bagheera3`, `octo` (all haptic tests skip on other device types)
**Accessory DB path:** `/home/ubuntu/.nddevice/accessory.db`

---


## Config-Driven Flow Activation

| Config Section                       | Config Key                              | Value            | Activates Flow(s)                              | Test Cases Affected                                      |
| ------------------------------------ | ---------------------------------------- | ---------------- | ----------------------------------------------- | ---------------------------------------------------------- |
| `[haptic_feedback]`                  | `enabled`                                | `1`               | Flow 1, 2 (health-check path), 4, 5, 7, 8       | All `TC_HAPTIC_*` except `TC_HAPTIC_4342`/`4343` (disabled-state tests) |
| `[haptic_feedback]`                  | `enabled`                                | `0`               | Flow 2 (disabled-state path), Flow 3 (GPIO removal) | `TC_HAPTIC_4342`, `TC_HAPTIC_4343`                      |
| `[haptic_motor_health_monitoring]`    | `is_active_monitoring_enabled`           | `true`            | Flow 2 (ignition-on + bootup GPIO checks)        | `TC_HAPTIC_4161`, `TC_HAPTIC_4162`, `TC_HAPTIC_4344`      |
| `[haptic_motor_health_monitoring]`    | `audio_feedback_if_active_monitoring_enabled` | `true`       | Flow 2 (audio cue path)                          | `TC_HAPTIC_4161`, `TC_HAPTIC_4344`                        |
| `[GPIO]`                             | *(haptic keys absent)*                   | —                 | Flow 3 (GPIO section removed)                    | `TC_HAPTIC_4343`                                          |
| `[hapticFeedback]`                   | `enable`                                 | *(read-only check)* | Flow 3 (config read-back)                     | `TC_HAPTIC_4358`                                          |
| `[live_streaming]`                   | `enabled`                                | `true`            | Flow 8 (livestreaming)                           | `TC_HAPTIC_4355`, `TC_HAPTIC_4356`, `TC_HAPTIC_4357`      |
| `[privacy_mode]` / `[upload_video]`  | *(pushed by test itself)*                | —                 | Flow 7 (alert session flow)                      | `TC_HAPTIC_4354`                                          |
| —                                     | —                                         | —                 | Flow 1, 4, 5 (always active)                     | `TC_HAPTIC_4157`, `TC_HAPTIC_4345`–`4353`                 |

**Rules:**
- `[haptic_feedback] enabled=1` is the primary gate for nearly all flows
- All haptic tests skip on device types other than `bagheera3`/`octo`
- Flow 6 (Metadata & Session) has no automated test case yet — always report `NOT_AUTOMATED`

---

## Cross-Service Dependencies

| Related Service    | Why                                                                     | Flow(s)                    |
| ------------------- | -------------------------------------------------------------------------- | -------------------------------------------- |
| `service_mon`       | Monitors `HPTC` start/stop/error events, drives crash respawn             | 1                                       |
| `bagheera` (ndcentral) | Sends `SessionInfo` messages; owns session lifecycle for VOD/alert flow | 7                                       |
| `cam_rec`            | Camera recording process — livestream pipeline, DMS camera resumption    | 4 (post-reboot), 8                 |
| `audio`              | Plays the ignition-on haptic health-check audio cue                      | 2                                       |
| `awsiot`              | Cloud reboot trigger, shadow keep-alive, livestream trigger, VOD command ack | 4 (`TC_HAPTIC_4349`), 7, 8   |
| `power_monitor`      | Orchestrates AWS reboot, crank shutdown, LPW cycle                        | 4                                       |
| `inference`           | NRT/analytics gate for alert-session VOD upload                          | 7                                       |
| `uploader` / `unifieduploader` | VOD upload after alert session                                  | 7                                       |
| `svc`                 | Keep-alive/watchdog; also a kill/restart target under test               | 4 (`TC_HAPTIC_4348`), 7              |
| `dmsAnalyticsClient`, `analyticsService` | Dependent-service health check only (status, not analytics output) | 1                                |
| `HealthStatsManager` | Receives `haptic_motor` health section                                   | 1, 2                                    |

---

## Flow Dependency Graph

```
boot → [haptic_feedback enabled?]
  └─ YES → [Flow 1: Service Status & Lifecycle] → SIGABRT respawn / systemctl control
               → [Flow 2: Audio & Ignition Behavior] → bootup GPIO check (always)
                                                     → ignition-on audio check (throttled 24h)
               → [Flow 5: Database Operations] → accessory.db ACCESSORY read
  └─ NO (enabled=0) → [Flow 2: disabled-state path] → GPIO held OFF, health check skipped
  └─ [GPIO] section removed → [Flow 3: Config] → clean boot without legacy keys

event (crash / kill / reboot) → [Flow 4: Stability & Reboot Recovery]
  ├─ cyclic reboot / bagheera kill / svc kill / camera-crash reboot / LPW / crank shutdown (local)
  └─ AWS reboot (cloud) → awsiot → power_monitor → haptic_feedback recovery + DMS camera resumes

alert triggered (cloud) → [Flow 7: Alert Session Flow] → svc/ndcentral/inference/uploader → haptic_feedback health

livestream triggered (cloud) → [Flow 8: Livestreaming] → cam_rec pipeline → haptic_feedback health during load

[Flow 6: Metadata & Session] → not yet automated (placeholder)
```

---

## Validation Instructions for the Agent

1. **Read device config** from `device_data/device_<ID>_config.ini`
2. **Check device type** — skip all `tests/haptic/` cases if device is NOT `bagheera3`
   or `octo`
3. **Determine active flows** using the Config-Driven Flow Activation table above
4. **Load the specific flow's skill file** (see Flow Index above) — each contains the
   detailed What-happens, Key-log-patterns, Test-cases table, and its own Validation
   Instructions. Do not rely on this index alone for log-pattern detail.
5. **For each active flow**, read the mapped pytest test files from `tests/haptic/`
   listed in that flow's table (use the **current** filename-based TC ID, not any ID
   embedded in a docstring or class name — those are historical/legacy and may not
   match the file's actual position in `functionality_map.py`)
6. **From each test file**, use docstrings (`"""STEP N — ..."""` /
   `"""PreCondition N — ..."""`) to understand what each step checks and which log
   pattern to search
7. **Log file paths:**
   - `haptic_feedback`: `/home/ubuntu/.nddevice/log/haptic_feedback/*`
   - `service_mon`: `/home/ubuntu/.nddevice/log/service_mon/*`
   - `ndcentral`: `/home/ubuntu/.nddevice/log/ndcentral/*`
   - `cam_rec`: `/home/ubuntu/.nddevice/log/cam_rec/*`
   - `audio`: `/home/ubuntu/.nddevice/log/audio/*`
   - `awsiot`: `/home/ubuntu/.nddevice/log/awsiot/*`
   - `power_mon`: `/home/ubuntu/.nddevice/log/power_mon/*`
   - `inference`: `/home/ubuntu/.nddevice/log/inference/*`
   - `accessory DB`: `/home/ubuntu/.nddevice/accessory.db`
8. **Report verdict** per test case: PASS / FAIL / NOT_TRIGGERED / NOT_AUTOMATED / NA
   (device type mismatch)
