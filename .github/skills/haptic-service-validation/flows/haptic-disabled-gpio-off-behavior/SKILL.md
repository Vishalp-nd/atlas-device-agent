---
name: haptic-disabled-gpio-off-behavior
description: "Use when: validating haptic_feedback behavior when disabled by config. Covers GPIO-off state at service start and explicit skipping of the ignition-on health-check path."
argument-hint: "device ID (e.g., /haptic-disabled-gpio-off-behavior 440073)"
---

# Haptic — Flow 5: Haptic Disabled GPIO-Off Behavior

## What happens

When `[haptic_feedback] enabled=0`, the service should keep the haptic GPIO OFF at
startup and explicitly skip the ignition-on health-check path. This validates the
disabled-state behavior rather than the normal active-monitoring path.

**When active:** `[haptic_feedback] enabled=0`
**Frequency:** On service start and ignition-on while disabled
**Cross-service impact:** None
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Key evidence

### haptic_feedback.log

- `USB_HUB_GPIO_1 set to 0`
- `Haptic GPIO is set to OFF at service start`
- `Ignition-ON detection: is_fresh_ignition_start=0`
- `Not a fresh ignition start, skipping ignition-ON health check`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4342` | `tests/haptic/test_tc_haptic_4342_haptic_disabled_audio_check.py` | disabled config keeps GPIO off and skips ignition-on health check |

## Pass criteria

- GPIO-off logs appear at service start
- The ignition-on health-check path is explicitly skipped

## Fail signals

- GPIO is not forced OFF at service start while disabled
- The service attempts the normal ignition-on health-check path despite being disabled

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the GPIO-off logs appear at service start
3. Verify the ignition-on health-check path is explicitly skipped
4. Do not require audio playback or health-success logs in this disabled-state flow