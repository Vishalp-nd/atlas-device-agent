---
name: haptic-database-operations
description: "Use when: validating haptic_feedback accessory.db read behavior from device logs. Covers the ACCESSORY table row-count check for haptic hardware pairing/provisioning."
argument-hint: "device ID (e.g., /haptic-database-operations 440073)"
---

# Haptic — Database Operations (Flow 5)

> Part of the [haptic-service-validation](../SKILL.md) skill family. This flow covers
> `functionality_map.py`'s `"Database Operations"` bucket.

## What happens

`haptic_feedback` reads its accessory serial number from `accessory.db` at
`/home/ubuntu/.nddevice/accessory.db`. The `ACCESSORY` table must contain at least one
row — an empty table means the haptic hardware was never paired/provisioned and the
service cannot report its serial number correctly.

**When active:** Always (DB read on service init)
**Frequency:** Checked once (DB is populated at provisioning time, not per-session)
**Cross-service impact:** None — local sqlite3 read only
**is_cloud_dependent:** 0 **is_analytics_dependent:** 0

## Test cases that validate this flow

| Test Case ID    | Python File                                                    | What it checks                                       |
| --------------- | ----------------------------------------------------------------- | ------------------------------------------------------- |
| `TC_HAPTIC_4353`| `tests/haptic/test_tc_haptic_4353_accessory_db_check.py`         | `SELECT * FROM ACCESSORY` returns at least one row     |

## Related confluence evidence (DQA — Haptic Module Software Functional Checks)

The confluence page's **"Haptic Accessory Detection"** scenario (validate the system
detects the haptic motor as an accessory, motor stays OFF except during DMS drowsy
alerts) is marked **FAIL** — root cause: `AN-28721`, no logging exists to confirm
motor detection at boot. `TC_HAPTIC_4353` only validates the DB-row precondition for
pairing; it does NOT independently confirm that `haptic_feedback` logs a
detection/pairing confirmation line. If asked to verify "accessory detected" beyond a
non-empty `ACCESSORY` table, flag `AN-28721` as the reason no such log line exists.

## Flow Manifest (machine-readable)

```json
{
  "flows": [
    {
      "name": "ACCESSORY Table Row-Count Check",
      "description": "SELECT * FROM ACCESSORY in accessory.db returns at least one row, confirming haptic hardware was paired/provisioned.",
      "flow_skill_path": ".github/skills/haptic-service-validation/database-operations/SKILL.md",
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
2. Run/verify the `sqlite3` query against `accessory.db` returns ≥1 row for the
   `ACCESSORY` table — no log grep is needed for this TC
3. Do not conflate "DB has a row" with "detection log exists" — the latter is a known
   gap (`AN-28721`), not covered by this TC
