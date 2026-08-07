---
name: analytics-session-processing-e2e
description: "Use when: validating end-to-end analytics session processing for inward, outward, and DMS sessions. Covers correct attribution of alert-session data per camera."
argument-hint: "device ID (e.g., /analytics-session-processing-e2e 440073)"
---

# Haptic — Flow 32: Analytics Session Processing (E2E)

## What happens

This flow validates that inward, outward, and DMS sessions are all processed correctly
and that alert-session data is attributed to the correct camera.

**When active:** Analytics-backed alert-session validation
**Frequency:** Per end-to-end analytics session review
**Cross-service impact:** analytics and session attribution pipeline
**Automated:** No
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** Yes

## Source coverage

| Source | What it validates |
| ------ | ----------------- |
| DQA confluence alert-session checks | inward, outward, and DMS sessions are processed and attributed correctly |

## Pass criteria

- All relevant session types are processed
- Alert-session data is attributed to the correct camera

## Fail signals

- Session processing is incomplete
- Alert-session data is attributed to the wrong camera

## Validation instructions

1. Report this flow as not yet automated
2. Use session-processing evidence to confirm per-camera attribution