---
name: api-audio-trigger-processing
description: "Use when: validating the API and audio-trigger processing path as its own atomic flow. Covers how trigger requests are interpreted and handed off to haptic behavior."
argument-hint: "device ID (e.g., /api-audio-trigger-processing 440073)"
---

# Haptic — Flow 49: API / Audio Trigger Processing

## What happens

This flow validates the API and audio-trigger processing path as its own target: how
trigger requests are interpreted and handed off to haptic behavior.

**When active:** Trigger-processing validation
**Frequency:** As needed for trigger-path analysis
**Cross-service impact:** API trigger source, audio trigger source, `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| Existing api-audio-trigger-processing bucket skill | trigger interpretation and handoff behavior |

## Pass criteria

- Trigger requests are interpreted correctly and handed off to the expected haptic path

## Fail signals

- Trigger requests are dropped, misinterpreted, or routed incorrectly

## Validation instructions

1. Use this flow when the question is about trigger-processing internals rather than a specific alert scenario
2. Keep it separate from DMS severe-drowsy and fallback flows