---
name: hapticfeedback-enable-config-read-back
description: "Use when: validating direct config read-back of the hapticFeedback enable value. Covers querying the config value without requiring a service restart or log-based validation."
argument-hint: "device ID (e.g., /hapticfeedback-enable-config-read-back 440073)"
---

# Haptic — Flow 8: [hapticFeedback] Enable Config Read-back

## What happens

This flow validates that `[hapticFeedback] enable` is readable through the existing
device config query path. It is intentionally a simple read-back check and does not
depend on restarting `haptic_feedback` or grepping logs.

**When active:** On-demand config validation
**Frequency:** Whenever config read-back is needed
**Cross-service impact:** None
**Automated:** Yes
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4358` | `tests/haptic/test_tc_haptic_4358_haptic_feedback_enable_config_check.py` | direct read-back of `[hapticFeedback] enable` |

## Pass criteria

- `[hapticFeedback] enable` is readable through the existing config query path

## Fail signals

- The config value cannot be read
- The returned value does not match the expected configuration

## Validation instructions

1. Confirm the device config is accessible
2. Use the existing config query path to read `[hapticFeedback] enable`
3. Do not require service restart, reboot, or log evidence for this flow