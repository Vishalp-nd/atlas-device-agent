---
name: haptic-audio-ignition-behavior
description: "Use when: validating haptic_feedback ignition-ON audio health check, bootup periodic GPIO/USB-hub health check, haptic-disabled GPIO-off behavior, and rapid ignition-cycle health-check state machine correctness from device logs. Covers periodic health monitoring, motor recovery, ignition-on health check, and 48-hour warning log patterns."
argument-hint: "device ID (e.g., /haptic-audio-ignition-behavior 440073)"
---

# Haptic — Audio & Ignition Behavior (Flow 2)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> `functionality_map.py`'s `"Audio & Ignition Behavior"` bucket.

## What happens

On a fresh ignition-on event, `haptic_feedback` decides whether to run a haptic health
check with an audible cue (throttled to once per 24h). If
`[haptic_motor_health_monitoring]` is enabled with audio feedback, it drives the motor,
plays `active_monitoring_audio_file`, confirms motor-off via GPIO read-back, and reports
success to health stats. On every boot, `periodic_health_monitor` runs its first check
independent of ignition and confirms the USB hub is detected (`"USB hub detected"` on
bagheera3 vs `"USB hub device 1 connected"` on octo) with no GPIO read errors. When
`[haptic_feedback] enabled=0`, the GPIO is held OFF at service start and the ignition-ON
health check path is explicitly skipped (`is_fresh_ignition_start=0` /
`Not a fresh ignition start, skipping ignition-ON health check`). Rapid ignition
on/off/on cycling must not corrupt the health-check state machine — each fresh
ignition-on either runs the full check or hits a valid 24h-skip path.

**When active:** `[haptic_feedback] enabled=1`; audio cue requires
`[haptic_motor_health_monitoring] is_active_monitoring_enabled=true` +
`audio_feedback_if_active_monitoring_enabled=true`
**Frequency:** Ignition-on audio check: once per fresh ignition-on event (throttled 24h);
bootup GPIO check: once per boot; disabled-state checks: on boot with `enabled=0`
**Cross-service impact:** `audio` service plays the health-check cue;
`HealthStatsManager` receives the haptic health trigger result
**is_cloud_dependent:** 0 **is_analytics_dependent:** 0

## Key log patterns (haptic_feedback.log)

- `Ignition-ON detection: is_fresh_ignition_start=<0|1>`
- `Not a fresh ignition start, skipping ignition-ON health check`
- `Ignition-ON: health check skipped (already checked within 24hrs)`
- `Motor OFF confirmed: input GPIO returned to HIGH (idle) after 700ms`
- `sending haptic health info to hs:` *(must contain `trigger_status` and `success`)*
- `Periodic health: output_state=0, input_gpio=1`
- `USB hub detected` *(bagheera3)* / `USB hub device 1 connected` *(octo)*
- `USB_HUB_GPIO_1 set to 0` / `Haptic GPIO is set to OFF at service start` *(disabled config)*

## Key log patterns (audio.log)

- `Audio Playback done for <path to .wav> with status: 0`

## Test cases that validate this flow

| Test Case ID    | Python File                                                                 | What it checks                                                              |
| --------------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `TC_HAPTIC_4161`| `tests/haptic/test_tc_haptic_4161_haptic_audio_check.py`                    | Ignition on/off cycle → fresh-ignition health check (or valid 24h skip) with audio cue, motor-off confirmation, HS success |
| `TC_HAPTIC_4162`| `tests/haptic/test_tc_haptic_4162_haptic_check_upon_bootup.py`              | Bootup periodic health check: output_state/input_gpio, USB hub detected + GPIO set, no GPIO read errors |
| `TC_HAPTIC_4342`| `tests/haptic/test_tc_haptic_4342_haptic_disabled_audio_check.py`          | `enabled=0` → GPIO held OFF at start, ignition-ON health check explicitly skipped |
| `TC_HAPTIC_4344`| `tests/haptic/test_tc_haptic_4344_haptic_rapid_ign_audio_health_checks.py`  | Rapid ignition on/off/on → Ignition-ON detection logged, health-check-or-valid-skip branch verified |

