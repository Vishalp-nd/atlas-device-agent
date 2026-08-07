---
name: dual-livestream-haptic-health-check
description: "Use when: validating haptic_feedback health during or after dual livestreaming. Covers service stability while dual livestream is active."
argument-hint: "device ID (e.g., /dual-livestream-haptic-health-check 440073)"
---

# Haptic — Flow 47: Dual Livestream Haptic Health Check

## What happens

This flow validates that `haptic_feedback` remains healthy during or after dual
livestreaming.

**When active:** Dual livestream scenario
**Frequency:** Per dual livestream validation
**Cross-service impact:** livestream path and `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** Yes
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence livestream checks | haptic service health during dual livestream |

## Pass criteria

- `haptic_feedback` remains healthy during or after dual livestream

## Fail signals

- Dual livestream destabilizes `haptic_feedback`

## Validation instructions

1. Report this flow as not yet automated
2. Keep this as a service-health-under-livestream flow, not a motor-trigger flow