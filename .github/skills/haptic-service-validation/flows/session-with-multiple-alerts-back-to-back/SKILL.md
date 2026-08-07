---
name: session-with-multiple-alerts-back-to-back
description: "Use when: documenting the edge case of back-to-back DMS drowsy alerts in one session. This flow is noted in source material as difficult to reproduce and not evaluated."
argument-hint: "device ID (e.g., /session-with-multiple-alerts-back-to-back 440073)"
---

# Haptic — Flow 27: Session with Multiple Alerts (back-to-back)

## What happens

This edge case asks whether multiple DMS drowsy alerts in one session are handled
correctly. The source material marks it as difficult to reproduce in lab or drive
conditions and not evaluated.

**When active:** Edge-case manual review only
**Frequency:** Rare, hard-to-reproduce scenario
**Cross-service impact:** DMS alert generation and repeated trigger handling
**Automated:** No
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence session-behavior matrix | scenario exists but is not evaluated |

## Pass criteria

- Not established in the source material

## Fail signals

- Not established in the source material

## Validation instructions

1. Report this flow as not yet automated and not evaluated
2. Do not overstate confidence for this edge case