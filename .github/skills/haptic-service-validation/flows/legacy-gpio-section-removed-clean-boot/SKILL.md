---
name: legacy-gpio-section-removed-clean-boot
description: "Use when: validating clean boot behavior after removing legacy haptic GPIO keys from config. Covers the octo-only scenario where haptic_feedback is disabled and the device must still boot cleanly."
argument-hint: "device ID (e.g., /haptic-legacy-gpio-section-removed-clean-boot 440073)"
---

# Haptic — Flow 7: Legacy GPIO Section Removed (Clean Boot)

## What happens

All legacy haptic-related keys under `[GPIO]` are removed from the override config and
`[haptic_feedback] enabled=0` is applied. The device, specifically in the existing
automation's octo scope, must boot cleanly without those legacy keys being present.
This is a config-boundary and boot-stability check, not a runtime haptic-trigger check.

**When active:** During config-push and reboot validation
**Frequency:** Once per config change and reboot cycle
**Cross-service impact:** None beyond config parsing during boot
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4343` | `tests/haptic/test_tc_haptic_4343_haptic_gpio_section_removed.py` | clean boot after removing legacy GPIO keys and disabling haptic_feedback |

## Pass criteria

- The device boots cleanly after the config change
- No crash-loop or boot failure is introduced by removing the legacy GPIO keys

## Fail signals

- The device fails to boot cleanly after the config change
- The service or system enters a crash-loop tied to the missing legacy GPIO keys

## Validation instructions

1. Confirm device type is `octo`, since that is the scope called out by the existing test
2. Verify the device boots cleanly after the config change
3. Do not require a specific "GPIO section removed" log line; absence of boot failure is the pass condition
4. Keep this flow separate from the simple config read-back flow