ALTER TABLE unique_cinfo_priority_map
UPDATE "TYPE" = 'INFO'
WHERE "CODE" = 41014
  AND description_pattern IS NOT NULL
  AND description_pattern NOT IN (
    'A_IMU: Data Outage Count: <N>',
    'A_GPS: Data Outage Count: <N>',
    'A_ENG_STAT: Data Outage Count: <N>',
    'A_PWR_VOLT: Data Outage Count: <N>',
    'WOIGN: FAIL : E : FAIL, WOMOT: FAIL : D : FAIL, WOSMS: FAIL : D : FAIL, LUMIA: FAIL : E : FAIL',
    'A_ENG_STAT: Data Outage Clear',
    'A_GPS: Data Outage Clear',
    'A_IGNS: D: (IGN: ON Prev: ERR <N> OFF V: <N>',
    'A_IMU: Data Outage Clear',
    'A_PWR_VOLT: Data Outage Clear',
    'A_PWR_VOLT: D: (VOLT: C: <N> <N> <N> false, BadBattery: true, FusionActive: true) ',
    'Glitch Detected: <N> <N>',
    'Glitch Detected: <N> <N> V: <N> <N>'
  );"""
Extract ERROR-tagged log lines for a device over a date range.

Scans OUTPUT/log/<device>/<date>/logs/*.log for every date in the given
range, pulls out lines tagged as errors in either log format used on the
device:

  1. Python-style:  2026-08-18 23:00:35,898 - root - ERROR - message
  2. Native format:  1787091660866: 36120263: PERS_DB: E: 3773: 372163: message

Each line has a variable prefix (timestamp, pid/tid, sequence number) that
makes otherwise-identical messages look unique, so counting is done on the
log message only, with the variable prefix stripped off. The message text
itself is further normalized by masking any token that contains a digit
(numbers, ms counts, exit codes, MAC/IP-style ids, ...), so messages that
only differ by an embedded value are grouped together.

The output CSV embeds device_id/start_date/end_date on every row, so it's
self-contained for downstream tools (e.g. atlas/log_bug_report_agent.py)
that need to go back to the original logs. It also includes a "last_seen"
column: the most recent timestamp among all raw lines folded into that row
(native-format epoch_ms timestamps are converted via UTC, not the executing
machine's local timezone, so results don't depend on where the script runs).

Patterns already explained by a previous atlas/log_bug_report_agent.py run
are skipped: --reference points at a JSONL file of {normalized_message,
source_file, reason} records (default OUTPUT/known_error_reasons.jsonl);
any pattern whose normalized_message already appears there is left out of
the output CSV.

Usage:
  python scripts/extract_error_logs.py --device 105322600007 \
      --start 2026-08-17 --end 2026-08-19 \
      [--base-dir OUTPUT/log] [--exclude gps] [--output errors.csv]
"""

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

PY_LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s*-\s*"
    r"(?P<component>[^-]+?)\s*-\s*(?P<level>[A-Z]+)\s*-\s*(?P<message>.*)$"
)

NATIVE_LOG_RE = re.compile(
    r"^(?P<epoch_ms>\d+):\s*\d+:\s*(?P<tag>\w+):\s*(?P<level>[A-Z]):\s*"
    r"\d+:\s*\d+:\s*(?P<message>.*)$"
)

# Known-noisy error lines that don't represent real problems. Add more
# regexes here as new noise is identified; matched against the raw line.
IGNORE_PATTERNS = [
    re.compile(r"ND_MAP: E: .*cur_raw_ns<raw_time_ns"),
]

# Any whitespace-delimited token containing a digit is treated as variable
# data (a number, ms count, exit code, MAC/IP-style id, etc.) and masked
# wholesale. This is a structural rule rather than a list of known value
# formats, so it also catches formats not seen before, as long as the
# varying part contains a digit. Trade-off: a fixed name that happens to
# contain a digit (e.g. "wlan0") gets masked too.
VARIABLE_TOKEN_RE = re.compile(r"\S*\d\S*")


def is_ignored(line):
    return any(pattern.search(line) for pattern in IGNORE_PATTERNS)


def normalize_message(message):
    return VARIABLE_TOKEN_RE.sub("<N>", message)


def parse_line(line, source_file):
    """Return a dict for the line if it's an error-level entry, else None."""
    line = line.rstrip("\n")
    if not line or is_ignored(line):
        return None

    m = PY_LOG_RE.match(line)
    if m:
        if m.group("level") != "ERROR":
            return None
        dt = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S,%f")
        return {
            "source_file": source_file,
            "format": "python",
            "message": m.group("message").strip(),
            "timestamp": _format_timestamp(dt),
            "line": line,
        }

    m = NATIVE_LOG_RE.match(line)
    if m:
        if m.group("level") != "E":
            return None
        # epoch_ms is a Unix timestamp (always UTC by definition), so convert
        # via UTC rather than the executing machine's local timezone - using
        # local time here would shift results depending on where the script
        # happens to run.
        dt = datetime.fromtimestamp(int(m.group("epoch_ms")) / 1000.0, tz=timezone.utc)
        return {
            "source_file": source_file,
            "format": "native",
            "message": f"{m.group('tag')}: {m.group('message').strip()}",
            "timestamp": _format_timestamp(dt),
            "line": line,
        }

    return None


