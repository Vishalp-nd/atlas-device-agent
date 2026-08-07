---
name: non-alert-session
description: "Use when: validating haptic behavior in a session with no triggering event. Covers the expectation that haptic remains OFF because no DMS drowsy alert or audio event occurs."
argument-hint: "device ID (e.g., /non-alert-session 440073)"
---

# Haptic — Flow 22: Non-Alert Session

## What happens

In a session with no triggering event, haptic should remain OFF. The source material
describes this as a session where no DMS drowsy alert or related audio event occurs.

**When active:** Manual or confluence-backed session review
**Frequency:** Session-behavior validation only
**Cross-service impact:** DMS alert generation path
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence session-behavior matrix | haptic remains OFF in a non-alert session |

## Pass criteria

- No haptic trigger occurs during the session
- No DMS drowsy alert or related audio event is present for the session

## Fail signals

- Haptic triggers in a session with no qualifying event

## Validation instructions

1. Report this flow as not yet automated
2. Use session evidence to confirm no alert-trigger path occurred