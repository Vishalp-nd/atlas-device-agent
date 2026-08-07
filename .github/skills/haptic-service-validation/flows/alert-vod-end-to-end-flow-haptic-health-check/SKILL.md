---
name: alert-vod-end-to-end-flow-haptic-health-check
description: "Use when: validating the full alert-session infrastructure and post-flow haptic service health. Covers keep-alive, alert push, session processing, upload-state evidence, per-camera VOD upload, and final haptic_feedback health."
argument-hint: "device ID (e.g., /alert-vod-end-to-end-flow-haptic-health-check 440073)"
---

# Haptic — Flow 28: Alert/VOD End-to-End Flow + Haptic Health Check

## What happens

This flow validates the end-to-end alert-session infrastructure adapted from the sanity
flow: AWS IoT keep-alive, alert push, session creation, inference-side `commn_set` and
`UPLOAD_STATE`, per-camera VOD upload, and final `haptic_feedback` service health after
the full flow completes.

**When active:** `[haptic_feedback] enabled=1`, privacy disabled, upload enabled
**Frequency:** Once per full alert-session cycle
**Cross-service impact:** `awsiot`, `svc`, `ndcentral`, `inference`, `uploader`
**Automated:** Yes
**Flow type:** Positive
**Cloud dependent:** Yes
**Analytics dependent:** Yes

## Key evidence

### awsiot.log

- `Received command: keep-alive`
- `Received command: 0<session>`

### inference.log

- `commn_set.*99`
- `UPLOAD_STATE`

### haptic_feedback health

- `haptic_feedback` remains or returns to `active`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4354` | `tests/haptic/test_tc_haptic_4354_alert_session_flow_haptic_health_check.py` | full alert/VOD flow and post-flow haptic health |

## Pass criteria

- Keep-alive succeeds and the alert-session flow proceeds
- Inference-side session evidence appears
- Per-camera VOD upload path completes as expected by the test
- `haptic_feedback` is healthy after the flow

## Fail signals

- Keep-alive or alert push fails before the flow starts
- Session-processing or upload-state evidence is missing
- `haptic_feedback` is unhealthy after the flow

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify keep-alive before treating an early abort as a haptic failure
3. Confirm inference-side session evidence before checking final haptic health
4. Keep this infrastructure flow separate from the actual motor-trigger flows below