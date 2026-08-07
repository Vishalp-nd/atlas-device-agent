---
name: haptic-api-audio-trigger-processing
description: "Use when: validating haptic_feedback API call handling and audio-trigger-driven motor processing from device logs — valid/invalid/unauthorized API calls, API calls while the service is stopped, and audio trigger message processing (valid/unsupported-format/corrupted audio files) tied to DMS drowsy alert events. Not yet automated in tests/haptic/; use DQA Confluence expected-behavior reference."
argument-hint: "device ID (e.g., /haptic-api-audio-trigger-processing 440073)"
---

# Haptic — API Calls & Audio Trigger Processing (Flow 10)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> the **API Calls & Audio Trigger Processing** section of the DQA Confluence page
> [Haptic_Feedback Service Checks](https://netradyne.atlassian.net/wiki/spaces/DQA/pages/2182873155/Haptic_Feedback+Service+Checks).
> Not present in `functionality_map.py`'s `"haptic"` bucket list — no automated
> `tests/haptic/` TC exists for this flow today.

## What happens

`haptic_feedback` receives a ZMQ audio/trigger message (from `dmsAnalyticsClient` via
the `audioPlayback` queue) when a DMS drowsy alert is raised, and drives the haptic
motor accordingly. API-level validation (valid/invalid-payload/unauthorized calls, and
calls while the service is stopped) is documented as a separate check but was **not
evaluated** on the confluence run. Audio trigger processing was validated with a valid
audio file, an unsupported format, and a corrupted file.

**When active:** N/A — not yet automated
**Frequency:** N/A
**Cross-service impact:** `dmsAnalyticsClient` (raises the drowsy signal/alert),
`audioPlayback`/`ndcentral` (queues the audio-trigger message to `haptic_feedback`),
`alert_publisher`
**is_cloud_dependent:** 0 **is_analytics_dependent:** 1

> **Not yet automated.** No file under `tests/haptic/` currently implements this flow —
> it is not present in `functionality_map.py`'s `"haptic"` bucket list. Do not report a
> PASS/FAIL verdict for it; report `NOT_AUTOMATED` if asked about this bucket.

**Test cases that validate this flow:** *(none — not in `functionality_map.py`, see note above)*

## Key log patterns (haptic_feedback.log)

- `Audio message received for session: <session>`
- `Filename: /home/ubuntu/autocam/audio/<...>.wav`
- `Audio data successfully serialized for session: <session> in the file <...>/audio_events.pb`

## Key log patterns (dmsanalytics.log)

- `DrowsySevere signal msg received: {...}`
- `raising drowsy alert {..., 'event_code': '401.1.5.0.20', ...}`
- `raising drowsy alert {..., 'event_code': '401.1.5.0.0', ...}`
- `Publishing dms_drowsy alert (401.1.5.1.0): Acceptable alert latency - <N>ms` *(from `src.alert_publisher`)*

## Expected behavior reference (DQA Confluence — Haptic_Feedback Service Checks)

| Scenario | Confluence Result | Notes |
| -------- | ------------------ | ----- |
| API Functional — valid API call | **NA** | Not evaluated on this run |
| API Functional — invalid payload | **NA** | Not evaluated on this run |
| API Functional — unauthorized request | **NA** | Not evaluated on this run |
| Service Status — API call when service stopped | **NA** | Not evaluated on this run |
| Audio Trigger — valid audio file | PASS | See log patterns above — `Severe_Stay_Alert.wav` example, `401.1.5.1.0` alert published with ~140ms latency |
| Audio Trigger — unsupported format | PASS | Handled gracefully |
| Audio Trigger — corrupted file | PASS | Handled gracefully |

## Haptic trigger timing metrics (DQA Confluence — Haptic Metrics table)

| Parameter | Value |
| --------- | ----- |
| Time for motor to turn on after being triggered | 1 second |
| Vibration duration — moderate drowsy | 3 seconds |
| Vibration duration — severe drowsy | 5 seconds |

> Cross-reference: the confluence "Boot Up time for haptic service" metric (12 seconds)
> matches [metadata-session](../metadata-session/SKILL.md)'s "First Minute Session"
> expectation (`haptic_feedback` up within 12 seconds from bootup) — do not duplicate
> that check here.

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. Report `NOT_AUTOMATED` for this bucket — there is no `tests/haptic/` file to
   execute and no TC ID assigned in `functionality_map.py`
3. If asked to manually assess audio-trigger processing, confirm
   `dmsanalytics.log` shows `raising drowsy alert {...}` and `alert_publisher`
   shows `Publishing dms_drowsy alert (<event_code>): Acceptable alert latency - <N>ms`,
   then confirm `haptic_feedback.log` shows `Audio message received for session:` and
   `Audio data successfully serialized for session:` around the same timestamp
4. If asked about motor-response timing, use the ~1 second turn-on and 3s/5s
   (moderate/severe) vibration-duration figures above as reference baselines, not hard
   pass/fail thresholds (no automated TC asserts these)
5. Do NOT report a PASS/FAIL verdict for the API Functional / Service-Status API-call
   scenarios — confluence marks these `NA` (not evaluated), not tested either way

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "API Calls & Audio Trigger Processing",
      "description": "API-level validation (valid/invalid-payload/unauthorized calls, service-stopped calls — not evaluated) and DMS-drowsy-alert-driven audio trigger message processing (valid/unsupported-format/corrupted audio files) into the haptic motor. Not yet automated.",
      "flow_skill_path": ".github/skills/haptic-service-validation/api-audio-trigger-processing/SKILL.md",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1
    }
  ]
}
```
