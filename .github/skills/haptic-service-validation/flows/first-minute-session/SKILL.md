---
name: first-minute-session
description: "Use when: validating early boot readiness of haptic_feedback. Covers the expectation that the service is up and running within 12 seconds from bootup."
argument-hint: "device ID (e.g., /first-minute-session 440073)"
---

# Haptic — Flow 25: First Minute Session

## What happens

The source material states that `haptic_feedback` should be up and running within 12
seconds from bootup. This is an early-readiness expectation rather than a full alert or
metadata-processing flow.

**When active:** Manual or confluence-backed boot-readiness review
**Frequency:** Boot-readiness validation only
**Cross-service impact:** service startup timing
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence session-behavior matrix | haptic_feedback is up within 12 seconds from bootup |

## Pass criteria

- `haptic_feedback` reaches running state within the expected boot window

## Fail signals

- `haptic_feedback` startup exceeds the expected boot window

## Validation instructions

1. Report this flow as not yet automated
2. Use boot timing evidence if asked to assess this flow manually