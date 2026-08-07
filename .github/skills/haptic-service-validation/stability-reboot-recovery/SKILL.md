---
name: haptic-stability-reboot-recovery
description: "Use when: validating haptic_feedback stability and recovery across disruptive events from device logs. Covers cyclic reboot, bagheera kill, svc kill, cloud-triggered AWS reboot, camera-crash reboot, low-power wakeup cycle, and crank shutdown."
argument-hint: "device ID (e.g., /haptic-stability-reboot-recovery 440073)"
---

# Haptic — Stability & Reboot Recovery (Flow 4)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> `functionality_map.py`'s `"Stability & Reboot Recovery"` bucket.

## What happens

`haptic_feedback` must return to an `active` state after every disruptive reboot/crash
path the device supports: cyclic reboot, `bagheera` process kill/restart, `svc` process
kill/restart, cloud-triggered AWS reboot (via `device.aws_reboot()` → `awsiot` →
`power_monitor`), camera-crash-triggered reboot, a full low-power-wakeup (LPW/crank)
cycle, and crank-based shutdown. Each scenario restores the original config and reboots
as cleanup.

**When active:** `[haptic_feedback] enabled=1`
**Frequency:** Once per disruptive event under test
**Cross-service impact:** `power_monitor` (AWS reboot, crank shutdown, LPW),
`bagheera`/`svc` (process kill scenarios), `awsiot` (cloud reboot trigger only for
`TC_HAPTIC_4349`)
**is_cloud_dependent:** 1 (via `TC_HAPTIC_4349` only) **is_analytics_dependent:** 0

## Key log patterns (awsiot.log, TC_HAPTIC_4349 only)

- `Reboot request sent to powermon`

## Key log patterns (power_mon.log, TC_HAPTIC_4349 only)

- `POWER_MONITOR_ctx->previous_shutdown_reason DBSTATE_SHUTDOWN_AWSIOT:REBOOT`

## Test cases that validate this flow

| Test Case ID    | Python File                                                                          | What it checks                                                        |
| --------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `TC_HAPTIC_4346`| `tests/haptic/test_tc_haptic_4346_cyclic_reboot_haptic_health_check.py`                | haptic_feedback active/healthy after cyclic reboot cycle                |
| `TC_HAPTIC_4347`| `tests/haptic/test_tc_haptic_4347_bagheera_kill_haptic_health_check.py`                | haptic_feedback unaffected by `bagheera` kill + restart                 |
| `TC_HAPTIC_4348`| `tests/haptic/test_tc_haptic_4348_svc_kill_haptic_health_check.py`                     | haptic_feedback unaffected by `svc` kill + restart                      |
| `TC_HAPTIC_4349`| `tests/haptic/test_tc_haptic_4349_aws_reboot_haptic_health_check.py`                   | Cloud-triggered AWS reboot → power_mon reason, haptic_feedback active + DMS camera stream resumes after |
| `TC_HAPTIC_4350`| `tests/haptic/test_tc_haptic_4350_camera_crash_reboot_haptic_health_check.py`          | haptic_feedback recovery after camera-crash-triggered reboot            |
| `TC_HAPTIC_4351`| `tests/haptic/test_tc_haptic_4351_low_power_wakeup_haptic_health_check.py`             | haptic_feedback recovery across a full LPW cycle                        |
| `TC_HAPTIC_4352`| `tests/haptic/test_tc_haptic_4352_crank_shutdown_haptic_health_check.py`               | haptic_feedback recovery across crank-based shutdown/restart            |

## Related confluence evidence (DQA — Haptic Module Software Functional Checks)

The confluence page's **"Driver.i and haptic service behavior during reboot
scenarios"** test (PASS — "No service crashes post all reboot scenarios") independently
confirms this exact scenario matrix: Cyclic reboot, Low power mode, **Power On Reset
(POR)**, AWSIOT Reboot, Camera Crash, Crank OFF Shutdown, SVC Reboot.

- **Coverage gap:** Power On Reset (POR) is confluence-tested but has no dedicated
  `tests/haptic/` TC in this bucket today — if asked about POR specifically, note it
  as validated manually/via confluence but not yet automated.
