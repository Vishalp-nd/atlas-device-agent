---
name: cloud-triggered-aws-reboot-recovery
description: "Use when: validating haptic_feedback recovery after a cloud-triggered AWS reboot. Covers the awsiot to power_monitor reboot path, post-reboot haptic service health, and DMS camera stream resumption."
argument-hint: "device ID (e.g., /cloud-triggered-aws-reboot-recovery 440073)"
---

# Haptic — Flow 12: Cloud-Triggered AWS Reboot Recovery

## What happens

This flow validates the cloud reboot round-trip driven by `device.aws_reboot()`. The
request should pass through `awsiot`, be handed to `power_monitor`, reboot the device,
and bring `haptic_feedback` back to a healthy active state. The existing source bucket
also treats DMS camera stream resumption as part of the expected recovery.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per AWS reboot scenario
**Cross-service impact:** `awsiot`, `power_monitor`, and DMS camera pipeline
**Automated:** Yes
**Flow type:** Negative
**Cloud dependent:** Yes
**Analytics dependent:** No

## Key evidence

### awsiot.log

- `Reboot request sent to powermon`

### power_mon.log

- `POWER_MONITOR_ctx->previous_shutdown_reason DBSTATE_SHUTDOWN_AWSIOT:REBOOT`

### haptic_feedback.log

- `######## Starting Haptic Service on tcp://127.0.0.1:6393 #####`

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4349` | `tests/haptic/test_tc_haptic_4349_aws_reboot_haptic_health_check.py` | cloud reboot round-trip and post-reboot haptic health |

## Pass criteria

- `awsiot` forwards the reboot request to `power_monitor`
- `power_monitor` records the AWS reboot shutdown reason
- The device reboots and `haptic_feedback` returns to `active`
- DMS camera stream resumes after reboot as expected by the existing test

## Fail signals

- The reboot request does not reach `power_monitor`
- The AWS reboot shutdown reason is missing
- `haptic_feedback` does not recover after reboot
- DMS camera stream does not resume after the reboot scenario

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Verify the cloud reboot request path in `awsiot.log` and `power_mon.log`
3. Confirm the device comes back and `haptic_feedback` returns to `active`
4. Confirm the post-reboot camera-stream expectation from the existing test