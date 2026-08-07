---
name: supercap-events-active-haptic-trigger
description: "Use when: validating haptic behavior while supercap-related events are active. Covers whether active power events interfere with haptic triggering."
argument-hint: "device ID (e.g., /supercap-events-active-haptic-trigger 440073)"
---

# Haptic — Flow 41: Supercap Events During Active Haptic Trigger

## What happens

This flow validates whether supercap-related power events interfere with an active haptic
trigger path.

**When active:** Power-event overlap scenario
**Frequency:** Per supercap overlap validation
**Cross-service impact:** power events and `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | active haptic trigger remains correct during supercap events |

## Pass criteria

- Haptic trigger path remains correct during supercap-related events

## Fail signals

- Supercap events interrupt or corrupt the haptic trigger path

## Validation instructions

1. Report this flow as not yet automated
2. Correlate power-event timing with haptic trigger timing