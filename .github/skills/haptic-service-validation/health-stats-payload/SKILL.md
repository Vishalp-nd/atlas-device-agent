---
name: haptic-health-stats-payload
description: "Use when: validating the haptic_feedback HealthStatsManager payload from device logs — JSON payload format, process info when the service is enabled/disabled, memory usage (normal + leak test), CPU usage during a haptic alert trigger, and missing-fields negative case. Not yet automated in tests/haptic/; use DQA Confluence expected-behavior reference."
argument-hint: "device ID (e.g., /haptic-health-stats-payload 440073)"
---

# Haptic — Health Stats Payload (Flow 11)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> the **Health Stats Payload** section of the DQA Confluence page
> [Haptic_Feedback Service Checks](https://netradyne.atlassian.net/wiki/spaces/DQA/pages/2182873155/Haptic_Feedback+Service+Checks).
> Not present in `functionality_map.py`'s `"haptic"` bucket list — no automated
> `tests/haptic/` TC exists for this flow today. (The P95 CPU/memory benchmark table
> from the same confluence page is out of scope for this skill.)

## What happens

`haptic_feedback` reports a JSON health-stats payload to `HealthStatsManager` covering
process info (present whether the service is enabled or disabled), memory usage, and
CPU usage (notably during an active haptic alert trigger). The payload's JSON
structure/format is validated independently of its field content.

**When active:** N/A — not yet automated
**Frequency:** N/A
**Cross-service impact:** `HealthStatsManager` (payload consumer)
**is_cloud_dependent:** 0 **is_analytics_dependent:** 0

> **Not yet automated.** No file under `tests/haptic/` currently implements this flow —
> it is not present in `functionality_map.py`'s `"haptic"` bucket list. Do not report a
> PASS/FAIL verdict for it; report `NOT_AUTOMATED` if asked about this bucket.

**Test cases that validate this flow:** *(none — not in `functionality_map.py`, see note above)*

## Expected behavior reference (DQA Confluence — Haptic_Feedback Service Checks)

| Scenario | Confluence Result | Notes |
| -------- | ------------------ | ----- |
| Payload Format — validate JSON | PASS | — |
| Process Info — when haptic service enabled | PASS | Artifact device `103392300431` |
| Process Info — when haptic service disabled | PASS | Artifact device `103402300071` |
| Memory Usage — normal usage | PASS | — |
| Memory Usage — memory leak test | PASS | — |
| CPU Usage — when haptic alert triggers | PASS | — |
| Negative Cases — missing fields in payload | **FAIL** — `AN-28689` | Same ticket already referenced by [alert-session-flow](../alert-session-flow/SKILL.md)'s "Observation payload includes haptic status" and [service-status-lifecycle](../service-status-lifecycle/SKILL.md)'s known-gaps section — this is the same underlying payload-completeness gap, not a distinct new issue |

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. Report `NOT_AUTOMATED` for this bucket — there is no `tests/haptic/` file to
   execute and no TC ID assigned in `functionality_map.py`
3. If asked to manually assess the health-stats payload, confirm it is well-formed
   JSON and that process-info fields are present in both the enabled and disabled
   states before checking memory/CPU figures
4. Do NOT report a fresh FAIL for "missing fields in payload" (`AN-28689`, unresolved)
   as a new regression — it is the same known, tracked gap referenced in
   [alert-session-flow](../alert-session-flow/SKILL.md) and
   [service-status-lifecycle](../service-status-lifecycle/SKILL.md); do not treat the
   three mentions as three separate bugs
5. Do not conflate this payload-content check with the `sending haptic health info to
   hs:` log line covered in
   [audio-ignition-behavior](../audio-ignition-behavior/SKILL.md) — that verifies a
   log line exists, this flow verifies the actual JSON payload structure/content sent
   to `HealthStatsManager`

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Health Stats Payload",
      "description": "haptic_feedback HealthStatsManager JSON payload: format validation, process info when enabled/disabled, memory usage (normal + leak test), CPU usage during an alert trigger, and missing-fields negative case (AN-28689). Not yet automated.",
      "flow_skill_path": ".github/skills/haptic-service-validation/health-stats-payload/SKILL.md",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0
    }
  ]
}
```
