---
name: gpio-disconnection-active-haptic-trigger
description: "Use when: validating behavior if GPIO disconnects during an active haptic trigger. Covers resilience of the trigger path across GPIO loss."
argument-hint: "device ID (e.g., /gpio-disconnection-active-haptic-trigger 440073)"
---

# Haptic — Flow 43: GPIO Disconnection During Active Haptic Trigger

## What happens

This flow validates resilience if GPIO disconnects during an active haptic trigger.

**When active:** GPIO-loss overlap scenario
**Frequency:** Per GPIO-loss validation
**Cross-service impact:** GPIO path and `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | active trigger path across GPIO disconnection |

## Pass criteria

- Trigger path remains correct or fails in a controlled way across GPIO loss

## Fail signals

- GPIO loss causes uncontrolled or silent failure of the active trigger path

## Validation instructions

1. Report this flow as not yet automated
2. Correlate GPIO-loss timing with haptic trigger timing