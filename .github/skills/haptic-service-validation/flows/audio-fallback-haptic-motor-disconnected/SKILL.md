---
name: audio-fallback-haptic-motor-disconnected
description: "Use when: validating fallback behavior when the haptic motor is disconnected. Covers the expectation that audio remains the available alert channel."
argument-hint: "device ID (e.g., /audio-fallback-haptic-motor-disconnected 440073)"
---

# Haptic — Flow 36: Audio Fallback when Haptic Motor Disconnected

## What happens

If the haptic motor is disconnected, the haptic channel is unavailable. This flow
validates that audio remains the fallback alert channel.

**When active:** Haptic motor disconnected scenario
**Frequency:** Per fallback validation
**Cross-service impact:** accessory state and audio alert path
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | audio remains available when haptic motor is disconnected |

## Pass criteria

- Audio alert path remains available when haptic motor is disconnected

## Fail signals

- Both haptic and audio alert channels are unavailable after motor disconnection

## Validation instructions

1. Report this flow as not yet automated
2. Keep this fallback case separate from the both-channels-unavailable case below