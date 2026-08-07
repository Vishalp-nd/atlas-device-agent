---
name: alert-session-dms-severe-drowsy-motor-trigger
description: "Use when: validating that a severe DMS drowsy alert triggers the haptic motor. Covers dmsanalytics alert evidence, alert-publisher latency evidence, and motor-running confirmation."
argument-hint: "device ID (e.g., /alert-session-dms-severe-drowsy-motor-trigger 440073)"
---

# Haptic — Flow 29: Alert Session (DMS Severe Drowsy) Motor Trigger

## What happens

This flow validates the actual haptic motor trigger for a severe DMS drowsy alert. The
source material ties this to the severe drowsy event code path and uses both
`dmsanalytics.log` and alert-publisher latency evidence to confirm the trigger.

**When active:** DMS severe drowsy alert scenario
**Frequency:** Per severe-drowsy alert validation
**Cross-service impact:** `dmsAnalyticsClient`, alert publisher, `haptic_feedback`
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Key evidence

### dmsanalytics.log

- `raising drowsy alert {...}`

### alert publisher logs

- `Publishing dms_drowsy alert (401.1.5.1.0): Acceptable alert latency - <N>ms`

### haptic_feedback.log

- `Periodic health: output HIGH, motor running`

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | severe drowsy alert triggers haptic motor |

## Pass criteria

- Severe drowsy alert evidence is present
- Alert-publisher latency evidence is present
- Haptic motor-running evidence appears around the same time

## Fail signals

- Severe drowsy alert occurs without corresponding haptic trigger evidence

## Validation instructions

1. Report this flow as not yet automated
2. Correlate alert evidence and haptic motor evidence by timestamp