## Related confluence-documented behavior (DQA — Haptic Module Software Functional Checks)

- **"Post Ignition Analytics Seconds"** (PASS) — haptic behavior during the
  post-ignition analytics window follows the configured post-ignition-analytics-seconds
  value; useful context if a TC exercises ignition timing edge cases beyond the
  24h-throttle window above.
- **"Session when Ignition LOW"** (PASS) — haptic remains OFF because analytics does
  not run during ignition-OFF, so no DMS drowsy/audio events are produced. This is a
  higher-level (DMS-analytics-gated) OFF state, distinct from this flow's
  service-start GPIO-OFF check (`TC_HAPTIC_4342`) — don't conflate the two when
  explaining *why* the motor is OFF.

---

## haptic_feedback Critical Info Codes (send_err_msg)

The presence of any of these codes in `haptic_feedback.log` is a signal to flag,
except the explicit success/skip codes.

| Code                                              | Meaning                                                        | Normal or Failure?                                  |
| -------------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------- |
| `SM_E_HPTC_LOG_INIT_FAIL`                          | Logger init or config parse failure                              | **Failure** — should not occur                        |
| `SM_E_HPTC_SERIAL_NUM_LOAD_FAIL`                   | Haptic serial number couldn't be loaded from accessories DB      | **Failure** — should not occur                        |
| `SM_E_HPTC_SET_GPIO_FAILED`                        | GPIO set (high or low) API call failed                           | **Failure** — should not occur                        |
| `SM_E_HPTC_GET_GPIO_FAILED`                        | GPIO get (read) API call failed                                  | **Failure** — should not occur                        |
| `SM_E_HPTC_USB_HUB_NOT_CONNECTED`                  | USB Hub not detected at start                                    | **Failure** — should not occur                        |
| `SM_E_HPTC_HEALTH_SUCCESS`                         | Health check passed                                              | Normal (expected, not a failure)                      |
| `SM_E_HPTC_HEALTH_SKIPPED`                         | Health check skipped (not paired or conditions not met)          | Normal/expected depending on config — verify context  |
| `SM_E_HPTC_FAULT_DETECTED`                         | Motor health fault detected                                      | **Failure** — should not occur                        |
| `SM_E_HPTC_FAULT_PERSISTENT`                       | Motor fault persisted after max retries                          | **Failure** — should not occur                        |
| `SM_E_HPTC_NO_HEALTH_48HR`                         | No health check reported in 48+ hours                            | **Failure** — should not occur                        |
| `SM_E_HPTC_TRIGGER_FAILED_REASON_LATENCY`          | Trigger skipped due to latency exceeding threshold                | **Failure** — should not occur                        |
| `SM_E_HPTC_JSON_PARSE_FAIL`                        | JSON parse error or unknown message type                         | **Failure** — should not occur                        |
| `SM_E_HPTC_JSON_SESSION_NAME_MISMATCH`             | Session name mismatch between ZMQ msg and current session        | **Failure** — should not occur                        |
| `SM_E_HPTC_EVENTS_METADATA_WRITE_FAIL`             | Failed to write `haptic_events.json`                              | **Failure** — should not occur                        |

**Validation guidance:** Grep `haptic_feedback.log` for the `SM_E_HPTC_*` prefix. Flag
any code besides `SM_E_HPTC_HEALTH_SUCCESS` (and `SM_E_HPTC_HEALTH_SKIPPED` when
contextually expected) as an anomaly.

---

## haptic_feedback Periodic Health Monitoring Log Messages

