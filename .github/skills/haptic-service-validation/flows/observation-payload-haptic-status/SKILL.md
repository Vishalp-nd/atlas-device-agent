---
name: observation-payload-haptic-status
description: "Use when: validating that haptic status is represented correctly in observation payloads. Covers payload-level reporting of haptic state."
argument-hint: "device ID (e.g., /observation-payload-haptic-status 440073)"
---

# Haptic — Flow 44: Observation Payload Haptic Status

## What happens

This flow validates that haptic status is represented correctly in observation payloads.

**When active:** Observation payload validation
**Frequency:** Per payload review
**Cross-service impact:** payload generation and haptic state reporting
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence payload checks | observation payload includes correct haptic status |

## Pass criteria

- Observation payload reflects the correct haptic status

## Fail signals

- Observation payload omits or misreports haptic status

## Validation instructions

1. Report this flow as not yet automated
2. Keep this payload-reporting flow separate from runtime trigger flows