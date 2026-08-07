---
name: last-minute-partial-session
description: "Use when: validating haptic metadata behavior for an abruptly ended partial session. Covers the expectation that no Haptic_events section should be present for the partial file."
argument-hint: "device ID (e.g., /last-minute-partial-session 440073)"
---

# Haptic — Flow 19: Last-Minute / Partial Session

## What happens

If a session ends abruptly, such as during a reboot mid-session, the partial file should
not contain processed haptic metadata. The expected behavior from the source material is
that the `Haptic_events` section is absent.

**When active:** Manual or confluence-backed metadata review
**Frequency:** Edge-case session validation only
**Cross-service impact:** session metadata and inference output
**Automated:** No
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence session-behavior matrix | partial session should not contain `Haptic_events` |

## Pass criteria

- The partial file does not contain a `Haptic_events` section

## Fail signals

- Haptic metadata is present in the partial file when it should not be

## Validation instructions

1. Report this flow as not yet automated
2. Use session metadata or inference output to confirm the section is absent
3. Keep this flow separate from normal alert-session processing