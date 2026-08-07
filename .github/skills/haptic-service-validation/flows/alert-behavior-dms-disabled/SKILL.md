---
name: alert-behavior-dms-disabled
description: "Use when: validating haptic behavior when DMS is disabled. Covers the expectation that DMS alert-driven haptic should not trigger."
argument-hint: "device ID (e.g., /alert-behavior-dms-disabled 440073)"
---

# Haptic — Flow 35: Alert Behavior when DMS Disabled

## What happens

When DMS is disabled, the DMS alert path is unavailable. This flow validates that haptic
does not trigger from a disabled DMS path.

**When active:** DMS disabled scenario
**Frequency:** Per DMS-disable validation
**Cross-service impact:** DMS enablement and `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | no DMS-driven haptic when DMS is disabled |

## Pass criteria

- No DMS-driven haptic trigger occurs while DMS is disabled

## Fail signals

- Haptic triggers from a DMS path despite DMS being disabled

## Validation instructions

1. Report this flow as not yet automated
2. Confirm DMS disabled state before judging haptic behavior