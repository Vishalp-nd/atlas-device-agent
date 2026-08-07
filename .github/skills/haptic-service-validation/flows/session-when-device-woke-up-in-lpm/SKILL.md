---
name: session-when-device-woke-up-in-lpm
description: "Use when: validating haptic behavior for a session that starts after low-power-mode wakeup. Covers the expectation that analytics does not run and haptic remains OFF."
argument-hint: "device ID (e.g., /session-when-device-woke-up-in-lpm 440073)"
---

# Haptic — Flow 24: Session when Device Woke Up in LPM

## What happens

For a session that starts after low-power-mode wakeup, the source material states that
analytics does not run during low-power mode, so haptic remains OFF.

**When active:** Manual or confluence-backed session review
**Frequency:** Session-behavior validation only
**Cross-service impact:** low-power-mode analytics gating
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence session-behavior matrix | haptic remains OFF for LPM wakeup session |

## Pass criteria

- No haptic trigger occurs for the LPM wakeup session
- No analytics-driven alert path is present for the session

## Fail signals

- Haptic triggers despite the LPM wakeup condition

## Validation instructions

1. Report this flow as not yet automated
2. Use session evidence to confirm haptic stayed OFF