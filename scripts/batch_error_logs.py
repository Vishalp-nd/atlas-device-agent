"""
Group rows from one or more scripts/extract_error_logs.py CSVs into
time-window batches, as a standalone, inspectable step before handing them
to atlas/log_bug_report_agent.py.

When a device has an incident (reboot, crash, network drop), several
different error patterns tend to get logged within a few seconds of each
other - symptoms of one shared root cause. This groups rows whose
last_seen timestamps are close together so they can be investigated
together instead of one at a time.

Grouping uses gap-based sweep clustering, scoped to the same device_id:
rows are sorted by last_seen within each device, and a new batch starts
whenever the gap since the previous row's last_seen exceeds the window.
This avoids the edge case of fixed time bins splitting two closely-timed
rows across a boundary.

The output CSV keeps every input row and column unchanged, adding
batch_id, batch_size, batch_start, and batch_end so the grouping can be
reviewed before running the agent on it.

Usage:
  python scripts/batch_error_logs.py --csv errors.csv \
      [--window-seconds 5] --output errors_batched.csv
"""

import argparse
import csv
from datetime import datetime

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _read_rows(csv_paths):
    rows = []
    for csv_path in csv_paths:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


def group_rows_by_time_window(rows, window_seconds=5):
    by_device = {}
    for row in rows:
        by_device.setdefault(row["device_id"], []).append(row)

    batches = []
    for device_rows in by_device.values():
        device_rows.sort(key=lambda r: datetime.strptime(r["last_seen"], TIMESTAMP_FORMAT))
        current_batch = []
        previous_ts = None
        for row in device_rows:
            ts = datetime.strptime(row["last_seen"], TIMESTAMP_FORMAT)
            if current_batch and (ts - previous_ts).total_seconds() > window_seconds:
                batches.append(current_batch)
                current_batch = []
            current_batch.append(row)
            previous_ts = ts
        if current_batch:
            batches.append(current_batch)

    batches.sort(key=lambda batch: datetime.strptime(batch[0]["last_seen"], TIMESTAMP_FORMAT))
    return batches


def write_batched_csv(batches, output_path):
    fieldnames = list(batches[0][0].keys()) + ["batch_id", "batch_size", "batch_start", "batch_end"]
    with open(output_path, "w", newline="") as f:
        # QUOTE_ALL: message text can contain a literal ";" (a delimiter
        # some spreadsheet apps use by default instead of/alongside ","),
        # which QUOTE_MINIMAL wouldn't quote since "," is this writer's
        # actual delimiter.
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for batch_id, batch in enumerate(batches, start=1):
            timestamps = [datetime.strptime(r["last_seen"], TIMESTAMP_FORMAT) for r in batch]
            batch_start = min(timestamps).strftime(TIMESTAMP_FORMAT)[:-3]
            batch_end = max(timestamps).strftime(TIMESTAMP_FORMAT)[:-3]
            for row in batch:
                writer.writerow({
                    **row,
                    "batch_id": batch_id,
                    "batch_size": len(batch),
                    "batch_start": batch_start,
                    "batch_end": batch_end,
                })


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", nargs="+", required=True, help="One or more CSV files produced by scripts/extract_error_logs.py.")
    parser.add_argument("--window-seconds", type=float, default=5, help="Max gap (seconds) between consecutive last_seen timestamps to stay in the same batch. Default: 5.")
    parser.add_argument("--output", required=True, help="Write the batched CSV to this path.")
    return parser.parse_args()


def main():
    args = _parse_args()
    rows = _read_rows(args.csv)
    if not rows:
        print("No rows found in input CSV(s).")
        return

    batches = group_rows_by_time_window(rows, args.window_seconds)

    print(f"Rows in: {len(rows)}")
    print(f"Batches out: {len(batches)}")
    sizes = sorted((len(b) for b in batches), reverse=True)
    print(f"Largest batch sizes: {sizes[:10]}")
    print(f"Batches with more than one row: {sum(1 for s in sizes if s > 1)}")

    write_batched_csv(batches, args.output)
    print(f"\nBatched CSV written to {args.output}")


if __name__ == "__main__":
    main()
