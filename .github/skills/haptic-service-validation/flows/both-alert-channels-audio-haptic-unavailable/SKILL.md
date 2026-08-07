---
name: both-alert-channels-audio-haptic-unavailable
description: "Use when: documenting the failure case where both audio and haptic alert channels are unavailable. Covers the combined unavailability scenario noted in source material."
argument-hint: "device ID (e.g., /both-alert-channels-audio-haptic-unavailable 440073)"
---

# Haptic — Flow 37: Both Alert Channels Unavailable

## What happens

This flow documents the combined failure case where both audio and haptic alert channels
are unavailable.

**When active:** Combined alert-channel failure scenario
**Frequency:** Rare failure analysis
**Cross-service impact:** audio path, haptic path, accessory state
**Automated:** No
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | combined audio+haptic unavailability scenario |

## Pass criteria

- Not established as a passing runtime behavior in the source material

## Fail signals

- Both alert channels are unavailable during a scenario that should still notify the driver

## Validation instructions

1. Report this flow as not yet automated
2. Treat this as a failure-analysis scenario, not a normal positive path