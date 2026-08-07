---
name: accessory-table-row-count-check
description: "Use when: validating the ACCESSORY table contents in accessory.db for haptic provisioning. Covers the row-count check that confirms the hardware was paired or provisioned."
argument-hint: "device ID (e.g., /accessory-table-row-count-check 440073)"
---

# Haptic — Flow 18: ACCESSORY Table Row-Count Check

## What happens

`haptic_feedback` depends on `accessory.db` for accessory serial and provisioning data.
This flow checks that the `ACCESSORY` table contains at least one row, which confirms
the haptic hardware was paired or provisioned.

**When active:** On-demand DB validation
**Frequency:** Usually once per provisioning or validation cycle
**Cross-service impact:** None; local sqlite read only
**Automated:** Yes
**Flow type:** Positive
**Cloud dependent:** No
**Analytics dependent:** No

## Key evidence

- `SELECT * FROM ACCESSORY` returns at least one row

## Source coverage

| Test Case ID | Existing Source | What it validates |
| ------------ | --------------- | ----------------- |
| `TC_HAPTIC_4353` | `tests/haptic/test_tc_haptic_4353_accessory_db_check.py` | non-empty ACCESSORY table |

## Pass criteria

- The `ACCESSORY` table contains at least one row

## Fail signals

- The `ACCESSORY` table is empty or unreadable

## Validation instructions

1. Confirm device type is `bagheera3` or `octo`
2. Run or verify the sqlite query against `accessory.db`
3. Do not confuse a non-empty table with proof of boot-time accessory-detection logging