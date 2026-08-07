---
name: session-when-ignition-low
description: "Use when: validating haptic behavior while ignition is low. Covers the expectation that analytics does not run and haptic remains OFF."
argument-hint: "device ID (e.g., /session-when-ignition-low 440073)"
---

# Haptic — Flow 23: Session when Ignition LOW

## What happens

When ignition is low, the source material states that analytics does not run, so no DMS
drowsy or audio-trigger events are produced. As a result, haptic remains OFF.

**When active:** Manual or confluence-backed session review
**Frequency:** Session-behavior validation only
**Cross-service impact:** analytics gating
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence session-behavior matrix | haptic remains OFF when ignition is low |

## Pass criteria

- No haptic trigger occurs while ignition is low
- No analytics-driven DMS alert path is present for the session

## Fail signals

- Haptic triggers despite ignition-low conditions

## Validation instructions

1. Report this flow as not yet automated
2. Use session evidence to confirm analytics did not run and haptic stayed OFF