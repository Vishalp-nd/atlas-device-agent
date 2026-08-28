#!/usr/bin/env python3
"""Analyze one or more error CSVs produced by scripts/extract_error_logs.py
(optionally pre-grouped by scripts/batch_error_logs.py) and write markdown
bug reports, using the same Azure OpenAI tool-calling agent (list_process_logs
/ grep_log) and the same bug-section format as atlas/critical_bug_report_agent.py.

Each CSV row is a normalized error-message pattern (numbers/ids masked, so a
family of similar raw lines collapses into one row) together with the
device_id/date-range/source log file(s) it was found in. If the input has a
batch_id column (from scripts/batch_error_logs.py), rows sharing a batch_id
are investigated together in one conversation, since error patterns that
occurred close together in time on the same device are often symptoms of one
shared root cause. Rows without a batch_id are each treated as their own
batch of one, so plain extract_error_logs.py output still works directly.

For each batch, the agent greps the source log(s) for real occurrences,
follows hints into other logs as needed, and produces one bug section per
distinct root cause it identifies (usually one, but more if some patterns in
the batch turn out unrelated).

This produces two outputs: the markdown bug report (--output), and a JSONL
file of {normalized_message, source_file, reason} records (--reference,
default OUTPUT/known_error_reasons.jsonl) appended to after each batch, so
scripts/extract_error_logs.py can skip these patterns next time and the
records can later be loaded into a DB.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from atlas.critical_bug_report_agent import (
    DEFAULT_BUGS_DIR,
    DEFAULT_LOG_ROOT,
    MAX_ITERATIONS,
    TOOLS,
    _build_index,
    _configure_logfire,
    _dispatch_tool,
    _get_client,
)

SECTION_SEPARATOR = "\n\n---\n\n"
REASONS_SENTINEL = "<<<REASONS_JSON>>>"
DEFAULT_REFERENCE_PATH = os.path.join("OUTPUT", "known_error_reasons.jsonl")

SYSTEM_PROMPT = """You are a device-log bug analyst for Netradyne's Atlas platform.

You are given one or more normalized error-message patterns extracted from device logs
by an automated log-scanning tool. The tool already stripped each line's timestamp/pid/tid
prefix and masked any embedded numbers/ids, so lines that only differed by a value
collapse into one pattern: "count" is how many times a pattern occurred, and
"variant_count" is how many distinct raw messages (differing only by an embedded
number/id) were folded into it. You are also given one real example of the raw message,
which log file(s) it came from, the device_id, and the date range that was scanned.

When more than one pattern is given, they all come from the SAME device and their most
recent occurrences fell within a short time window of each other - they may be symptoms
of one shared root cause (e.g. a reboot, a crash, a network drop), or they may simply be
an unrelated coincidence. Investigate before assuming either way.

Device logs for that device are available under:

    <log_root>/<device_id>/<date>/logs/<file>.log

Investigate using list_process_logs and grep_log:
1. Call list_process_logs for the device and the date of the batch's "Most recent
   occurrence" timestamp to see what's available (that date is the most likely
   place to find real occurrences; fall back to other dates in the range if not).
2. grep_log the relevant source log file(s) for a distinctive substring of each
   pattern's example message to find real occurrences. If nothing is found on one
   date, try another date within the range.
3. Read the surrounding context to understand what led up to the error(s) and what
   happened after. Follow hints (process names, related keywords, nearby service
   start/stop lines) into service_mon.log or any other log that seems relevant, to
   build a chronological picture of what happened before, during, and after, and why
   it was logged.
