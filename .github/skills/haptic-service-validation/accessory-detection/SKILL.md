---
name: haptic-accessory-detection
description: "Use when: validating haptic_feedback hardware/accessory detection from device logs — motor stays OFF at boot except during DMS drowsy alerts, motor turns ON upon drowsy-alert detection, and vibration-on duration measurement. Not yet automated in tests/haptic/; use DQA Confluence expected-behavior reference."
argument-hint: "device ID (e.g., /haptic-accessory-detection 440073)"
---

# Haptic — Accessory Detection (Flow 12)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> the **Haptic Accessory Detection** section of the DQA Confluence page
> [Haptic Module – Software Functional Checks](https://netradyne.atlassian.net/wiki/spaces/DQA/pages/2147155976/Haptic+Module+Software+Functional+Checks).
> Not present in `functionality_map.py`'s `"haptic"` bucket list — no automated
> `tests/haptic/` TC exists for this flow today.

## What happens

The system should detect the haptic motor as a connected accessory at boot, keep it in
an OFF state, and only turn it ON when a DMS drowsy alert is detected — then measure
how long the vibration motor stays ON. Validation is meant to check hardware status via
the installer app / logs, but the confluence run reports this as **FAIL**: there is no
log line confirming motor detection at service start (`AN-28721`).

**When active:** N/A — not yet automated
**Frequency:** N/A
**Cross-service impact:** `dmsAnalyticsClient` (drowsy alert trigger), `haptic_feedback`
(motor ON/OFF), installer app (hardware status)
**is_cloud_dependent:** 0 **is_analytics_dependent:** 1

> **Not yet automated.** No file under `tests/haptic/` currently implements this flow —
> it is not present in `functionality_map.py`'s `"haptic"` bucket list. Do not report a
> PASS/FAIL verdict for it; report `NOT_AUTOMATED` if asked about this bucket.

**Test cases that validate this flow:** *(none — not in `functionality_map.py`, see note above)*

## Expected behavior reference (DQA Confluence — Haptic Module Software Functional Checks)

| Scenario | Confluence Result | Notes |
| -------- | ------------------ | ----- |
| Validate system detects haptic motor as accessory; motor remains OFF; turns ON only on DMS drowsy alert; measure vibration-ON duration | **FAIL** — `AN-28721` | No logging exists to confirm motor detection at boot. Same ticket already referenced in [service-status-lifecycle](../service-status-lifecycle/SKILL.md)'s known-gaps section and [logging-operations](../logging-operations/SKILL.md)'s Log Creation scenario — do not treat as three separate bugs |

**Reference alerts (confluence artifacts):**
- Alert with haptic motor connected — `https://idms-staging.netradyne.com/console/#/alerts/70882730`
- Alert without haptic motor connection — `https://idms-staging.netradyne.com/console/#/alerts/71067219`

## Related confluence pages (not fetched — pointers only)

- **Installer App Basic Checks - New Accessory** — a separate confluence page,
  "Installer App Checks - Haptic Motor(New Accessory)", covers installer-app-level
  hardware detection checks for the haptic motor as a new accessory. Not fetched as
  part of this skill — use the `jira-confluence-fetch` skill to pull it directly if
  deeper installer-app validation is needed.
- **Configuration Testing** — see [config](../config/SKILL.md)'s existing pointer to
  the "Configuration Tests - DMS + Haptic Motor" page; not duplicated here.

## Validation Instructions for the Agent

1. Confirm device type is `bagheera3` or `octo` before running any check
2. Report `NOT_AUTOMATED` for this bucket — there is no `tests/haptic/` file to
   execute and no TC ID assigned in `functionality_map.py`
3. If asked to manually assess accessory detection, look for a DMS drowsy alert
   (`raising drowsy alert {...}` in `dmsanalytics.log`) followed by motor-ON evidence
   in `haptic_feedback.log` (e.g. `Periodic health: output HIGH, motor running`) —
   do NOT expect a dedicated "accessory detected" log line at boot, since none exists
4. Do NOT report a fresh FAIL for "no logging for motor detection" (`AN-28721`,
   unresolved) as a new regression — it is a known, tracked gap
5. If asked about installer-app-level hardware checks specifically, fetch the
   "Installer App Checks - Haptic Motor(New Accessory)" confluence page rather than
   assuming its content

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "Haptic Accessory Detection",
      "description": "System detects the haptic motor as a connected accessory at boot, keeps it OFF except during DMS drowsy alerts, turns it ON upon drowsy-alert detection, and vibration-ON duration is measured. FAIL — no motor-detection logging exists (AN-28721). Not yet automated.",
      "flow_skill_path": ".github/skills/haptic-service-validation/accessory-detection/SKILL.md",
      "is_cloud_dependent": 0,
      "is_analytics_dependent": 1
    }
  ]
}
```
