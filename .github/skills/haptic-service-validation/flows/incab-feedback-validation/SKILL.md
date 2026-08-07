---
name: incab-feedback-validation
description: "Use when: validating that both severe and moderate drowsy event codes trigger haptic feedback. Covers the event-code-specific trigger behavior documented in source material."
argument-hint: "device ID (e.g., /incab-feedback-validation 440073)"
---

# Haptic — Flow 30: Incab Feedback Validation

## What happens

This flow validates that both severe drowsy and moderate drowsy event codes trigger
haptic feedback, not just the severe case.

**When active:** DMS drowsy alert validation
**Frequency:** Per event-code validation
**Cross-service impact:** DMS alert generation and `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | severe and moderate drowsy event codes both trigger haptic feedback |

## Pass criteria

- Severe drowsy event code triggers haptic feedback
- Moderate drowsy event code triggers haptic feedback

## Fail signals

- One of the documented drowsy event codes fails to trigger haptic feedback

## Validation instructions

1. Report this flow as not yet automated
2. Validate both event-code paths separately when evidence is available