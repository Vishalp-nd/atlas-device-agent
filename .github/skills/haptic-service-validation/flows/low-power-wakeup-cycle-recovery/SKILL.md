---
name: low-power-wakeup-cycle-recovery
description: "Use when: validating haptic_feedback recovery across a full low-power-wakeup cycle. Covers service return to active state after the LPW path completes."
argument-hint: "device ID (e.g., /low-power-wakeup-cycle-recovery 440073)"
---

# Haptic — Flow 14: Low-Power Wakeup Cycle Recovery

## What happens

This flow validates that `haptic_feedback` survives a full low-power-wakeup cycle and
returns to a healthy active state once the device completes the LPW path.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per LPW scenario
**Cross-service impact:** low-power and wakeup orchestration
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4351` | `tests/haptic/test_tc_haptic_4351_low_power_wakeup_haptic_health_check.py` | haptic_feedback recovery across LPW |

## Pass criteria

- The LPW cycle completes
- `haptic_feedback` returns to `active`

## Fail signals

- `haptic_feedback` does not recover after the LPW cycle
- The service remains inactive or unhealthy after wakeup

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the LPW cycle completes
3. Confirm `haptic_feedback` returns to `active`