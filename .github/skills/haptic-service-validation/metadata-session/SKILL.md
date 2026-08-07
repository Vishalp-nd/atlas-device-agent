---
name: haptic-metadata-session
description: "Use when: validating haptic metadata/session leftover behavior — partial sessions, metadata-leftover sessions, and privacy sessions where the haptic_events section must be empty or absent. Not yet automated in tests/haptic/; use DQA Confluence expected-behavior reference."
argument-hint: "device ID (e.g., /haptic-metadata-session 440073)"
---

# Haptic — Metadata & Session (Flow 6)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> `functionality_map.py`'s `"Metadata & Session"` bucket.

## What happens

Per the functionality map, this bucket covers partial-session metadata leftover
behavior for haptic health data (session ends abruptly — e.g. reboot mid-session — and
any partial `haptic_events.json`/session metadata must not leak into the next
session).

**When active:** N/A — not yet automated
**Frequency:** N/A
**Cross-service impact:** N/A
**is_cloud_dependent:** 0 **is_analytics_dependent:** 0

> **Not yet automated.** No file under `tests/haptic/` currently implements this TC
> (`TC_HAPTIC_4175`) — `functionality_map.py` still reserves this bucket for future
> coverage. Do not report a PASS/FAIL verdict for it; report `NOT_AUTOMATED` if asked
> about this bucket.

**Test cases that validate this flow:** *(none — placeholder bucket, see note above)*

## Expected behavior reference (DQA Confluence — Haptic Module Software Functional Checks)

Until this bucket is automated, use these confluence-confirmed expectations from the
**"Haptic Behaviour on Different Types of Sessions"** and **"Behavior across different
session types"** test matrices as the ground truth for what a future TC should assert:

| Session Type                              | Confluence Result | Expected Haptic Behavior                                                                 |
| ------------------------------------------ | ------------------ | ------------------------------------------------------------------------------------------ |
| Last-Minute / Partial Session              | PASS               | No metadata processed for the partial file; `Haptic_events` section is **not present**    |
| Metadata Leftovers Session                 | PASS               | `haptic_event` section is **empty** under inference data for the metadata-leftover file    |
| Privacy Session                            | PASS               | `haptic_event` section is **empty** under inference data for the privacy file              |
| Non-Alert Session                          | PASS               | Haptic remains OFF — no DMS drowsy alert/audio events in session                           |
| Session when Ignition LOW                  | PASS               | Haptic remains OFF — analytics doesn't run during ignition-OFF                             |
| Session when Device woke up in LPM         | PASS               | Haptic remains OFF — analytics doesn't run during LPM                                      |
| First Minute Session                       | PASS               | `haptic_feedback` service up and running within 12 seconds from bootup                     |
| First Minute Session with Alert            | Not Evaluated      | Edge case — generating a DMS drowsy alert in the very first session is hard to reproduce in lab/drive; monitored from field drives |
| Session with Multiple Alerts (back-to-back)| Not Evaluated      | Edge case — back-to-back DMS drowsy alerts in one session is hard to reproduce in lab/drive; monitored from field drives |

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Last-Minute / Partial Session",
      "description": "Session ends abruptly (e.g. reboot mid-session) \u2014 no metadata processed for the partial file, Haptic_events section must not be present.",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/alert-session-flow/SKILL.md"]
    },
    {
      "name": "Metadata Leftovers Session",
      "description": "haptic_event section must be empty under inference data for the metadata-leftover file.",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/alert-session-flow/SKILL.md"]
    },
    {
      "name": "Privacy Session",
      "description": "haptic_event section must be empty under inference data for the privacy file (no data upload, no haptic in privacy mode).",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/alert-session-flow/SKILL.md"]
    },
    {
      "name": "Non-Alert Session",
      "description": "Haptic remains OFF \u2014 no DMS drowsy alert/audio events in a session with no triggering event.",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Session when Ignition LOW",
      "description": "Haptic remains OFF because analytics does not run during ignition-OFF, so no DMS drowsy/audio events are produced.",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "Session when Device Woke Up in LPM",
      "description": "Haptic remains OFF \u2014 analytics doesn't run during low-power mode.",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "First Minute Session",
      "description": "haptic_feedback service is up and running within 12 seconds from bootup.",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "positive",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": []
    },
    {
      "name": "First Minute Session with Alert",
      "description": "Edge case: a DMS drowsy alert generated in the very first session \u2014 hard to reproduce in lab/drive, monitored from field drives, Not Evaluated.",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/alert-session-flow/SKILL.md"]
    },
    {
      "name": "Session with Multiple Alerts (back-to-back)",
      "description": "Edge case: back-to-back DMS drowsy alerts in one session \u2014 hard to reproduce in lab/drive, monitored from field drives, Not Evaluated.",
      "flow_skill_path": ".github/skills/haptic-service-validation/metadata-session/SKILL.md",
      "automated": 0,
      "flow_type": "negative",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 0,
      "dependent_flows": [".github/skills/haptic-service-validation/alert-session-flow/SKILL.md"]
    }
  ]
}
```

## Validation Instructions for the Agent

1. Report `NOT_AUTOMATED` for this bucket — there is no `tests/haptic/` file to
   execute for `TC_HAPTIC_4175`
2. If asked to manually assess metadata/session leftover behavior from raw device
   logs, use the confluence expectations table above as the target behavior: for a
   partial/metadata-leftover/privacy session, the `haptic_events`/`haptic_event`
   section in the session's inference/metadata output should be **absent or empty**,
   never populated
3. Do not confuse this bucket with [Flow 7: Alert Session Flow](../alert-session-flow/SKILL.md)
   (`TC_HAPTIC_4354`), which covers a normal, complete alert session — this bucket is
   specifically about abrupt/partial/privacy session edge cases