| Level  | Log Message                                                                                          | Normal or Failure?                                              |
| ------ | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| LOG_E  | `Periodic health: failed to read input GPIO, error: %d`                                              | **Failure** — GPIO read API call failed                            |
| LOG_I  | `Periodic health: output_state=%d, input_gpio=%d`                                                    | Normal — routine status line                                       |
| LOG_I  | `Periodic health: output HIGH, motor running — normal during alert`                                  | Normal — expected during an active alert                           |
| LOG_W  | `Periodic health: fault detected - output HIGH but motor not responding (input idle)`                | **Failure** — motor not responding while driven                    |
| LOG_W  | `Periodic health recovery (both HIGH): retry %d/%d`                                                  | **Failure path** — recovery attempt in progress                    |
| LOG_E  | `Periodic health recovery (both HIGH): failed to set output LOW, err: %d`                             | **Failure** — recovery step failed                                 |
| LOG_E  | `Periodic health recovery (both HIGH): failed to set output HIGH, err: %d`                            | **Failure** — recovery step failed                                 |
| LOG_E  | `Periodic health recovery (both HIGH): failed to read input GPIO, err: %d`                            | **Failure** — recovery step failed                                 |
| LOG_I  | `Periodic health recovery (both HIGH): motor responding after %d attempt(s)`                          | Normal — recovery succeeded, but indicates a prior fault occurred  |
| LOG_W  | `Periodic health recovery (both HIGH): fault persists after attempt %d/%d (input still HIGH/idle)`    | **Failure** — recovery attempt did not clear the fault              |
| LOG_E  | `Periodic health: motor not responding - fault persistent after %d recovery attempts`                | **Failure** — fault persisted through all retries (terminal)       |
| LOG_W  | `Periodic health: fault detected - both output and input are LOW (motor stuck vibrating)`             | **Failure** — motor stuck vibrating                                 |
| LOG_W  | `Periodic health recovery (both LOW): retry %d/%d`                                                    | **Failure path** — recovery attempt in progress                    |
| LOG_E  | `Periodic health recovery (both LOW): failed to set output HIGH, err: %d`                             | **Failure** — recovery step failed                                 |
| LOG_E  | `Periodic health recovery (both LOW): failed to set output LOW, err: %d`                              | **Failure** — recovery step failed                                 |
| LOG_E  | `Periodic health recovery (both LOW): failed to read input GPIO, err: %d`                             | **Failure** — recovery step failed                                 |
| LOG_I  | `Periodic health recovery (both LOW): fault cleared after %d attempt(s)`                              | Normal — recovery succeeded, but indicates a prior fault occurred  |
| LOG_W  | `Periodic health recovery (both LOW): fault persists after attempt %d/%d (input still LOW/running)`   | **Failure** — recovery attempt did not clear the fault              |
| LOG_E  | `Periodic health: motor stuck vibrating - fault persistent after %d recovery attempts`                | **Failure** — fault persisted through all retries (terminal)       |

**Validation guidance:** Grep `haptic_feedback.log` for `Periodic health` lines. Flag
any `LOG_W`/`LOG_E` occurrence — including transient recovery retries that eventually
clear — since the corresponding `SM_E_HPTC_FAULT_DETECTED`/`SM_E_HPTC_FAULT_PERSISTENT`
codes are typically emitted alongside these.

---

## haptic_feedback Ignition-ON Health Check Log Messages

