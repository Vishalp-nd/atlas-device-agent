---
name: metadata-leftovers-session
description: "Use when: validating haptic metadata behavior for a metadata-leftover session. Covers the expectation that the haptic_event section remains empty in inference data."
argument-hint: "device ID (e.g., /metadata-leftovers-session 440073)"
---

# Haptic — Flow 20: Metadata Leftovers Session

## What happens

For a metadata-leftover session, the expected behavior from the source material is that
the `haptic_event` section remains empty under inference data.

**When active:** Manual or confluence-backed metadata review
**Frequency:** Edge-case session validation only
**Cross-service impact:** inference data and session metadata
**Automated:** No
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence session-behavior matrix | metadata-leftover file should have empty `haptic_event` section |

## Pass criteria

- The `haptic_event` section is empty in the relevant inference data

## Fail signals

- The `haptic_event` section is populated when it should be empty

## Validation instructions

1. Report this flow as not yet automated
2. Use inference data or metadata output to confirm the section is empty