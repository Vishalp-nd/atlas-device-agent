---
name: sigabrt-crash
description: "Use when: validating haptic_feedback recovery from a SIGABRT crash. Covers service_mon error/start logs and post-respawn health of haptic_feedback plus its dependent services."
argument-hint: "device ID (e.g., /haptic-sigabrt-crash 440073)"
---

# Haptic — Flow 1: SIGABRT Crash Respawn

## What happens

`haptic_feedback` is intentionally crashed with `SIGABRT` (`kill -6`) and must be
respawned by the service supervision path. `service_mon` should log the error and the
subsequent restart of `HPTC`, and the service must return to `active`. The dependent
services checked by the existing automation must also report healthy after the respawn:
`dmsAnalyticsClient`, `audioPlayback`, `bagheera`, `cam_rec`, `uploader`, and
`analyticsService`.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** On crash injection only
**Cross-service impact:** `service_mon` supervises `HPTC`; dependent services are
verified for post-respawn health
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Key evidence

### service_mon.log

- `Service error: HPTC :`
- `Service started: HPTC : <epoch>`

### Service status checks

- `haptic_feedback` returns to `active`
- `dmsAnalyticsClient`, `audioPlayback`, `bagheera`, `cam_rec`, `uploader`, and
  `analyticsService` also report `active`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4157` | `tests/haptic/test_tc_haptic_4157_haptic_service_status.py` | SIGABRT crash, respawn, and dependent-service health |

## Pass criteria

- `service_mon.log` shows the crash and the subsequent `HPTC` restart
- `haptic_feedback` returns to `active`
- All six dependent services checked by the existing test report `active`

## Fail signals

- `Service error: HPTC :` appears without a matching restart
- `haptic_feedback` remains inactive after the crash
- Any dependent service checked by the test does not recover to `active`

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify `service_mon.log` shows the `Service error: HPTC :` line followed by
   `Service started: HPTC : <epoch>`
3. Confirm `haptic_feedback` returns to `active`
4. Confirm the six dependent services checked by the test also report `active`
5. Do not mix this flow with generic systemctl lifecycle checks; those belong in the
   separate service-lifecycle flow