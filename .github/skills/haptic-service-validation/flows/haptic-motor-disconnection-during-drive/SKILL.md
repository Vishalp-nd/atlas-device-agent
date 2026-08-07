---
name: haptic-motor-disconnection-during-drive
description: "Use when: validating behavior when the haptic motor disconnects during an active drive. Covers loss of the haptic channel during runtime."
argument-hint: "device ID (e.g., /haptic-motor-disconnection-during-drive 440073)"
---

# Haptic — Flow 39: Haptic Motor Disconnection During Drive

## What happens

This flow validates runtime behavior when the haptic motor disconnects during an active
drive.

**When active:** Runtime accessory-failure scenario
**Frequency:** Per runtime disconnection validation
**Cross-service impact:** accessory state and alert delivery path
**Automated:** No
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | runtime haptic motor disconnection scenario |

## Pass criteria

- Not fully established in the source material beyond identifying the scenario

## Fail signals

- Haptic channel is lost during drive without expected fallback or recovery behavior

## Validation instructions

1. Report this flow as not yet automated
2. Treat this as a runtime failure-analysis scenario unless stronger evidence is available