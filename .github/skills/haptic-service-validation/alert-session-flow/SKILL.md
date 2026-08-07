---
name: haptic-alert-session-flow
description: "Use when: validating the end-to-end DMS-alert-triggered haptic session flow from device logs. Covers AWS IoT shadow keep-alive, push-alert, svc/ndcentral/inference/uploader verification, per-camera VOD upload, haptic motor trigger on drowsy alerts, and related negative/stress scenarios (camera disabled, DMS disabled, motor disconnected, back-to-back alerts, supercap, service restart/GPIO disconnect during an active trigger)."
argument-hint: "device ID (e.g., /haptic-alert-session-flow 440073)"
---

# Haptic — Alert Session Flow (Flow 7)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> `functionality_map.py`'s `"Alert Session Flow"` bucket.

## What happens

End-to-end alert-session flow adapted from
`tests/sanity/test_tc_sanity_1330_alert_session_flow.py`, trimmed to `bagheera3`/`octo`
only. Sets ignition on, disables privacy, enables video upload, confirms the AWS IoT
shadow is not full (`aws_ping_command("keep-alive")` round-trip), pushes an alert, and
verifies `svc`/`ndcentral`/`inference`/`uploader` react correctly through session
creation, `commn_set` event code 99, `UPLOAD_STATE`, and per-camera VOD upload
(cameras 0-3 on bagheera3; 0-1 always + 2-3 gated on connectivity on octo). After the
full alert/VOD flow, it verifies `haptic_feedback` service health: `service_status`
active and the `"Starting Haptic Service"` log line present.

**When active:** `[haptic_feedback] enabled=1`; requires privacy disabled and
`upload_video` enabled for all cameras (test pushes this config itself)
**Frequency:** Once per full alert-session cycle (long-running, multi-camera)
**Cross-service impact:** `awsiot` (shadow/keep-alive, cloud alert push), `inference`
(NRT/analytics processing gate), `uploader`/`unifieduploader` (VOD upload),
`ndcentral`, `svc`, `scheduler_manager`
**is_cloud_dependent:** 1 **is_analytics_dependent:** 1

## Key log patterns (awsiot.log)

- `Connected successfully`
- `Received command: keep-alive`
- `Received command: 0<session>` *(per-camera VOD command ack)*

## Key log patterns (inference.log)

- `commn_set.*99`
- `UPLOAD_STATE` *(for the active session)*

## Test cases that validate this flow

| Test Case ID    | Python File                                                                         | What it checks                                                          |
| --------------- | ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `TC_HAPTIC_4354`| `tests/haptic/test_tc_haptic_4354_alert_session_flow_haptic_health_check.py`       | Full alert/VOD e2e flow + haptic_feedback service health post-flow          |

## Related confluence evidence (DQA — Haptic Module Software Functional Checks)

`TC_HAPTIC_4354` validates the alert-session infrastructure (cloud shadow, upload,
service-health post-flow) but does NOT drive the haptic **motor trigger itself** off a
real drowsy-alert event, nor cover the negative/stress scenarios below. The confluence
page documents the actual DMS-drowsy-alert → haptic-motor-trigger behavior in detail —
use this as the ground truth if asked to validate motor-trigger correctness beyond
`TC_HAPTIC_4354`'s own scope.

### Alert-triggered haptic behavior

- **Alert session (DMS severe drowsy)** — PASS. Triggering DMS severe drowsy (event
  code `401.1.5.0.20` / published as `401.1.5.1.0`) activates the haptic motor;
  `dmsanalytics.log` shows `raising drowsy alert {...}` and `alert_publisher` shows
  `Publishing dms_drowsy alert (401.1.5.1.0): Acceptable alert latency - <N>ms`.
- **Incab Feedback Validation** — severe drowsy (`401.1.5.1.0`) and moderate drowsy
  (`401.1.5.1.1`) event codes both trigger haptic feedback.
- **Privacy session** — PASS. No data upload and no haptic when device is in privacy
  mode.
- **Analytics Session Processing (E2E)** — PASS. Inward, outward, and DMS sessions are
  all processed and alert-session data correctly attributed per camera.
- **Post Ignition Analytics Seconds** — PASS. Haptic behavior during the post-ignition
  analytics window follows the `post_ignition_analytics_seconds` config value.

### Negative scenarios

| Scenario | Result | Notes |
| -------- | ------ | ----- |
| Haptic when inward camera is disabled | PASS | No DMS alerts triggered (inward camera disabled from cloud) → no haptic |
| Alert behavior when DMS is disabled | PASS | No haptic or audio alert triggered when DMS disabled |
| Audio fallback when haptic motor is disconnected | **FAIL** | Expected: audio alert should be used when the haptic actuator is disconnected; audio fallback did not occur correctly |
| Behavior when both alert channels (audio + haptic) unavailable | PASS | No DMS/haptic alerts triggered when both channels are unavailable |
| DMS camera manual disconnect/reconnect | PASS | Haptic alerts correctly triggered/not-triggered across a manual DMS camera disconnect+reconnect cycle |

### Stress / stability-under-load scenarios

| Scenario | Result | Notes |
| -------- | ------ | ----- |
| Haptic motor disconnection during a drive | **FAIL** — `BGR3-1394` | Motor disconnection/reconnection during an active drive is not handled correctly in metadata |
| Back-to-back alert generation | Not Evaluated | Multiple alerts for the same event type (severe + moderate drowsy) back-to-back is difficult to reproduce in lab/drive |
| Supercap events during an active haptic trigger | PASS | Supercap events must NOT be received by the device while a haptic alert is triggering |
| Haptic service restart during an active trigger | PASS | Restarting `haptic_feedback` mid-trigger (including multiple restarts) is handled correctly — a queued haptic trigger message is still received once the service restarts |
| GPIO disconnection during an active haptic trigger | PASS | Disconnecting the GPIO pin while the motor is triggering, or restarting the service mid-trigger, correctly stops the motor in both cases (tracked under `BGR3-1394`) |
| Observation payload includes haptic status | **FAIL** — `AN-28689` | The observation/metadata report does not currently include haptic status |

