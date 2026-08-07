---
name: haptic-service-restart-active-trigger
description: "Use when: validating behavior if haptic_feedback restarts during an active trigger scenario. Covers resilience of the trigger path across service restart."
argument-hint: "device ID (e.g., /haptic-service-restart-active-trigger 440073)"
---

# Haptic — Flow 42: Haptic Service Restart During Active Trigger

## What happens

This flow validates resilience if `haptic_feedback` restarts during an active trigger
scenario.

**When active:** Service-restart overlap scenario
**Frequency:** Per restart-resilience validation
**Cross-service impact:** `haptic_feedback` lifecycle and active trigger handling
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | active trigger path across haptic service restart |

## Pass criteria

- Trigger path remains correct or recovers cleanly across service restart

## Fail signals

- Service restart causes loss or corruption of the active trigger path

## Validation instructions

1. Report this flow as not yet automated
2. Separate pre-restart and post-restart evidence when assessing the flow