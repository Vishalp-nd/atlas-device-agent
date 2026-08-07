---
name: haptic-config
description: "Use when: validating haptic_feedback config-boundary behavior from device logs. Covers legacy [GPIO] haptic key removal with the service disabled (clean boot) and [hapticFeedback] enable config value read-back."
argument-hint: "device ID (e.g., /haptic-config 440073)"
---

# Haptic — Config (Flow 3)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> `functionality_map.py`'s `"Config"` bucket.

## What happens

Two config-boundary scenarios: (1) all legacy `[GPIO]` haptic keys
(`action_direction_Haptic_Seat_In/Out`, `action_expander_*`, `action_port_*`,
`action_trigger_state_*`) are removed from the override config and `[haptic_feedback]`
is set `enabled=0` — the device must boot cleanly without those keys present; (2) a
plain config read-back confirms `[hapticFeedback] enable` is queryable via
`device.check_config_value`, independent of any service restart.

**When active:** On-demand config validation; no continuous background flow
**Frequency:** Once per config push/reboot cycle
**Cross-service impact:** None outside `haptic_feedback` config parsing
**is_cloud_dependent:** 0 **is_analytics_dependent:** 0

## Test cases that validate this flow

| Test Case ID    | Python File                                                                 | What it checks                                                              |
| --------------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `TC_HAPTIC_4343`| `tests/haptic/test_tc_haptic_4343_haptic_gpio_section_removed.py`          | Boots cleanly (octo only) after all `[GPIO]` haptic keys removed and `haptic_feedback` disabled |
| `TC_HAPTIC_4358`| `tests/haptic/test_tc_haptic_4358_haptic_feedback_enable_config_check.py`  | `[hapticFeedback] enable` config value is readable via `check_config_value` |

## Related confluence reference

The DQA Confluence space has a dedicated broader configuration page,
**"Configuration Tests - DMS + Haptic Motor"**, linked from the "Haptic Module –
Software Functional Checks" page. It was not fetched as part of this skill (separate
page ID) — if deeper DMS+haptic config interaction validation is needed beyond the two
TCs above, fetch that page directly via the `jira-confluence-fetch` skill.

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Legacy GPIO Section Removed (Clean Boot)",
      "description": "All legacy [GPIO] haptic keys removed and [haptic_feedback] set enabled=0 \u2014 device (octo only) must boot cleanly without those keys present.",
      "flow_skill_path": ".github/skills/haptic-service-validation/config/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "[hapticFeedback] Enable Config Read-back",
      "description": "Plain config read-back confirms [hapticFeedback] enable is queryable via device.check_config_value, independent of any service restart.",
      "flow_skill_path": ".github/skills/haptic-service-validation/config/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    }
  ]
}
```

## Validation Instructions for the Agent

1. Confirm device type is `octo` for `TC_HAPTIC_4343` (bagheera3 is not in scope for
   this specific TC per its own device-type precondition)
2. For `TC_HAPTIC_4343`: verify the device boots cleanly (no crash-loop) after the
   `[GPIO]` section's haptic keys are absent — do not expect any specific "GPIO
   section missing" log line, absence of a crash is the pass criterion
3. For `TC_HAPTIC_4358`: this is a pure config read-back — no service restart or log
   grep is required, just confirm `check_config_value` returns the expected value
