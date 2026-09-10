#!/usr/bin/env python3
"""One-time migration: repartition the retention-managed ClickHouse tables by day.

criticalinfo_snowflakes_data, observation_data and video_metadata were originally
partitioned monthly (toYYYYMM). Rolling retention (scripts/purge_old_data.py) drops
whole partitions, and with monthly partitions a day can only age out in monthly lumps
-- a month's data survives up to an extra month, then vanishes all at once. Daily
partitions (toYYYYMMDD) make each partition wholly inside or wholly outside the
window, so retention becomes one metadata-only DROP PARTITION per night.

PARTITION BY cannot be altered in place, so each table is rebuilt and swapped:

    CREATE TABLE <t>_new (...) PARTITION BY toYYYYMMDD(...)
    INSERT INTO <t>_new SELECT * FROM <t>
    RENAME TABLE <t> TO <t>_premigration, <t>_new TO <t>

The pre-migration table is left in place; drop it manually once a nightly run has
completed cleanly against the new one.

Run this ONCE, manually, with the nightly cron disabled -- it must not race
pipeline/nightly_critical_events_poll.sh.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for _path in (REPO_ROOT, SCRIPT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from clickhouse_retention import (  # noqa: E402
    DEFAULT_CLICKHOUSE_SECTION,
    TABLE_TIME_COLUMNS,
    connect,
    quote_identifier,
)

# Child before parent: video_metadata rows are the detail of observation_data rows,
# so if the run is interrupted the leftover state is coherent either way, but this
# order keeps the parent authoritative for longest.
MIGRATION_ORDER = ["video_metadata", "observation_data", "criticalinfo_snowflakes_data"]

NEW_SUFFIX = "_new"
OLD_SUFFIX = "_premigration"


def fetch_create_statement(client, database: str, table: str) -> str:
    rows = client.query(
        "SELECT create_table_query FROM system.tables "
        "WHERE database = {db:String} AND name = {tbl:String}",
        parameters={"db": database, "tbl": table},
    ).result_rows
    if not rows:
        raise RuntimeError(f"Table {database}.{table} does not exist")
    return str(rows[0][0])


def rewrite_create_statement(create_sql: str, database: str, table: str, new_table: str) -> str:
    """Retarget a SHOW CREATE output at <new_table> and switch it to a daily key.

    Only the partition granularity and the table name change; columns, ENGINE,
    ORDER BY and SETTINGS are carried over verbatim from the live table so the
    rebuilt table cannot silently drift from the original.

    system.tables reports the statement database-qualified and unquoted, e.g.
    "CREATE TABLE atlas.video_metadata (...)", but accept the quoted forms too.
    """
    target = f"CREATE TABLE {quote_identifier(database)}.{quote_identifier(new_table)}"
    candidates = [
        f"CREATE TABLE {database}.{table}",
        f"CREATE TABLE {quote_identifier(database)}.{quote_identifier(table)}",
        f"CREATE TABLE {quote_identifier(table)}",
        f"CREATE TABLE {table}",
    ]
    for candidate in candidates:
        if create_sql.startswith(candidate):
            rewritten = create_sql.replace(candidate, target, 1)
            break
    else:
        raise RuntimeError(
            f"Could not locate the CREATE TABLE clause for {table}. Live DDL: {create_sql[:200]}"
        )

    if "PARTITION BY toYYYYMMDD(" in rewritten:
        raise RuntimeError(f"{table} is already partitioned by day; nothing to migrate")
    if "PARTITION BY toYYYYMM(" not in rewritten:
        raise RuntimeError(
            f"{table} is not partitioned by toYYYYMM(...); refusing to guess a new key. "
            f"Live DDL: {rewritten}"
        )
    return rewritten.replace("PARTITION BY toYYYYMM(", "PARTITION BY toYYYYMMDD(", 1)


def table_stats(client, database: str, table: str, time_column: str) -> dict[str, object]:
    col = quote_identifier(time_column)
    rows = client.query(
        f"SELECT count(), uniqExact(toYYYYMMDD({col})), min({col}), max({col}) "
        f"FROM {quote_identifier(database)}.{quote_identifier(table)}"
    ).result_rows[0]
    return {"rows": rows[0], "days": rows[1], "min_time": rows[2], "max_time": rows[3]}


def table_exists(client, database: str, table: str) -> bool:
    rows = client.query(
        "SELECT count() FROM system.tables WHERE database = {db:String} AND name = {tbl:String}",
        parameters={"db": database, "tbl": table},
    ).result_rows
    return bool(rows and rows[0][0])


def migrate_table(client, database: str, table: str, dry_run: bool) -> None:
    time_column = TABLE_TIME_COLUMNS[table]
    new_table = f"{table}{NEW_SUFFIX}"
    old_table = f"{table}{OLD_SUFFIX}"

    create_sql = rewrite_create_statement(
        fetch_create_statement(client, database, table), database, table, new_table
    )
    before = table_stats(client, database, table, time_column)

    print(f"\n=== {table} ===")
    print(f"  current: {before['rows']:,} rows across {before['days']} day(s), "
          f"{before['min_time']} .. {before['max_time']}")
    print(f"  new DDL: {create_sql}")

    rename_sql = (
        f"RENAME TABLE {quote_identifier(database)}.{quote_identifier(table)} "
        f"TO {quote_identifier(database)}.{quote_identifier(old_table)}, "
        f"{quote_identifier(database)}.{quote_identifier(new_table)} "
        f"TO {quote_identifier(database)}.{quote_identifier(table)}"
    )

    if dry_run:
        print(f"  [dry-run] would create {new_table}, copy {before['rows']:,} rows, then:")
        print(f"  [dry-run]   {rename_sql}")
        return

    for existing in (new_table, old_table):
        if table_exists(client, database, existing):
            raise RuntimeError(
                f"{database}.{existing} already exists -- a previous migration attempt left "
                f"state behind. Inspect and drop it before re-running."
            )

    print(f"  creating {new_table}")
    client.command(create_sql)

    print(f"  copying rows into {new_table} (this is the slow step)")
    client.command(
        f"INSERT INTO {quote_identifier(database)}.{quote_identifier(new_table)} "
        f"SELECT * FROM {quote_identifier(database)}.{quote_identifier(table)}"
    )

    after = table_stats(client, database, new_table, time_column)
    print(f"  copied:  {after['rows']:,} rows across {after['days']} day(s), "
          f"{after['min_time']} .. {after['max_time']}")

    if after["days"] != before["days"] or after["min_time"] != before["min_time"] \
            or after["max_time"] != before["max_time"]:
        raise RuntimeError(
            f"Coverage mismatch for {table}: before={before}, after={after}. "
            f"{new_table} has been left in place for inspection; nothing was renamed."
        )
    if after["rows"] != before["rows"]:
        # ReplacingMergeTree can legitimately collapse duplicates on a fresh insert,
        # so a row delta is informational as long as the day coverage matches.
        print(f"  NOTE: row count changed by {after['rows'] - before['rows']:,} "
              f"(expected for ReplacingMergeTree deduplication)")

    print(f"  swapping {table} <- {new_table}")
    client.command(rename_sql)
    print(f"  done. Previous table kept as {old_table}; drop it once a nightly run looks good.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-config", default=str(REPO_ROOT / "db_credentials.ini"),
                        help="Path to db_credentials.ini")
    parser.add_argument("--clickhouse-section", default=DEFAULT_CLICKHOUSE_SECTION,
                        help=f"Section in db_credentials.ini for ClickHouse (default: {DEFAULT_CLICKHOUSE_SECTION})")
    parser.add_argument("--table", action="append", choices=MIGRATION_ORDER, dest="tables",
                        help="Migrate only this table (repeatable). Default: all three, child first.")
    parser.add_argument("--timeout", type=int, default=7200,
                        help="ClickHouse HTTP read timeout in seconds for the row copy (default: 7200)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the DDL and row counts without creating, copying or renaming anything")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    tables = [t for t in MIGRATION_ORDER if not args.tables or t in args.tables]

    client, database = connect(Path(args.db_config), args.clickhouse_section, timeout=args.timeout)
    print(f"ClickHouse database: {database}")
    if args.dry_run:
        print("DRY RUN -- no changes will be made")

    for table in tables:
        migrate_table(client, database, table, args.dry_run)

    if not args.dry_run:
        print("\nMigration complete. Next: run one nightly poll, confirm rows land in the "
              "expected daily partitions, then drop the *_premigration tables.")


if __name__ == "__main__":
    main()
