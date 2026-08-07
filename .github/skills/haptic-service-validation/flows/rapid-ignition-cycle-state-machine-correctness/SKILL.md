---
name: rapid-ignition-cycle-state-machine-correctness
description: "Use when: validating haptic_feedback behavior across rapid ignition on/off/on cycles. Covers fresh-ignition detection and correct branching into either a full health check or a valid 24-hour skip path."
argument-hint: "device ID (e.g., /haptic-rapid-ignition-cycle-state-machine-correctness 440073)"
---

# Haptic — Flow 6: Rapid Ignition-Cycle State Machine Correctness

## What happens

Rapid ignition transitions must not corrupt the internal health-check state machine.
Each fresh ignition-on should either run the normal health-check path or take the valid
24-hour skip path. The important behavior is that the service remains logically
consistent under quick ignition cycling.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** During rapid ignition-cycle testing
**Cross-service impact:** `HealthStatsManager` and `audio` only if the full health-check
path runs
**Automated:** Yes
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Key evidence

### haptic_feedback.log

- `Ignition-ON detection: is_fresh_ignition_start=<0|1>`
- Either the full health-check sequence or
  `Ignition-ON: health check skipped (already checked within 24hrs)`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4344` | `tests/haptic/test_tc_haptic_4344_haptic_rapid_ign_audio_health_checks.py` | rapid ignition cycling preserves valid health-check state transitions |

## Pass criteria

- Ignition-on detection is logged during the rapid-cycle scenario
- Each fresh ignition-on reaches a valid branch: full health check or valid 24-hour skip

## Fail signals

- The state machine enters an invalid or missing branch during rapid ignition cycling
- Expected ignition-on detection logs are missing for the exercised cycle

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify ignition-on detection is logged for the rapid-cycle scenario
3. Accept either a full health-check run or a valid 24-hour skip
4. Fail only if neither valid branch appears or the state machine behaves inconsistently