---
name: critical-event-query-triage
description: "Use when analyzing critical-event table rows using CODE, CODE_AUX, DESCRIPTION, PROCESS_NAMES, OCCURRENCE_COUNT, and time context. Provides a standard narrowing method and confidence rubric."
---

# Critical Event Query Triage

Use this skill as the first step after fetching rows from critical-events storage.

## Input Contract

For each row, collect:

- `CODE`
- `CODE_AUX`
- `DESCRIPTION`
- `PROCESS_NAMES`
- event timestamp
- optional: device version/build, occurrence count, adjacent events in +/- 5 min window

## Narrowing Procedure

1. Resolve `CODE` to enum label using `nd_msg_types`.
2. Use service skill family inferred by enum prefix and/or process name.
3. Match `DESCRIPTION` against exact or pattern-matched strings documented in that service skill.
4. Decode `CODE_AUX` using service-specific aux semantics (enum, bitmask, return code, index, sentinel).
5. Enumerate all candidate emitter paths for this tuple.
6. Apply eliminators:
   - branch predicate mismatch
  - aux mismatch
   - process mismatch
   - timestamp mismatch with prerequisite logs/events
7. Assign confidence.

## Confidence Rubric

- `High`:
  - unique emitter path remains after tuple matching and corroborating logs match expected branch context.
- `Medium`:
  - multiple emitter paths remain but one path dominates based on aux/description and nearby events.
- `Low`:
  - only code-level mapping is possible, or emitter implementation is not present in this repo.

## Standard Output Format

Return analysis in this structure:

- `Event Signature`: code, aux, description pattern, process
- `Candidate Paths`: list of source branches that can emit this signature
- `Most Likely Cause`: branch + upstream failure chain
- `Why Not Others`: explicit elimination reasoning
- `Confidence`: High/Medium/Low with rationale
- `Verification Steps`: exact logs/states to confirm on device

## SQL-Oriented Usage

If triaging from DB directly, first query with tight filters:

```sql
SELECT code, code_aux, description, process_names, occurrence_count, timestamp
FROM sm_critical_events
WHERE code = ?
  AND process_names = ?
  AND description LIKE ?
ORDER BY timestamp DESC
LIMIT 200;
```

Then group by tuple to avoid mixing distinct causes under one code:

```sql
SELECT code, code_aux, description, process_names, COUNT(*) AS n
FROM sm_critical_events
WHERE code = ?
GROUP BY code, code_aux, description, process_names
ORDER BY n DESC;
```

## Important Rule

Never infer root cause from `CODE` alone when `DESCRIPTION` and `CODE_AUX` are available.
Same code can map to multiple operational causes.
