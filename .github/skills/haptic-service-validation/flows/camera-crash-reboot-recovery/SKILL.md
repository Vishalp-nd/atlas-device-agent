---
name: camera-crash-reboot-recovery
description: "Use when: validating haptic_feedback recovery after a camera-crash-triggered reboot. Covers post-reboot service health and optional shutdown-reason corroboration in power_monitor logs."
argument-hint: "device ID (e.g., /camera-crash-reboot-recovery 440073)"
---

# Haptic — Flow 13: Camera-Crash Reboot Recovery

## What happens

This flow validates that `haptic_feedback` recovers after a reboot triggered by a camera
crash. The source bucket also notes an optional corroborating shutdown-reason string in
`power_mon.log`, and a known JSON parsing anomaly that should be treated as a secondary
log issue rather than an automatic flow failure.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per camera-crash reboot scenario
**Cross-service impact:** camera pipeline and `power_monitor`
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Key evidence

### power_mon.log

- `DBSTATE_SHUTDOWN_CAM_CRASH:REBOOT`

### haptic_feedback.log

- `######## Starting Haptic Service on tcp://127.0.0.1:6393 #####`
- `Waiting for haptic alert messages`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4350` | `tests/haptic/test_tc_haptic_4350_camera_crash_reboot_haptic_health_check.py` | haptic_feedback recovery after camera-crash reboot |

## Pass criteria

- The camera-crash reboot completes
- `haptic_feedback` returns to `active`
- Startup banner or ready-state logs confirm healthy respawn

## Fail signals

- `haptic_feedback` does not recover after the reboot
- The service remains inactive or unhealthy after the camera-crash scenario

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Optionally corroborate the shutdown reason in `power_mon.log`
3. Confirm `haptic_feedback` returns to `active`
4. Treat the known `JSON parsing error ... too big integer` anomaly as a secondary issue unless the test's own assertions fail