---
name: crank-shutdown-recovery
description: "Use when: validating haptic_feedback recovery across a crank-based shutdown and restart. Covers service return to active state after the crank shutdown path completes."
argument-hint: "device ID (e.g., /crank-shutdown-recovery 440073)"
---

# Haptic — Flow 15: Crank Shutdown Recovery

## What happens

This flow validates that `haptic_feedback` recovers across a crank-based shutdown and
restart. After the crank path completes, the service should return to a healthy active
state.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per crank shutdown scenario
**Cross-service impact:** crank and shutdown orchestration
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4352` | `tests/haptic/test_tc_haptic_4352_crank_shutdown_haptic_health_check.py` | haptic_feedback recovery across crank shutdown |

## Pass criteria

- The crank shutdown and restart complete
- `haptic_feedback` returns to `active`

## Fail signals

- `haptic_feedback` does not recover after the crank shutdown path
- The service remains inactive or unhealthy after restart

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the crank shutdown path completes
3. Confirm `haptic_feedback` returns to `active`