| Level  | Log Message                                                                                                     | Normal or Failure?                                              |
| ------ | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| LOG_E  | `get_reset_wake_reason failed, err: unable to read wake reason`                                                   | **Failure** — could not determine wake/reset reason                |
| LOG_I  | `pow_on_off_reason = 0x%X, reason_str = %s`                                                                        | Normal — routine status line                                       |
| LOG_I  | `Wake reason analysis: ignition=%d, sw_reboot=%d, watchdog=%d`                                                     | Normal — routine status line                                       |
| LOG_I  | `Ignition-ON health check skipped (hub=%d, paired=%d, active_monitoring=%d, device_obj=%p)`                       | Normal — expected when preconditions aren't met                    |
| LOG_I  | `Ignition-ON detection: is_fresh_ignition_start=%d`                                                                | Normal — routine status line                                       |
| LOG_I  | `Not a fresh ignition start, skipping ignition-ON health check`                                                   | Normal — expected on sw_reboot/watchdog resets                     |
| LOG_I  | `Ignition-ON: health check skipped (already checked within 24hrs)`                                                | Normal — expected throttling behavior                              |
| LOG_I  | `Playing health check audio: %s`                                                                                  | Normal — routine status line                                       |
| LOG_E  | `Failed to send audio play request for health check`                                                              | **Failure** — audio cue request failed                             |
| LOG_I  | `Ignition-ON: triggering health check (>24hrs since last check)`                                                  | Normal — routine status line                                       |
| LOG_I  | `Ignition-ON: health check trigger completed in %lld ms (expected ~%dms per attempt, max %d retries)`             | Normal — routine status line                                       |

**Validation guidance:** Grep `haptic_feedback.log` for `Ignition-ON` /
`get_reset_wake_reason` / `pow_on_off_reason` / `Wake reason analysis` lines. Flag the
two `LOG_E` cases as anomalies; the rest describe the fresh-ignition-start decision path.

---

## haptic_feedback 48-Hour No-Health-Check Warning

| Level  | Log Message                                              | Normal or Failure?                                    |
| ------ | ----------------------------------------------------------- | ----------------------------------------------------------- |
| LOG_W  | `No haptic health check in 48+ hours (%lld ms ago)`        | **Failure** — health monitoring has stalled for 48+ hours |

**Validation guidance:** Grep `haptic_feedback.log` for `No haptic health check in 48` —
any match should be flagged and cross-checked against `SM_E_HPTC_NO_HEALTH_48HR`.

---

## Haptic Health Check Flow

> Consolidates the tables above (Critical Info Codes, Periodic Health Monitoring,
> Ignition-ON Health Check, 48-Hour Warning) into a single end-to-end flow, from boot
> through result reporting.

```
┌─────────────────────────────────────────────────────────────┐
│                        BOOT / STARTUP                        │
├─────────────────────────────────────────────────────────────┤
│ 1. Config loaded (is_active_monitoring_enabled, audio_feedback) │
│ 2. Load cached health check timestamp                        │
│ 3. Read power-on/off reason & wake reason analysis           │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                 PERIODIC HEALTH MONITORING                    │
│              (runs continuously in background)                │
├─────────────────────────────────────────────────────────────┤
│ • Reads output_state and input_gpio periodically             │
│                                                              │
│ Normal states:                                               │
│   output=LOW, input=LOW  → Motor idle (healthy)              │
│   output=HIGH, input=HIGH → Motor running during alert       │
│                                                              │
│ Fault states:                                                │
│   output=HIGH, input=LOW → Motor not responding              │
│   output=LOW, input=LOW  → Motor stuck vibrating              │
│                                                              │
│ On GPIO read failure → LOG_E (error code, e.g. -6)           │
└──────────────────────────┬──────────────────────────────────┘
                           │ (fault detected)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    RECOVERY MECHANISM                         │
├─────────────────────────────────────────────────────────────┤
│ Both HIGH (motor not responding):                            │
│   1. Set output LOW → wait → set output HIGH                 │
│   2. Read input GPIO to check if motor responds              │
│   3. Retry up to N attempts                                  │
│   4. If recovered → "motor responding after X attempt(s)"    │
│   5. If failed → "fault persistent after X recovery attempts"│
│                                                              │
│ Both LOW (motor stuck vibrating):                            │
│   1. Set output HIGH → wait → set output LOW                 │
│   2. Read input GPIO to check if fault clears                │
│   3. Retry up to N attempts                                  │
│   4. If cleared → "fault cleared after X attempt(s)"         │
│   5. If failed → "motor stuck vibrating - fault persistent"  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                IGNITION-ON HEALTH CHECK                       │
│            (triggered on each ignition start)                 │
├─────────────────────────────────────────────────────────────┤
│ 1. Detect ignition-ON event                                  │
│                                                              │
│ Skip conditions:                                             │
│   • Hub not paired / active monitoring disabled              │
│   • Not a fresh ignition start                               │
│   • Already checked within 24 hours                          │
│                                                              │
│ Trigger conditions:                                          │
│   • >24 hrs since last health check → trigger check          │
│   • >48 hrs since last check → WARNING logged                │
│                                                              │
│ 2. (Optional) Play health check audio                        │
│ 3. Send haptic trigger request (with retry if enabled)       │
│ 4. Wait for motor feedback within timeout                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   RESULT REPORTING                            │
├─────────────────────────────────────────────────────────────┤
│ Based on motor response:                                     │
│                                                              │
│ SM_E_HPTC_HEALTH_SUCCESS  → Motor responded correctly         │
│ SM_E_HPTC_FAULT_DETECTED  → No feedback from motor            │
│ SM_E_HPTC_HEALTH_SKIPPED  → Check skipped (conditions not met)│
│                                                              │
│ → Send health info to HS (HealthStatsManager)                 │
└─────────────────────────────────────────────────────────────┘
```

