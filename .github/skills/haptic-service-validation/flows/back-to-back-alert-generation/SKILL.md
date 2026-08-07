---
name: back-to-back-alert-generation
description: "Use when: validating repeated alert generation in close succession. Covers whether the haptic path handles consecutive alerts correctly."
argument-hint: "device ID (e.g., /back-to-back-alert-generation 440073)"
---

# Haptic — Flow 40: Back-to-Back Alert Generation

## What happens

This flow validates whether the haptic path handles consecutive alerts generated in close
succession.

**When active:** Repeated-alert scenario
**Frequency:** Per repeated-alert validation
**Cross-service impact:** alert generation rate and haptic trigger handling
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | consecutive alerts are handled correctly |

## Pass criteria

- Consecutive alerts do not break the haptic trigger path

## Fail signals

- Later alerts are dropped or mishandled by the haptic path

## Validation instructions

1. Report this flow as not yet automated
2. Correlate each alert with its expected haptic response