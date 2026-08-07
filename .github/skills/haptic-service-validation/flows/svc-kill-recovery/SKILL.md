---
name: svc-kill-recovery
description: "Use when: validating haptic_feedback resilience to an svc process kill and restart. Covers haptic_feedback remaining healthy after the svc disruption."
argument-hint: "device ID (e.g., /haptic-svc-kill-recovery 440073)"
---

# Haptic — Flow 11: SVC Kill Recovery

## What happens

`svc` is killed and restarted while `haptic_feedback` is expected to remain healthy.
The flow validates that the haptic service is unaffected by the `svc` disruption and is
active after the scenario completes.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per svc kill scenario
**Cross-service impact:** `svc`
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4348` | `tests/haptic/test_tc_haptic_4348_svc_kill_haptic_health_check.py` | haptic_feedback remains healthy after svc kill and restart |

## Pass criteria

- The svc disruption completes
- `haptic_feedback` remains or returns to `active`

## Fail signals

- `haptic_feedback` becomes unhealthy or inactive after the svc kill scenario

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the svc disruption completes
3. Confirm `haptic_feedback` remains or returns to `active`
4. Keep this flow separate from the generic service-lifecycle flow, which tests direct systemctl operations on haptic_feedback itself