**Known bug tracker:** parent query `BGR3-1388` links all haptic-related bugs
(`BGR3-1394`, and others) — use `jira-confluence-fetch` to pull current bug status if
needed.

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Alert/VOD End-to-End Flow + Haptic Health Check",
      "description": "Full alert-session e2e flow (shadow keep-alive, push alert, svc/ndcentral/inference/uploader verification, per-camera VOD upload) followed by haptic_feedback service health check.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 1,
      "is_analytics_dependent": 1,
      "dependent_flows": [".github/skills/haptic-service-validation/metadata-session/SKILL.md"]
    },
    {
      "name": "Alert Session (DMS Severe Drowsy) Motor Trigger",
      "description": "Triggering DMS severe drowsy (401.1.5.0.20 / published 401.1.5.1.0) activates the haptic motor; confirmed via dmsanalytics.log and alert_publisher latency log. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Incab Feedback Validation",
      "description": "Both severe drowsy (401.1.5.1.0) and moderate drowsy (401.1.5.1.1) event codes trigger haptic feedback.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Privacy Session (No Upload, No Haptic)",
      "description": "No data upload and no haptic when device is in privacy mode. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": [".github/skills/haptic-service-validation/metadata-session/SKILL.md"]
    },
    {
      "name": "Analytics Session Processing (E2E)",
      "description": "Inward, outward, and DMS sessions are all processed and alert-session data correctly attributed per camera. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Post Ignition Analytics Seconds",
      "description": "Haptic behavior during the post-ignition analytics window follows the post_ignition_analytics_seconds config value. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Haptic When Inward Camera Disabled",
      "description": "No DMS alerts triggered (inward camera disabled from cloud) \u2192 no haptic. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Alert Behavior When DMS Disabled",
      "description": "No haptic or audio alert triggered when DMS is disabled. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Audio Fallback When Haptic Motor Disconnected",
      "description": "Expected: audio alert should be used when the haptic actuator is disconnected. FAIL \u2014 audio fallback did not occur correctly (unresolved, known issue).",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Both Alert Channels (Audio + Haptic) Unavailable",
      "description": "No DMS/haptic alerts triggered when both channels are unavailable. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "DMS Camera Manual Disconnect/Reconnect",
      "description": "Haptic alerts correctly triggered/not-triggered across a manual DMS camera disconnect+reconnect cycle. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Haptic Motor Disconnection During a Drive",
      "description": "Motor disconnection/reconnection during an active drive is not handled correctly in metadata. FAIL \u2014 BGR3-1394 (unresolved, known issue).",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Back-to-Back Alert Generation",
      "description": "Multiple alerts for the same event type (severe + moderate drowsy) back-to-back \u2014 difficult to reproduce in lab/drive. Not Evaluated.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Supercap Events During an Active Haptic Trigger",
      "description": "Supercap events must NOT be received by the device while a haptic alert is triggering. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Haptic Service Restart During an Active Trigger",
      "description": "Restarting haptic_feedback mid-trigger (including multiple restarts) is handled correctly \u2014 a queued haptic trigger message is still received once the service restarts. PASS.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": [".github/skills/haptic-service-validation/service-status-lifecycle/SKILL.md"]
    },
    {
      "name": "GPIO Disconnection During an Active Haptic Trigger",
      "description": "Disconnecting the GPIO pin while the motor is triggering, or restarting the service mid-trigger, correctly stops the motor in both cases. PASS \u2014 tracked under BGR3-1394.",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1,
      "dependent_flows": []
    },
    {
      "name": "Observation Payload Includes Haptic Status",
      "description": "The observation/metadata report should include haptic status. FAIL \u2014 AN-28689, not currently included (unresolved, known issue).",
      "flow_skill_path": ".github/skills/haptic-service-validation/alert-session-flow/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/service-status-lifecycle/SKILL.md"]
    }
  ]
}
```

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. For `TC_HAPTIC_4354`: treat AWS IoT shadow-full (`device shadow might be full`) as
   an abort condition distinct from a genuine haptic/service failure — check
   `awsiot.log` for `Received command: keep-alive` first if the test aborts early
3. For `TC_HAPTIC_4354`: verify `inference.log` shows both `commn_set.*99` and
   `UPLOAD_STATE` for the active session before checking `haptic_feedback` post-flow
   health
4. If asked to validate an actual DMS-drowsy-alert → haptic-motor-trigger event (not
   just the session/upload infrastructure `TC_HAPTIC_4354` covers), look for
   `raising drowsy alert {...}` in `dmsanalytics.log`/`ndcentral.log` and
   `Publishing dms_drowsy alert (<event_code>): Acceptable alert latency - <N>ms` in
   the alert publisher logs, then confirm `Periodic health: output HIGH, motor
   running` in `haptic_feedback.log` around the same timestamp
5. Do NOT report a fresh FAIL for "audio fallback when haptic disconnected"
   (unresolved) or "haptic motor disconnection during a drive" (`BGR3-1394`,
   unresolved) as new regressions — these are known, tracked issues unless a fresh,
   different symptom is observed
6. Do NOT report a fresh FAIL for "observation payload includes haptic status"
   (`AN-28689`, unresolved) unless explicitly asked to validate that specific field
