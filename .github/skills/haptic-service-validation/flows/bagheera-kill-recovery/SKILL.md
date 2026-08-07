---
name: bagheera-kill-recovery
description: "Use when: validating haptic_feedback resilience to a bagheera process kill and restart. Covers haptic_feedback remaining healthy after the dependent process disruption."
argument-hint: "device ID (e.g., /haptic-bagheera-kill-recovery 440073)"
---

# Haptic — Flow 10: Bagheera Kill Recovery

## What happens

`bagheera` is killed and restarted while `haptic_feedback` is expected to remain
healthy. The flow validates that the haptic service is unaffected by this disruption and
returns to or remains in an active state afterward.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per bagheera kill scenario
**Cross-service impact:** `bagheera`
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4347` | `tests/haptic/test_tc_haptic_4347_bagheera_kill_haptic_health_check.py` | haptic_feedback remains healthy after bagheera kill and restart |

## Pass criteria

- The bagheera disruption completes
- `haptic_feedback` remains or returns to `active`

## Fail signals

- `haptic_feedback` becomes unhealthy or inactive after the bagheera kill scenario

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the bagheera disruption completes
3. Confirm `haptic_feedback` remains or returns to `active`
4. Use the haptic startup banner as supporting evidence if the service restarts during the scenario