---
name: cyclic-reboot-recovery
description: "Use when: validating haptic_feedback recovery after a cyclic reboot. Covers service return to active state and healthy startup after the reboot cycle completes."
argument-hint: "device ID (e.g., /haptic-cyclic-reboot-recovery 440073)"
---

# Haptic — Flow 9: Cyclic Reboot Recovery

## What happens

After a cyclic reboot, `haptic_feedback` must come back to a healthy active state. The
existing source bucket also treats the startup banner in `haptic_feedback.log` as a
useful sign that the service has respawned correctly.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per cyclic reboot scenario
**Cross-service impact:** None beyond reboot orchestration
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Key evidence

### haptic_feedback.log

- `######## Starting Haptic Service on tcp://127.0.0.1:6393 #####`
- `Waiting for haptic alert messages`

### Service status

- `haptic_feedback` returns to `active`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4346` | `tests/haptic/test_tc_haptic_4346_cyclic_reboot_haptic_health_check.py` | haptic_feedback recovery after cyclic reboot |

## Pass criteria

- The reboot completes successfully
- `haptic_feedback` returns to `active`
- The startup banner or ready-state logs confirm healthy respawn

## Fail signals

- `haptic_feedback` does not return after the reboot
- The service remains inactive or unhealthy after the reboot cycle

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the reboot completes and `haptic_feedback` returns to `active`
3. Use the startup banner and waiting log as supporting evidence of healthy respawn