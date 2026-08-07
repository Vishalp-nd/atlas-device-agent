---
name: privacy-session
description: "Use when: validating haptic metadata behavior for a privacy session. Covers the expectation that the haptic_event section remains empty for the privacy file."
argument-hint: "device ID (e.g., /privacy-session 440073)"
---

# Haptic — Flow 21: Privacy Session

## What happens

For the privacy-session metadata case, the expected behavior from the source material is
that the `haptic_event` section remains empty under inference data for the privacy file.

**When active:** Manual or confluence-backed privacy-session review
**Frequency:** Edge-case session validation only
**Cross-service impact:** privacy-mode metadata and inference output
**Automated:** No
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence session-behavior matrix | privacy file should have empty `haptic_event` section |

## Pass criteria

- The `haptic_event` section is empty for the privacy file

## Fail signals

- Haptic metadata appears in the privacy file when it should be empty

## Validation instructions

1. Report this flow as not yet automated
2. Use inference data or metadata output to confirm the section is empty
3. Keep this metadata-focused flow separate from the alert-session privacy behavior flow