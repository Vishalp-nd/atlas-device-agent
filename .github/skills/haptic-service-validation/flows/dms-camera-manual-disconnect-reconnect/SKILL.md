---
name: dms-camera-manual-disconnect-reconnect
description: "Use when: validating haptic behavior across manual DMS camera disconnect and reconnect. Covers recovery of the DMS-driven haptic path after camera restoration."
argument-hint: "device ID (e.g., /dms-camera-manual-disconnect-reconnect 440073)"
---

# Haptic — Flow 38: DMS Camera Manual Disconnect/Reconnect

## What happens

This flow validates that the DMS-driven haptic path behaves correctly across a manual DMS
camera disconnect and reconnect sequence.

**When active:** Manual DMS camera disconnect/reconnect scenario
**Frequency:** Per camera-recovery validation
**Cross-service impact:** DMS camera availability, analytics recovery, `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | DMS-driven haptic path recovers after camera reconnect |

## Pass criteria

- DMS-driven haptic path is absent while the camera is disconnected
- DMS-driven haptic path recovers after reconnect

## Fail signals

- Haptic path does not recover after camera reconnect

## Validation instructions

1. Report this flow as not yet automated
2. Separate the disconnected and reconnected phases when assessing evidence