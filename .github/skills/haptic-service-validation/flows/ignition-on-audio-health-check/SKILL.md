---
name: ignition-on-audio-health-check
description: "Use when: validating the ignition-on haptic health check path. Covers fresh ignition detection, 24-hour throttling, audio cue playback, motor-off confirmation, and health reporting to HealthStatsManager."
argument-hint: "device ID (e.g., /haptic-ignition-on-audio-health-check 440073)"
---

# Haptic — Flow 3: Ignition-ON Audio Health Check

## What happens

On a fresh ignition-on event, `haptic_feedback` decides whether to run its active
monitoring health check. When active monitoring and audio feedback are enabled, the
service triggers the health check, plays the configured audio cue, confirms the motor
returns to the idle GPIO state, and reports the result to `HealthStatsManager`. The
flow is throttled so a valid skip within 24 hours is also an acceptable outcome.

**When active:** `[haptic_feedback] enabled=1` and
`[haptic_motor_health_monitoring] is_active_monitoring_enabled=true`; audio cue also
requires `audio_feedback_if_active_monitoring_enabled=true`
**Frequency:** On fresh ignition-on events, subject to 24-hour throttling
**Cross-service impact:** `audio` plays the cue; `HealthStatsManager` receives the
health result
**Automated:** Yes
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Key evidence

### haptic_feedback.log

- `Ignition-ON detection: is_fresh_ignition_start=<0|1>`
- `Ignition-ON: triggering health check (>24hrs since last check)`
- `Ignition-ON: health check skipped (already checked within 24hrs)`
- `Motor OFF confirmed: input GPIO returned to HIGH (idle) after 700ms`
- `sending haptic health info to hs:`

### audio.log

- `Audio Playback done for <path> with status: 0`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4161` | `tests/haptic/test_tc_haptic_4161_haptic_audio_check.py` | ignition-on health check or valid 24-hour skip, audio cue, motor-off confirmation, HS reporting |

## Pass criteria

- The ignition-on decision path is logged
- Either the full health-check path runs successfully or the valid 24-hour skip path is logged
- If the full path runs, audio playback succeeds and motor-off confirmation is logged
- Health info is sent to `HealthStatsManager`

## Fail signals

- No valid ignition-on branch is logged
- Audio playback fails when the full health-check path runs
- Motor-off confirmation is missing after the health check
- Health reporting to `HealthStatsManager` is missing

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the ignition-on decision path is logged in `haptic_feedback.log`
3. Accept either the full health-check path or the valid 24-hour skip path
4. If the full path runs, confirm audio playback succeeds and motor-off confirmation is logged
5. Confirm health info is sent to `HealthStatsManager`