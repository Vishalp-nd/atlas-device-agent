#!/usr/bin/env python3
"""Back up a ClickHouse critical info table and re-update type using the CSV map.

Rows are normalized in ClickHouse the same way the CSV patterns were generated.
Unmatched rows are ignored and left unchanged.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from critical_events_pipeline import _read_clickhouse_config, _run_clickhouse_query
from regex_map import CSV_PATH


DEFAULT_CLICKHOUSE_SECTION = "CLICKHOUSE_DB"
DEFAULT_SOURCE_TABLE = "criticalinfo_snowflakes_data"
DEFAULT_BATCH_SIZE = 100
NORMALIZED_DESCRIPTION_EXPR = (
    "trim(replaceRegexpAll(ifNull(\"DESCRIPTION\", ''), '\\S*\\d\\S*', '<N>'))"
)


def _load_normalized_type_map() -> list[tuple[str, str]]:
    normalized_items: list[tuple[str, str]] = []
    seen_patterns: set[str] = set()
    with CSV_PATH.open(newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            pattern = (row.get("description_pattern") or "").strip()
            event_type = (row.get("TYPE") or "").strip().upper()
            if not pattern or not event_type or pattern in seen_patterns:
                continue
            seen_patterns.add(pattern)
            normalized_items.append((pattern, event_type))
    return normalized_items


NORMALIZED_TYPE_MAP = _load_normalized_type_map()


def _escape_clickhouse_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_backup_table_name(source_table: str) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{source_table}_backup_{timestamp}"


def _build_backup_queries(source_table: str, backup_table: str) -> list[str]:
    return [
        f"CREATE TABLE {backup_table} AS {source_table}",
        f"INSERT INTO {backup_table} SELECT * FROM {source_table}",
    ]


def _build_update_query(source_table: str, regex_items: list[tuple[str, str]]) -> str:
    conditions: list[str] = []
    where_conditions: list[str] = []
    for normalized_pattern, event_type in regex_items:
        escaped_pattern = _escape_clickhouse_literal(normalized_pattern)
        escaped_type = _escape_clickhouse_literal(event_type)
        equals_expr = f"{NORMALIZED_DESCRIPTION_EXPR} = '{escaped_pattern}'"
        conditions.append(f"{equals_expr}, '{escaped_type}'")
        where_conditions.append(equals_expr)

    if not conditions:
        raise ValueError("No regex mappings available to build ClickHouse update query")

    multi_if_expr = ",\n                ".join(conditions + ["type"])
    where_expr = " OR\n            ".join(where_conditions)
    return f'''
        ALTER TABLE {source_table}
        UPDATE type = multiIf(
                {multi_if_expr}
            )
        WHERE {where_expr}
    '''


def _iter_regex_batches(batch_size: int) -> list[list[tuple[str, str]]]:
    items = NORMALIZED_TYPE_MAP
    return [items[index:index + batch_size] for index in range(0, len(items), batch_size)]


def run(args: argparse.Namespace) -> None:
    params = _read_clickhouse_config(args.db_config, args.clickhouse_section)

    backup_table = None
    if not args.skip_backup:
        backup_table = args.backup_table or _build_backup_table_name(args.source_table)
        print(f"Creating backup table: {backup_table}")
        for query in _build_backup_queries(args.source_table, backup_table):
            _run_clickhouse_query(params, query)
    else:
        print("Skipping backup table creation")

    regex_batches = _iter_regex_batches(args.batch_size)
    print(
        f"Applying {len(regex_batches)} update batches from {len(NORMALIZED_TYPE_MAP)} normalized mappings; "
        "unmatched rows will be ignored"
    )
    for batch_number, regex_batch in enumerate(regex_batches, start=1):
        query = _build_update_query(args.source_table, regex_batch)
        _run_clickhouse_query(params, query)
        print(f"Applied batch {batch_number}/{len(regex_batches)} with {len(regex_batch)} regex mappings")

    print("Done.")
    if backup_table is not None:
        print(f"Backup table: {backup_table}")
    print(f"Updated table: {args.source_table}")


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-config",
        default=str(repo_root / "db_credentials.ini"),
        help="Path to db_credentials.ini",
    )
    parser.add_argument(
        "--clickhouse-section",
        default=DEFAULT_CLICKHOUSE_SECTION,
        help="Section in db_credentials.ini for ClickHouse",
    )
    parser.add_argument(
        "--source-table",
        default=DEFAULT_SOURCE_TABLE,
        help="ClickHouse table whose type column should be re-updated",
    )
    parser.add_argument(
        "--backup-table",
        default=None,
        help="Optional explicit backup table name; defaults to a timestamped copy",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip creating a backup table before applying updates",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Number of regex mappings per ClickHouse ALTER UPDATE batch",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()