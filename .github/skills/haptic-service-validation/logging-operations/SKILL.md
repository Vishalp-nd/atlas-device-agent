---
name: haptic-logging-operations
description: "Use when: validating haptic_feedback log lifecycle behavior from device logs — log creation on start/stop/restart, error logging on failure, size/time-based rotation, upload (including under network failure), and deletion (retention policy + manual). Not yet automated in tests/haptic/; use DQA Confluence expected-behavior reference."
argument-hint: "device ID (e.g., /haptic-logging-operations 440073)"
---

# Haptic — Logging Operations (Flow 9)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> the **Logging (Create / Upload / Delete)** section of the DQA Confluence page
> [Haptic_Feedback Service Checks](https://netradyne.atlassian.net/wiki/spaces/DQA/pages/2182873155/Haptic_Feedback+Service+Checks).
> Not present in `functionality_map.py`'s `"haptic"` bucket list — no automated
> `tests/haptic/` TC exists for this flow today.

## What happens

`haptic_feedback` writes to `/home/ubuntu/.nddevice/log/haptic_feedback/*` on every
start/stop/restart, rotates log files by size/time, uploads logs to the server
(including a retry/resilience path under network failure), and deletes old logs per
retention policy or on manual request.

**When active:** N/A — not yet automated
**Frequency:** N/A
**Cross-service impact:** Log uploader/retention pipeline (shared with other services)
**is_cloud_dependent:** 1 (log upload to server) **is_analytics_dependent:** 0

> **Not yet automated.** No file under `tests/haptic/` currently implements this flow —
> it is not present in `functionality_map.py`'s `"haptic"` bucket list. Do not report a
> PASS/FAIL verdict for it; report `NOT_AUTOMATED` if asked about this bucket.

**Test cases that validate this flow:** *(none — not in `functionality_map.py`, see note above)*

## Expected behavior reference (DQA Confluence — Haptic_Feedback Service Checks)

| Scenario | Confluence Result | Notes |
| -------- | ------------------ | ----- |
| Log Creation — log generated on service start/stop/restart | **FAIL** — `AN-28721` | Same ticket referenced in [service-status-lifecycle](../service-status-lifecycle/SKILL.md)'s "Motor/accessory detection has no logging" gap — appears to cover multiple missing-log scenarios, not just accessory detection |
| Error logs on failure | **FAIL** — `BGR3-1391` | Error-path logging is incomplete/missing |
| Log Rotation — rotation on size/time | PASS | — |
| Upload Logs — successful upload to server | PASS | — |
| Upload Logs — upload with network failure | PASS | Resilient to transient network loss |
| Delete Logs — automatic deletion (retention policy) | PASS | — |
| Delete Logs — manual deletion | PASS | — |
| Negative Cases — missing log directory | PASS | Service tolerates a missing log directory |

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. Report `NOT_AUTOMATED` for this bucket — there is no `tests/haptic/` file to
   execute and no TC ID assigned in `functionality_map.py`
3. If asked to manually assess log creation/rotation/upload/deletion, use the
   confluence expectations table above as the target behavior
4. Do NOT report a fresh FAIL for "log generated on service start/stop/restart"
   (`AN-28721`, unresolved) or "error logs on failure" (`BGR3-1391`, unresolved) as new
   regressions — these are known, tracked issues unless a fresh, different symptom is
   observed
5. Do not confuse `AN-28721` here (log line existence) with the same ticket referenced
   in [service-status-lifecycle](../service-status-lifecycle/SKILL.md) for
   accessory/motor-detection logging — confirm which specific log line is missing
   before attributing a failure to this ticket

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Logging Operations",
      "description": "haptic_feedback log lifecycle: creation on start/stop/restart, error logging on failure, size/time-based rotation, upload (incl. under network failure), and deletion (retention policy + manual). Not yet automated.",
      "flow_skill_path": ".github/skills/haptic-service-validation/logging-operations/SKILL.md",
      "is_cloud_dependent": 1,
      "is_analytics_dependent": 0
    }
  ]
}
```