def _format_timestamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def daterange(start, end):
    days = (end - start).days
    for i in range(days + 1):
        yield start + timedelta(days=i)


def collect_errors(base_dir, device, start_date, end_date, exclude=None):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    exclude = [token.lower() for token in (exclude or [])]

    entries = []
    for day in daterange(start, end):
        logs_dir = os.path.join(base_dir, device, day.isoformat(), "logs")
        if not os.path.isdir(logs_dir):
            continue
        for fname in sorted(os.listdir(logs_dir)):
            if not fname.endswith(".log"):
                continue
            if any(token in fname.lower() for token in exclude):
                continue
            fpath = os.path.join(logs_dir, fname)
            with open(fpath, "r", errors="replace") as f:
                for line in f:
                    parsed = parse_line(line, fname)
                    if parsed:
                        entries.append(parsed)
    return entries


def build_report(entries):
    for e in entries:
        e["normalized_message"] = normalize_message(e["message"])

    counts = Counter(e["normalized_message"] for e in entries)
    files_by_key = defaultdict(set)
    variants_by_key = defaultdict(set)
    example_by_key = {}
    last_seen_by_key = {}
    for e in entries:
        key = e["normalized_message"]
        files_by_key[key].add(e["source_file"])
        variants_by_key[key].add(e["message"])
        example_by_key.setdefault(key, e["message"])
        # Timestamps are formatted zero-padded (YYYY-MM-DD HH:MM:SS.mmm), so
        # string comparison sorts chronologically.
        if e["timestamp"] > last_seen_by_key.get(key, ""):
            last_seen_by_key[key] = e["timestamp"]

    return {
        "total_error_lines": len(entries),
        "unique_error_messages": len(counts),
        "message_counts": [
            {
                "normalized_message": key,
                "count": count,
                "variant_count": len(variants_by_key[key]),
                "source_files": sorted(files_by_key[key]),
                "example_message": example_by_key[key],
                "last_seen": last_seen_by_key[key],
            }
            for key, count in counts.most_common()
        ],
    }


DEFAULT_REFERENCE_PATH = os.path.join("OUTPUT", "known_error_reasons.jsonl")


def load_known_messages(reference_path):
    """Return the set of normalized_message values already explained in a
    previous atlas/log_bug_report_agent.py run, or an empty set if the
    reference file doesn't exist yet."""
    if not os.path.isfile(reference_path):
        return set()
    known = set()
    with open(reference_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                known.add(json.loads(line)["normalized_message"])
    return known


def write_csv(report, output_path, device, start_date, end_date):
    with open(output_path, "w", newline="") as f:
        # Quote every field: message text can contain a literal ";" (a
        # delimiter some spreadsheet apps, e.g. LibreOffice, use by default
        # instead of/alongside ","), and QUOTE_MINIMAL only quotes fields
        # containing the delimiter this writer itself uses (",").
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow([
            "device_id", "start_date", "end_date",
            "count", "variant_count", "source_files", "normalized_message", "example_message", "last_seen",
        ])
        for item in report["message_counts"]:
            writer.writerow([
                device,
                start_date,
                end_date,
                item["count"],
                item["variant_count"],
                "|".join(item["source_files"]),
                item["normalized_message"],
                item["example_message"],
                item["last_seen"],
            ])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", required=True, help="Device ID, e.g. 105322600007")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--base-dir", default=os.path.join("OUTPUT", "log"), help="Base log directory (default: OUTPUT/log)")
    parser.add_argument("--output", help="Write full CSV report to this file")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Substring of a log file name to skip (e.g. 'gps' skips gps.log). Repeatable.",
    )
    parser.add_argument(
        "--reference",
        default=DEFAULT_REFERENCE_PATH,
        help=f"JSONL file of already-explained patterns (from atlas/log_bug_report_agent.py) to skip. Default: {DEFAULT_REFERENCE_PATH}",
    )
    args = parser.parse_args()

    entries = collect_errors(args.base_dir, args.device, args.start, args.end, exclude=args.exclude)
    report = build_report(entries)

    known_messages = load_known_messages(args.reference)
    kept = [item for item in report["message_counts"] if item["normalized_message"] not in known_messages]
    skipped = [item for item in report["message_counts"] if item["normalized_message"] in known_messages]

    print(f"Device: {args.device}  Range: {args.start} to {args.end}")
    print(f"Total error lines: {report['total_error_lines']}")
    print(f"Unique error messages: {report['unique_error_messages']}")
    if skipped:
        print(f"Skipped as already explained: {len(skipped)} patterns ({sum(item['count'] for item in skipped)} lines)")
    print()
    print("Top repeated error messages:")
    for item in kept[:20]:
        files = ",".join(item["source_files"])
        print(f"  [{item['count']:>5}] (variants={item['variant_count']}, {files}) {item['normalized_message']}")

    if args.output:
        report_to_write = {**report, "message_counts": kept}
        write_csv(report_to_write, args.output, args.device, args.start, args.end)
        print(f"\nFull report written to {args.output}")


if __name__ == "__main__":
    main()