**Validation guidance:** Use this flow to interpret log sequences holistically —
e.g., a `Periodic health` fault line followed by recovery-retry lines and an eventual
"responding"/"cleared" line is a **recovered** fault (still worth flagging, per the
Periodic Health Monitoring table), while the same sequence ending in a "persistent"
line is a **terminal failure**. Ties directly into
[Flow 1: Service Status & Lifecycle](../service-status-lifecycle/SKILL.md).

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Ignition-ON Audio Health Check",
      "description": "Fresh ignition-on event runs the throttled (24h) haptic health check with audio cue, motor-off GPIO read-back confirmation, and success reported to HealthStatsManager.",
      "flow_skill_path": ".github/skills/haptic-service-validation/audio-ignition-behavior/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/service-status-lifecycle/SKILL.md"]
    },
    {
      "name": "Bootup Periodic GPIO/USB Hub Health Check",
      "description": "On every boot, periodic_health_monitor runs its first check independent of ignition and confirms the USB hub is detected with no GPIO read errors.",
      "flow_skill_path": ".github/skills/haptic-service-validation/audio-ignition-behavior/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/service-status-lifecycle/SKILL.md"]
    },
    {
      "name": "Haptic Disabled GPIO-Off Behavior",
      "description": "With [haptic_feedback] enabled=0, GPIO is held OFF at service start and the ignition-ON health check path is explicitly skipped.",
      "flow_skill_path": ".github/skills/haptic-service-validation/audio-ignition-behavior/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Rapid Ignition-Cycle State Machine Correctness",
      "description": "Rapid ignition on/off/on cycling must not corrupt the health-check state machine \u2014 each fresh ignition-on either runs the full check or hits a valid 24h-skip path.",
      "flow_skill_path": ".github/skills/haptic-service-validation/audio-ignition-behavior/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/service-status-lifecycle/SKILL.md"]
    }
  ]
}
```

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. For `TC_HAPTIC_4161`/`4344` (ignition-on audio checks): both the full health-check
   log sequence AND a valid 24h-skip log are acceptable PASS outcomes — only fail if
   neither path's logs appear
3. For `TC_HAPTIC_4162` (bootup check): confirm the device-type-specific USB hub
   string (`USB hub detected` vs `USB hub device 1 connected`) matches the actual
   device type under test
4. For `TC_HAPTIC_4342` (disabled state): confirm GPIO-OFF logs appear at service
   start AND the ignition-ON health check is explicitly skipped — do not require the
   full health-check sequence
5. Grep for `SM_E_HPTC_*` codes, `Periodic health` lines, `Ignition-ON`/
   `get_reset_wake_reason`/`pow_on_off_reason` lines, and `No haptic health check in 48`
   — flag anomalies per the tables above regardless of whether the specific test
   case's own assertions check for them
