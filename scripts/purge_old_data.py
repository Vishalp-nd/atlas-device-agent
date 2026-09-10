#!/usr/bin/env python3
"""Rolling-window retention: delete polled data older than RETENTION_MONTHS.

Two targets:

1. ClickHouse -- criticalinfo_snowflakes_data, observation_data and video_metadata.
   These are partitioned by day (toYYYYMMDD), so a partition is wholly inside or
   wholly outside the window and ageing out a day is a metadata-only DROP PARTITION.
   No mutations, no row rewrites. If the tables are still partitioned monthly, run
   scripts/repartition_clickhouse_daily.py first -- this script refuses to guess.

2. On disk -- the dated polling trigger folders written by pipeline/data_polling.py
   and the timestamped nightly logs written by nightly_critical_events_poll.sh. Both
   carry their date in the path, which is what gets parsed; mtime is never consulted,
   since a re-touched old directory would otherwise look fresh.

Configured by RETENTION_MONTHS (default 2) in the repo-root .env, overridable per-run
with --months. Run with --dry-run first: it exercises every guard and prints the exact
DDL it would issue, without changing anything.

Invoked nightly from pipeline/nightly_critical_events_poll.sh, where a failure here is
logged but does not fail the poll.
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import os
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for _path in (REPO_ROOT, SCRIPT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from clickhouse_retention import (  # noqa: E402
    ClickHouseError,
    DEFAULT_CLICKHOUSE_SECTION,
    DEFAULT_MIN_PARTITION,
    PURGE_ORDER,
    SENTINEL_PARTITIONS,
    TABLE_SIZE_EXCEEDS_MAX_DROP_SIZE_LIMIT,
    TABLE_TIME_COLUMNS,
    connect,
    format_size,
    quote_identifier,
)

DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_RETENTION_MONTHS = 2

# Daily partition ids are exactly 8 digits. The check also rejects 'all', which is what
# a non-partitioned table reports in system.parts -- dropping that would truncate it.
PARTITION_ID_RE = re.compile(r"\d{8}")

# OUTPUT/<family>/<ota>/polling/<YYYY-MM-DD>/ -- pipeline/data_polling.py
DATE_DIR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# pipeline/logs/nightly_obs_poll_<YYYYmmdd>_<HHMMSS>.log
NIGHTLY_LOG_RE = re.compile(r"nightly_obs_poll_(\d{8})_\d{6}\.log")


class PurgeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- cutoff


def months_ago(today: datetime.date, months: int) -> datetime.date:
    """Subtract calendar months, clamping the day to the target month's length."""
    total = today.year * 12 + (today.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


# ---------------------------------------------------------------------- clickhouse


def fetch_partitions(client, database: str, tables: list[str]) -> dict[str, list[dict]]:
    """One metadata query for every partition of every target table.

    `active` is mandatory: without it, outdated parts still awaiting cleanup are counted
    too, double-reporting rows and bytes. Reading rows/bytes from system.parts also avoids
    a per-partition SELECT count(), which on video_metadata would scan ~176M rows for a
    number the metadata already has.
    """
    result = client.query(
        "SELECT table, partition_id, count() AS parts, sum(rows) AS rows, "
        "       sum(bytes_on_disk) AS bytes, min(min_time) AS min_time, max(max_time) AS max_time "
        "FROM system.parts "
        "WHERE database = {db:String} AND table IN {tables:Array(String)} AND active "
        "GROUP BY table, partition_id "
        "ORDER BY table, partition_id",
        parameters={"db": database, "tables": tables},
    )
    by_table: dict[str, list[dict]] = {table: [] for table in tables}
    for table, partition_id, parts, rows, num_bytes, min_time, max_time in result.result_rows:
        by_table[table].append({
            "partition_id": str(partition_id),
            "parts": parts,
            "rows": rows,
            "bytes": num_bytes,
            "min_time": min_time,
            "max_time": max_time,
        })
    return by_table


def observed_max_time(client, database: str, table: str, partition_id: str):
    """Read the real newest timestamp inside one partition.

    system.parts only populates min_time/max_time when the partition key is a plain
    Date/DateTime column. observation_data and video_metadata wrap theirs in
    ifNull(start_time, ...), so they always report 1970 and the metadata cannot be used
    as the data-derived guard. Fall back to a query scoped to the single partition via
    the _partition_id virtual column -- one column read over one day's data, and only
    for a partition already selected for dropping.
    """
    column = quote_identifier(TABLE_TIME_COLUMNS[table])
    rows = client.query(
        f"SELECT max({column}) FROM {quote_identifier(database)}.{quote_identifier(table)} "
        f"WHERE _partition_id = {{pid:String}}",
        parameters={"pid": partition_id},
    ).result_rows
    return rows[0][0] if rows else None


def classify_partition(
    partition: dict, cutoff: datetime.date, cutoff_id: str, min_partition: str, confirm_max_time
) -> tuple[bool, str]:
    """Decide whether one partition may be dropped. Returns (droppable, reason).

    `confirm_max_time` is a zero-arg callable, invoked only once every cheap check has
    already passed, so the extra query costs nothing for partitions being retained.
    """
    partition_id = partition["partition_id"]

    if partition_id in SENTINEL_PARTITIONS:
        return False, "UNDATED sentinel partition - RETAINED, needs triage"

    if not PARTITION_ID_RE.fullmatch(partition_id):
        return False, (
            f"partition id {partition_id!r} is not a daily (YYYYMMDD) partition - "
            f"run scripts/repartition_clickhouse_daily.py first"
        )

    if partition_id >= cutoff_id:
        return False, "within retention window"

    if partition_id < min_partition:
        return False, f"below --min-partition floor {min_partition}"

    # Independent, data-derived confirmation: check the rows themselves rather than
    # trusting arithmetic on the partition name.
    max_time = partition["max_time"]
    if max_time is None or getattr(max_time, "year", 0) <= 1970:
        max_time = confirm_max_time()
    if max_time is None:
        return False, "partition holds no readable timestamp - refusing to drop on name alone"
    if max_time.date() >= cutoff:
        return False, f"newest row {max_time} is not older than cutoff {cutoff}"

    return True, f"newest row {max_time} is older than cutoff"


def purge_clickhouse(args, cutoff: datetime.date) -> int:
    client, database = connect(Path(args.db_config), args.clickhouse_section)
    tables = [t for t in PURGE_ORDER if not args.tables or t in args.tables]
    cutoff_id = cutoff.strftime("%Y%m%d")

    print(f"ClickHouse database : {database}")
    print(f"Cutoff              : {cutoff} (dropping daily partitions < {cutoff_id})")

    by_table = fetch_partitions(client, database, tables)
    total_rows = 0
    total_bytes = 0
    dropped = 0
    failures = 0

    for table in tables:
        partitions = by_table.get(table, [])
        if not partitions:
            print(f"\n{table}: no active partitions")
            continue

        print(f"\n{table} (time column {TABLE_TIME_COLUMNS[table]}):")
        for partition in partitions:
            partition_id = partition["partition_id"]
            droppable, reason = classify_partition(
                partition, cutoff, cutoff_id, args.min_partition,
                lambda: observed_max_time(client, database, table, partition_id),
            )
            summary = (
                f"  {partition_id}  parts={partition['parts']:<3} "
                f"rows={partition['rows']:>14,}  {format_size(partition['bytes']):>12}  "
                f"[{partition['min_time']} .. {partition['max_time']}]"
            )

            if not droppable:
                if partition_id in SENTINEL_PARTITIONS:
                    print(f"{summary}  WARNING: {reason}")
                elif not PARTITION_ID_RE.fullmatch(partition_id):
                    print(f"{summary}  ERROR: {reason}")
                    failures += 1
                else:
                    print(f"{summary}  KEEP ({reason})")
                continue

            ddl = (
                f"ALTER TABLE {quote_identifier(database)}.{quote_identifier(table)} "
                f"DROP PARTITION ID '{partition_id}'"
            )
            if args.dry_run:
                print(f"{summary}  DROP ({reason})")
                print(f"      [dry-run] {ddl}")
            else:
                print(f"{summary}  DROP ({reason})")
                try:
                    client.command(ddl)
                except Exception as exc:  # noqa: BLE001 - reported per partition, run continues
                    if f"({TABLE_SIZE_EXCEEDS_MAX_DROP_SIZE_LIMIT})" in str(exc):
                        print(
                            f"      FAILED: partition exceeds the server's "
                            f"max_partition_size_to_drop. Raise that server setting; do not "
                            f"write /var/lib/clickhouse/flags/force_drop_table."
                        )
                    else:
                        print(f"      FAILED: {exc}")
                    failures += 1
                    continue

            dropped += 1
            total_rows += partition["rows"]
            total_bytes += partition["bytes"]

    verb = "would drop" if args.dry_run else "dropped"
    print(f"\nClickHouse: {verb} {dropped} partition(s), {total_rows:,} rows, "
          f"{format_size(total_bytes)}")
    if dropped and not args.dry_run:
        print("Note: disk space returns once inactive parts are removed "
              "(old_parts_lifetime, ~8 minutes) - not immediately.")
    return failures


# ---------------------------------------------------------------------- filesystem


def _remove(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def purge_polling_folders(cutoff: datetime.date, dry_run: bool) -> tuple[int, int]:
    """Remove OUTPUT/<family>/<ota>/polling/<YYYY-MM-DD>/ directories older than cutoff.

    Only the polling/<date>/ subtree is dated. The per-OTA artifacts a level above it
    (Feature_level_summary.csv, device_list_config.csv, Feature_device_summary.xlsx) are
    the sources data_polling.py copies from, so this never walks above polling/.
    """
    output_root = REPO_ROOT / "OUTPUT"
    removed = 0
    skipped = 0
    if not output_root.is_dir():
        return removed, skipped

    for polling_dir in sorted(output_root.glob("*/*/polling")):
        if not polling_dir.is_dir():
            continue
        for date_dir in sorted(polling_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            if not DATE_DIR_RE.fullmatch(date_dir.name):
                print(f"  SKIP (unparseable name): {date_dir.relative_to(REPO_ROOT)}")
                skipped += 1
                continue
            try:
                folder_date = datetime.date.fromisoformat(date_dir.name)
            except ValueError:
                print(f"  SKIP (invalid date): {date_dir.relative_to(REPO_ROOT)}")
                skipped += 1
                continue
            if folder_date >= cutoff:
                continue
            print(f"  {'[dry-run] ' if dry_run else ''}remove {date_dir.relative_to(REPO_ROOT)}")
            _remove(date_dir, dry_run)
            removed += 1

        # Tidy up the now-empty parents, but never the OUTPUT tree itself.
        if not dry_run:
            for parent in (polling_dir, polling_dir.parent):
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()

    return removed, skipped


def purge_nightly_logs(cutoff: datetime.date, dry_run: bool) -> tuple[int, int]:
    """Remove pipeline/logs/nightly_obs_poll_<YYYYmmdd>_<HHMMSS>.log older than cutoff."""
    log_dir = REPO_ROOT / "pipeline" / "logs"
    removed = 0
    skipped = 0
    if not log_dir.is_dir():
        return removed, skipped

    for log_file in sorted(log_dir.iterdir()):
        if not log_file.is_file():
            continue
        match = NIGHTLY_LOG_RE.fullmatch(log_file.name)
        if not match:
            print(f"  SKIP (unparseable name): {log_file.relative_to(REPO_ROOT)}")
            skipped += 1
            continue
        try:
            log_date = datetime.datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            print(f"  SKIP (invalid date): {log_file.relative_to(REPO_ROOT)}")
            skipped += 1
            continue
        if log_date >= cutoff:
            continue
        print(f"  {'[dry-run] ' if dry_run else ''}remove {log_file.relative_to(REPO_ROOT)}")
        _remove(log_file, dry_run)
        removed += 1

    return removed, skipped


def purge_files(cutoff: datetime.date, dry_run: bool) -> None:
    print(f"\nFilesystem (removing entries dated before {cutoff}):")
    folders_removed, folders_skipped = purge_polling_folders(cutoff, dry_run)
    logs_removed, logs_skipped = purge_nightly_logs(cutoff, dry_run)

    verb = "would remove" if dry_run else "removed"
    print(f"Filesystem: {verb} {folders_removed} polling folder(s) and {logs_removed} "
          f"nightly log(s); left {folders_skipped + logs_skipped} unparseable entr(ies) alone")


# --------------------------------------------------------------------------- entry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db-config", default=str(REPO_ROOT / "db_credentials.ini"),
                        help="Path to db_credentials.ini")
    parser.add_argument("--clickhouse-section", default=DEFAULT_CLICKHOUSE_SECTION,
                        help=f"Section in db_credentials.ini for ClickHouse "
                             f"(default: {DEFAULT_CLICKHOUSE_SECTION})")
    parser.add_argument("--months", type=int, default=None,
                        help=f"Retention window in calendar months "
                             f"(default: RETENTION_MONTHS in .env, else {DEFAULT_RETENTION_MONTHS})")
    parser.add_argument("--cutoff-date", default=None,
                        help="Override the computed cutoff with an explicit YYYY-MM-DD. "
                             "Intended for testing the rolling behaviour.")
    parser.add_argument("--min-partition", default=DEFAULT_MIN_PARTITION,
                        help=f"Refuse to drop partitions below this id, and abort if the "
                             f"computed cutoff falls below it (default: {DEFAULT_MIN_PARTITION})")
    parser.add_argument("--tables", action="append", choices=PURGE_ORDER,
                        help="Restrict the ClickHouse purge to this table (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run every guard and print what would happen, changing nothing")
    parser.add_argument("--clickhouse-only", action="store_true", help="Skip the filesystem purge")
    parser.add_argument("--files-only", action="store_true", help="Skip the ClickHouse purge")
    return parser


def resolve_months(args) -> int:
    if args.months is not None:
        return args.months
    load_dotenv(str(DEFAULT_ENV_PATH), override=False)
    raw = os.environ.get("RETENTION_MONTHS", "").strip()
    if not raw:
        return DEFAULT_RETENTION_MONTHS
    try:
        return int(raw)
    except ValueError as exc:
        raise PurgeError(f"RETENTION_MONTHS must be an integer, got {raw!r}") from exc


def main() -> int:
    args = build_parser().parse_args()
    if args.clickhouse_only and args.files_only:
        raise PurgeError("--clickhouse-only and --files-only are mutually exclusive")

    months = resolve_months(args)
    if months < 0:
        raise PurgeError(f"Retention window must not be negative, got {months}")

    if args.cutoff_date:
        cutoff = datetime.date.fromisoformat(args.cutoff_date)
    else:
        cutoff = months_ago(datetime.date.today(), months)

    # A cutoff below the floor means a bad --months or a system clock jump, not real
    # data that old. Abort before anything destructive happens.
    if cutoff.strftime("%Y%m%d") < args.min_partition:
        raise PurgeError(
            f"Computed cutoff {cutoff} is below --min-partition {args.min_partition}. "
            f"Refusing to run: check --months/RETENTION_MONTHS and the system clock."
        )

    print(f"Retention window    : {months} month(s)")
    if args.dry_run:
        print("DRY RUN             : nothing will be deleted")

    failures = 0
    if not args.files_only:
        failures += purge_clickhouse(args, cutoff)
    if not args.clickhouse_only:
        purge_files(cutoff, args.dry_run)

    if failures:
        print(f"\nCompleted with {failures} failure(s).")
        return 1
    return 0


if __name__ == "__main__":
    # The nightly runner only sees this script's stdout, so surface failures as a single
    # readable line rather than a traceback. Config and connection problems are the
    # common case and are not bugs worth a stack trace.
    try:
        sys.exit(main())
    except PurgeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, ClickHouseError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