- **Camera-crash shutdown reason** observed in `power_mon.log`:
  `previousShutdownReason":"DBSTATE_SHUTDOWN_CAM_CRASH:REBOOT : BATTERY_ACTIVE"` —
  useful confirmation log if `TC_HAPTIC_4350` needs the shutdown-reason cross-checked
  (analogous to the AWS-reboot reason string above).
- **haptic_feedback startup banner** confirmed in confluence log excerpts:
  `######## Starting Haptic Service on tcp://127.0.0.1:6393 #####` followed by
  `Waiting for haptic alert messages` — expect these two lines immediately after any
  reboot/restart in this bucket as the first sign of a healthy respawn.
- **Known log anomaly (not a functional failure in the confluence run):** a
  `JSON parsing error on line 1: too big integer near '18446744073709551615'` was
  observed in `haptic_feedback.log` right after a camera-crash reboot, tied to a
  `SessionInfo` message with an out-of-range `frame_gen_time`. This looks like a
  `frame_gen_time` integer-overflow producer-side bug (`ndcentral`/`bagheera`), not a
  `haptic_feedback` fault — if seen, flag it as a log anomaly worth reporting
  separately, but don't treat it as a `TC_HAPTIC_4350` failure by itself unless the
  test's own assertions fail.
- **"Device Stability - Long run stability"** confluence test is marked **In
  Progress** (Yellow) — no pass/fail verdict exists yet for extended long-run
  stability beyond the discrete reboot scenarios above.

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Cyclic Reboot Recovery",
      "description": "haptic_feedback returns to active/healthy state after a cyclic reboot cycle.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Bagheera Kill Recovery",
      "description": "haptic_feedback is unaffected by a bagheera process kill + restart.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "SVC Kill Recovery",
      "description": "haptic_feedback is unaffected by an svc process kill + restart.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Cloud-Triggered AWS Reboot Recovery",
      "description": "device.aws_reboot() \u2192 awsiot \u2192 power_monitor reboot round-trip; haptic_feedback active and DMS camera stream resumes after.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 1,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Camera-Crash Reboot Recovery",
      "description": "haptic_feedback recovers after a camera-crash-triggered reboot.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Low-Power Wakeup Cycle Recovery",
      "description": "haptic_feedback recovers across a full low-power-wakeup (LPW) cycle.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Crank Shutdown Recovery",
      "description": "haptic_feedback recovers across a crank-based shutdown/restart.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Power On Reset (POR) Recovery",
      "description": "Confluence-tested reboot scenario (part of the 'no service crashes post all reboot scenarios' matrix) \u2014 no dedicated tests/haptic/ TC exists yet; validated manually/via confluence only.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Device Stability - Long Run Stability",
      "description": "Extended long-run stability beyond the discrete reboot scenarios above. Confluence status: In Progress \u2014 no pass/fail verdict exists yet.",
      "flow_skill_path": ".github/skills/haptic-service-validation/stability-reboot-recovery/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    }
  ]
}
```

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. For `TC_HAPTIC_4349` (AWS reboot): confirm the cloud round-trip (`aws_reboot` →
   `awsiot` "Reboot request sent to powermon" → `power_mon`
   `DBSTATE_SHUTDOWN_AWSIOT:REBOOT`) before checking haptic_feedback recovery
3. For `TC_HAPTIC_4350` (camera crash): optionally cross-check
   `DBSTATE_SHUTDOWN_CAM_CRASH:REBOOT` in `power_mon.log` as corroborating evidence
4. For all TCs in this bucket: confirm `haptic_feedback.log` shows the startup banner
   (`Starting Haptic Service on tcp://127.0.0.1:6393`) and `service_status` reports
   `active` after the reboot/kill completes
5. If a `JSON parsing error ... too big integer` line appears in `haptic_feedback.log`
   after a reboot, report it as a secondary log anomaly (likely a `frame_gen_time`
   producer bug) rather than failing the TC solely because of it
6. Report Power On Reset (POR) queries as "validated via confluence, not yet
   automated in `tests/haptic/`" rather than PASS/FAIL
