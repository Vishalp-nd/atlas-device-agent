---
name: haptic-when-inward-camera-disabled
description: "Use when: validating haptic behavior when the inward camera is disabled. Covers the expectation that DMS-driven haptic should not trigger without inward-camera analytics."
argument-hint: "device ID (e.g., /haptic-when-inward-camera-disabled 440073)"
---

# Haptic — Flow 34: Haptic when Inward Camera Disabled

## What happens

When the inward camera is disabled, the DMS analytics path that normally produces drowsy
alerts is unavailable. This flow validates that haptic does not trigger from that absent
path.

**When active:** Inward camera disabled scenario
**Frequency:** Per camera-disable validation
**Cross-service impact:** camera availability, DMS analytics, `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | no DMS-driven haptic when inward camera is disabled |

## Pass criteria

- No DMS-driven haptic trigger occurs while inward camera is disabled

## Fail signals

- Haptic triggers from a DMS path despite inward camera being disabled

## Validation instructions

1. Report this flow as not yet automated
2. Confirm the inward camera disabled state before judging haptic behavior