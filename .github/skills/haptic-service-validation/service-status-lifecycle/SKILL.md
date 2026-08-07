---
name: haptic-service-status-lifecycle
description: "Use when: validating haptic_feedback service status and lifecycle from device logs. Covers SIGABRT crash respawn (service_mon), systemctl stop/restart/enable/disable, installed file permissions, and dependent-service (dmsAnalyticsClient/audioPlayback/bagheera/cam_rec/uploader/analyticsService) status after a haptic_feedback restart."
argument-hint: "device ID (e.g., /haptic-service-status-lifecycle 440073)"
---

# Haptic — Service Status & Lifecycle (Flow 1)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> `functionality_map.py`'s `"Service Status & Lifecycle"` bucket.

## What happens

`haptic_feedback` must recover cleanly from a crash and support full systemd lifecycle
control. On SIGABRT (`kill -6`), `service_mon` logs `Service started: HPTC :` on
respawn and the service (plus its dependents — `dmsAnalyticsClient`, `audioPlayback`,
`bagheera`, `cam_rec`, `uploader`, `analyticsService`) must report `active` again.
Independently, the service supports `stop` → inactive, `restart` → active,
`enable`/`disable` via `systemctl is-enabled` (disabling does NOT stop the currently
running process), and its installed files must carry expected permissions (`.sh`
scripts and binary `-rwxr-xr-x`, `.service` unit `-rw-r--r--`).

**When active:** Always (`[haptic_feedback] enabled=1`)
**Frequency:** On crash (SIGABRT) or on-demand systemctl operations
**Cross-service impact:** `service_mon` tracks `HPTC` start/stop/error; dependent
services listed above are expected to remain unaffected by a haptic_feedback restart
**is_cloud_dependent:** 0 **is_analytics_dependent:** 0

## Key log patterns (service_mon.log)

- `Service started: HPTC : <epoch>`
- `Service error: HPTC :`

## Test cases that validate this flow

| Test Case ID    | Python File                                                                 | What it checks                                                              |
| --------------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `TC_HAPTIC_4157`| `tests/haptic/test_tc_haptic_4157_haptic_service_status.py`                 | SIGABRT → HPTC respawn, service + 6 dependent services active before/after   |
| `TC_HAPTIC_4345`| `tests/haptic/test_tc_haptic_4345_service_lifecycle_and_permissions.py`     | stop→inactive, restart→active, enable/disable via systemctl, file permissions |

## Known gaps (from DQA Confluence — Haptic Module Software Functional Checks)

- **Motor/accessory detection has no logging** (`AN-28721`) — the confluence test
  "Haptic Accessory Detection" (validate system detects the haptic motor as an
  accessory and it stays OFF except during DMS drowsy alerts) is marked **FAIL**
  because no log line confirms motor detection at service start. If asked to verify
  accessory/hardware detection beyond `TC_HAPTIC_4157`'s dependent-service-status
  check, expect this gap — do not assume a detection log line exists.
- **Haptic motor disconnection during a drive is not handled correctly** (`BGR3-1394`,
  linked from parent bug query `BGR3-1388`) — a runtime hot-unplug of the haptic
  accessory is not currently covered by any automated `tests/haptic/` TC. Treat this
  as a known coverage gap, not a regression, unless a new TC is added for it.
- **Observation payload / healthstat report omits haptic status** (`AN-28689`) —
  confluence test "Observation and Healthstat Checks" (validate metadata report
  includes haptic status) is marked **FAIL**. If validating `HealthStatsManager`
  payload content (not just the `sending haptic health info to hs:` log line), expect
  the haptic status field may be missing from the observation/metadata report. See
  [health-stats-payload](../health-stats-payload/SKILL.md) for the dedicated payload
  check that also hits this same ticket.

## Additional confluence evidence (DQA — Haptic_Feedback Service Checks)

Beyond `TC_HAPTIC_4157`/`TC_HAPTIC_4345`, the confluence page's **"Service Status &
Mode"** and **"Dependent Services"** sections independently confirm:

| Scenario | Confluence Result | Notes |
| -------- | ------------------ | ----- |
| Start Service — start when already running | PASS | No-op / idempotent start |
| Stop Service — stop when already stopped | PASS | No-op / idempotent stop |
| Restart Service — restart when dependencies are down | PASS | Restart succeeds even if a dependent service is unavailable |
| Service Dependency — dependency available | PASS | Artifacts: `dmsAnalyticsClient` and `audioPlayback` service |
| Queue Creation — auto-create queue at service startup | PASS | A ZMQ queue is created between `dmsAnalyticsClient` and `audioPlayback` service |
| Queue Creation — queue already exists | PASS | Startup tolerates a pre-existing queue |

**Validation guidance:** these are confluence-only scenarios (not covered by a
dedicated `tests/haptic/` assertion) — treat as supporting evidence rather than a
standalone TC verdict unless asked to validate them specifically.

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "SIGABRT Crash Respawn",
      "description": "haptic_feedback recovers from a SIGABRT (kill -6) crash; service_mon logs the respawn and the service plus its 6 dependents (dmsAnalyticsClient, audioPlayback, bagheera, cam_rec, uploader, analyticsService) report active again.",
      "flow_skill_path": ".github/skills/haptic-service-validation/service-status-lifecycle/SKILL.md",
      "automated": 1,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Service Lifecycle Control & File Permissions",
      "description": "systemctl stop\u2192inactive, restart\u2192active, enable/disable (disable does not stop the running process), and installed file permission checks (.sh/binary -rwxr-xr-x, .service unit -rw-r--r--).",
      "flow_skill_path": ".github/skills/haptic-service-validation/service-status-lifecycle/SKILL.md",
      "automated": 1,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    }
  ]
}
```

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. For SIGABRT respawn: verify `Service error: HPTC :` followed by `Service started:
   HPTC : <epoch>` in `service_mon.log`, then confirm all 6 dependent services report
   `active` via `service_status`
3. For lifecycle control: verify `systemctl is-enabled`/`is-active` output transitions
   match the expected state per step, and that `disable` does NOT stop an already
   running process
4. For file permissions: compare `get_file_permissions` output against the expected
   mode table in `TC_HAPTIC_4345`'s own assertions (`-rwxr-xr-x` for scripts/binary,
   `-rw-r--r--` for the `.service` unit)
5. Do not report a FAIL for accessory-detection logging or observation-payload haptic
   status unless explicitly asked to validate those — they are known gaps (see above),
   not covered by `TC_HAPTIC_4157`/`TC_HAPTIC_4345`