4. If there are multiple patterns, you do not need to individually grep every single
   one - investigate enough of them to establish whether they share a root cause (or
   don't), and rely on the given counts/timestamps for the rest.
5. Every claim in your Steps/Flow section must be backed by a quoted log line.

Once you have enough evidence (or you run out of tool budget), respond with prose only
(no further tool calls). Produce ONE markdown bug entry per DISTINCT root cause you
identified among the given pattern(s) - usually one entry even if several patterns were
given, but more than one if the evidence shows some patterns are unrelated to the rest.
Each entry must have these sections, in this exact order and format:

## [<Process name>] <short bug title>

**Description:** <one-line description>

**Impact:** <impact of this bug>

**Recovery:** <how/whether the device recovers>

**Steps/Flow:**
1. <step> - `<quoted log line>`
2. <step> - `<quoted log line>`

**Occurrence Rate:** <occurrence count / rate, referencing the given occurrence count(s)>

**Device Details:**
- `<device_id>`
- <dates analyzed>
-most recent occurrence: <use the "Most recent occurrence" value given to you>
- OTA version

Add a final line starting with `Note:` ONLY if the evidence suggests this is not really a
bug - e.g. the device recovered on its own, or the "error" is expected/benign behavior.
Omit the Note line entirely otherwise.

If you produce more than one bug entry, separate them with a line containing only `---`
surrounded by blank lines, exactly like this document's own section separator. Do not
include any text before the first `## [` heading.

After the last bug entry (and Note line, if present), you MUST append a machine-readable
record of every pattern you were given, so it can be stored for future reference and
skip re-analysis of the same pattern. On its own line, write exactly:

<<<REASONS_JSON>>>

followed by a single JSON array with one object per pattern you were given (one object
per pattern, even if several patterns share the same bug entry), each with exactly these
keys:

  "normalized_message": <the pattern's "Normalized message pattern", copied exactly>
  "source_file": <one of the pattern's source log file names>
  "reason": <a concise 1-2 sentence explanation of why this pattern occurs, consistent
             with the Description of whichever bug entry covers it>

If a pattern's "Source log file(s)" lists more than one file, include one object per
file (all sharing the same "reason"). Attribute each pattern to whichever bug entry
actually explains it - if some patterns turned out unrelated to the rest and got their
own separate bug entry, their "reason" must reflect that entry, not another one's.

Nothing may follow the JSON array.
"""


def _read_rows(csv_paths: list[str]) -> list[dict]:
    rows = []
    for csv_path in csv_paths:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["source_files"] = row["source_files"].split("|")
                rows.append(row)
    return rows


def _group_rows_into_batches(rows: list[dict]) -> list[list[dict]]:
    """Group rows by batch_id (from scripts/batch_error_logs.py) if present,
    preserving first-seen order. Rows without a batch_id each become their
    own batch of one, so plain extract_error_logs.py output still works."""
    if not rows or "batch_id" not in rows[0]:
        return [[row] for row in rows]

    batches: dict[str, list[dict]] = {}
    for row in rows:
        batches.setdefault(row["batch_id"], []).append(row)
    return list(batches.values())


def _build_batch_prompt(batch: list[dict]) -> str:
    device_id = batch[0]["device_id"]
    start_date = min(r["start_date"] for r in batch)
    end_date = max(r["end_date"] for r in batch)
    if "batch_start" in batch[0]:
        time_span = f"{batch[0]['batch_start']} to {batch[0]['batch_end']}"
        most_recent = batch[0]["batch_end"]
    else:
        most_recent = max(r["last_seen"] for r in batch)
        time_span = most_recent

    pattern_blocks = []
    for i, row in enumerate(batch, start=1):
        process_hint = Path(row["source_files"][0]).stem
        pattern_blocks.append(
            f"Pattern {i}:\n"
            f"  Process hint (source log file name): {process_hint}\n"
            f"  Source log file(s): {', '.join(row['source_files'])}\n"
            f"  Normalized message pattern: {row['normalized_message']}\n"
            f"  Example raw message: {row['example_message']}\n"
            f"  Occurrence count: {row['count']}\n"
            f"  Distinct raw variants folded into this pattern: {row['variant_count']}\n"
            f"  Last seen: {row['last_seen']}"
        )

    return (
        f"Device ID: {device_id}\n"
        f"Date range scanned: {start_date} to {end_date}\n"
        f"Batch time span (most recent occurrences of these patterns): {time_span}\n"
        f"Number of patterns in this batch: {len(batch)}\n\n"
        + "\n\n".join(pattern_blocks)
        + f"\n\nMost recent occurrence (from the log scan): {most_recent}\n"
        f"OTA version: unknown\n"
    )


def analyze_batch(client, deployment: str, batch: list[dict], log_root: Path,
                   max_iterations: int = MAX_ITERATIONS) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_batch_prompt(batch)},
    ]
    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=deployment, messages=messages, tools=TOOLS, tool_choice="auto", temperature=0.2,
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            return message.content or ""
        for call in message.tool_calls:
            result = _dispatch_tool(call, log_root)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    messages.append({
        "role": "user",
        "content": "Iteration limit reached. Produce your best bug report(s) now, in the exact markdown format requested (including the trailing <<<REASONS_JSON>>> block), using only the evidence already gathered.",
    })
    final = client.chat.completions.create(model=deployment, messages=messages, temperature=0.2)
    return final.choices[0].message.content or ""


def _split_reasons_json(raw_response: str) -> tuple[str, list[dict]]:
    """Split off the trailing <<<REASONS_JSON>>> block. Degrades gracefully
    (markdown kept, no records) if the model didn't include a well-formed
    one, rather than failing the whole batch."""
    if REASONS_SENTINEL not in raw_response:
        logging.getLogger("atlas.log_bug_report_agent").warning(
            "Response missing %s sentinel; no reasons recorded for this batch.", REASONS_SENTINEL
        )
        return raw_response.strip(), []

    markdown_part, _, json_part = raw_response.partition(REASONS_SENTINEL)
    try:
        records = json.loads(json_part.strip())
    except json.JSONDecodeError as exc:
        logging.getLogger("atlas.log_bug_report_agent").warning(
            "Failed to parse %s block: %s", REASONS_SENTINEL, exc
        )
        return markdown_part.strip(), []
    return markdown_part.strip(), records


def _append_reasons(reference_path: str, records: list[dict]) -> None:
    if not records:
        return
    known_pairs = set()
    if os.path.isfile(reference_path):
        with open(reference_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    existing = json.loads(line)
                    known_pairs.add((existing["normalized_message"], existing["source_file"]))

    new_lines = []
    for record in records:
        key = (record.get("normalized_message"), record.get("source_file"))
        if key in known_pairs:
            continue
        known_pairs.add(key)
        new_lines.append(json.dumps(record))

    if not new_lines:
        return
    os.makedirs(os.path.dirname(reference_path) or ".", exist_ok=True)
    with open(reference_path, "a", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", nargs="+", required=True, help="One or more CSV files produced by scripts/extract_error_logs.py (optionally batched by scripts/batch_error_logs.py).")
    parser.add_argument("--output", default=None, help="Output markdown path. Defaults to OUTPUT/critical_bugs/log_bugs_<timestamp>.md.")
    parser.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT), help="Root directory containing <device_id>/<date>/logs/ dirs.")
    parser.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS, help="Max tool-calling iterations per batch.")
    parser.add_argument("--only", default=None, help="Limit analysis to rows whose device_id or normalized_message contains this substring (for prompt iteration).")
    parser.add_argument(
        "--reference",
        default=DEFAULT_REFERENCE_PATH,
        help=f"JSONL file to append newly-explained {{normalized_message, source_file, reason}} records to, for scripts/extract_error_logs.py to skip next time. Default: {DEFAULT_REFERENCE_PATH}",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _configure_logfire()

    rows = _read_rows(args.csv)
    if args.only:
        rows = [r for r in rows if args.only in r["device_id"] or args.only in r["normalized_message"]]
    batches = _group_rows_into_batches(rows)

    log_root = Path(args.log_root)
    output_path = Path(args.output) if args.output else DEFAULT_BUGS_DIR / f"log_bugs_{datetime.now():%Y%m%d_%H%M%S}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client, deployment = _get_client()

    sections = []
    all_records = []
    for batch in batches:
        process_hint = Path(batch[0]["source_files"][0]).stem
        try:
            raw_result = analyze_batch(client, deployment, batch, log_root, args.max_iterations).strip()
            markdown_part, records = _split_reasons_json(raw_result)
            sections.extend(piece.strip() for piece in markdown_part.split(SECTION_SEPARATOR) if piece.strip())
            all_records.extend(records)
        except Exception as exc:
            sections.append(f"## [{process_hint}] ANALYSIS FAILED\n\nAgent error: {exc}")

    index = _build_index(sections, ["-"] * len(sections))
    header = f"# Log Error Bug Report - {', '.join(Path(p).name for p in args.csv)}\nGenerated: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
    body = SECTION_SEPARATOR.join(sections)
    output_path.write_text(f"{header}\n{index}\n---\n\n{body}\n", encoding="utf-8")
    _append_reasons(args.reference, all_records)
    print(output_path)
    print(f"Recorded {len(all_records)} pattern reason(s) to {args.reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
