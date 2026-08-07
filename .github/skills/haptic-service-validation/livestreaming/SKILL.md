---
name: haptic-livestreaming
description: "Use when: validating haptic_feedback stability during cloud-triggered inward/outward/dual AWS livestream sessions from device logs."
argument-hint: "device ID (e.g., /haptic-livestreaming 440073)"
---

# Haptic — Livestreaming (Flow 8)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> `functionality_map.py`'s `"Livestreaming"` bucket.

## What happens

Triggers a cloud-initiated AWS livestream (`device.aws_live_stream("inward"/"outward"/"dual")`)
with `[live_streaming] enabled=true` and `audio_notification=true` pushed via config.
Confirms `cam_rec` logs `LIVE stream starting for kinesis_req_cam_id = <N>` for the
requested camera(s), waits for the stream to run, then confirms `haptic_feedback`
remains active and reaches its ready state throughout the livestream session.

**When active:** `[live_streaming] enabled=true`
**Frequency:** Once per livestream trigger under test
**Cross-service impact:** `cam_rec` (stream pipeline), `awsiot`/cloud (livestream
trigger and kinesis request), `haptic_feedback` (must survive concurrent camera load)
**is_cloud_dependent:** 1 **is_analytics_dependent:** 0

## Key log patterns (cam_rec.log)

- `LIVE stream starting for kinesis_req_cam_id = <N>` *(1=inward, 0=outward, both for dual)*

## Test cases that validate this flow

| Test Case ID    | Python File                                                                          | What it checks                                                       |
| --------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `TC_HAPTIC_4355`| `tests/haptic/test_tc_haptic_4355_inward_livestream_haptic_health_check.py`         | Inward livestream trigger + haptic_feedback active/ready during stream  |
| `TC_HAPTIC_4356`| `tests/haptic/test_tc_haptic_4356_outward_livestream_haptic_health_check.py`        | Outward livestream trigger + haptic_feedback active/ready during stream |
| `TC_HAPTIC_4357`| `tests/haptic/test_tc_haptic_4357_dual_livestream_haptic_health_check.py`           | Dual (inward+outward) livestream trigger + haptic_feedback health        |

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Inward Livestream Haptic Health Check",
      "description": "Cloud-triggered inward AWS livestream; cam_rec starts kinesis_req_cam_id=1 and haptic_feedback remains active/ready throughout.",
      "flow_skill_path": ".github/skills/haptic-service-validation/livestreaming/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 1,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Outward Livestream Haptic Health Check",
      "description": "Cloud-triggered outward AWS livestream; cam_rec starts kinesis_req_cam_id=0 and haptic_feedback remains active/ready throughout.",
      "flow_skill_path": ".github/skills/haptic-service-validation/livestreaming/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 1,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Dual Livestream Haptic Health Check",
      "description": "Cloud-triggered dual (inward+outward) AWS livestream; cam_rec starts both kinesis_req_cam_id streams and haptic_feedback remains active/ready under concurrent camera load.",
      "flow_skill_path": ".github/skills/haptic-service-validation/livestreaming/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 1,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    }
  ]
}
```

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. Confirm `cam_rec.log` shows `LIVE stream starting for kinesis_req_cam_id = <N>`
   with the camera ID matching the requested direction (`1`=inward, `0`=outward, both
   for dual) before checking `haptic_feedback` health
3. Confirm `haptic_feedback`'s `service_status` remains `active` throughout the
   livestream duration, not just at the start/end
