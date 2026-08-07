---
name: power-on-reset-por-recovery
description: "Use when: documenting the Power On Reset recovery scenario for haptic_feedback. This flow is confluence-backed only and has no dedicated automated test case yet."
argument-hint: "device ID (e.g., /power-on-reset-por-recovery 440073)"
---

# Haptic — Flow 16: Power On Reset (POR) Recovery

## What happens

The DQA confluence reboot matrix includes Power On Reset as one of the scenarios where
no service crashes should occur and `haptic_feedback` should recover. There is no
dedicated automated `tests/haptic/` case for this flow yet.

**When active:** Confluence-backed manual validation only
**Frequency:** As needed for manual reboot-matrix review
**Cross-service impact:** reboot orchestration
**Automated:** No
**Flow type:** Negative
**Cloud dependent:** No
**Analytics dependent:** No

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence reboot matrix | POR is part of the "no service crashes post all reboot scenarios" matrix |

## Pass criteria

- Manual or confluence-backed evidence shows `haptic_feedback` recovers after POR

## Fail signals

- POR-specific evidence shows `haptic_feedback` fails to recover

## Validation instructions

1. Report this flow as not yet automated
2. Use confluence-backed reboot-matrix evidence if asked about POR specifically
3. Do not claim automated coverage for this flow