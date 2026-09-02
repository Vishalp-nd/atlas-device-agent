#!/usr/bin/env python3
"""Back up a ClickHouse critical info table and re-update type and priority using the CSV map.

Rows are normalized in ClickHouse the same way the CSV patterns were generated.
Unmatched rows are ignored and left unchanged.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from critical_events_pipeline import _read_clickhouse_config, _run_clickhouse_query


DEFAULT_CLICKHOUSE_SECTION = "CLICKHOUSE_DB"
DEFAULT_SOURCE_TABLE = "criticalinfo_snowflakes_data"
DEFAULT_BATCH_SIZE = 100
DEFAULT_CSV_PATH = Path(__file__).resolve().parent / "unique_cinfo_op_mapped.csv"
NORMALIZED_DESCRIPTION_EXPR = (
    "trim(replaceRegexpAll(ifNull(\"DESCRIPTION\", ''), '\\S*\\d\\S*', '<N>'))"
)


def _load_normalized_type_priority_map(csv_path: Path) -> list[tuple[str, str, str]]:
    normalized_items: list[tuple[str, str, str]] = []
    seen_patterns: set[str] = set()
    with csv_path.open(newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            pattern = (row.get("description_pattern") or "").strip()
            event_type = (row.get("TYPE") or "").strip().upper()
            priority = (row.get("predicted_priority") or row.get("priority") or "").strip().upper()
            if not pattern or not event_type or not priority or pattern in seen_patterns:
                continue
            seen_patterns.add(pattern)
            normalized_items.append((pattern, event_type, priority))
    return normalized_items


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


def _build_update_query(source_table: str, regex_items: list[tuple[str, str, str]]) -> str:
    type_conditions: list[str] = []
    priority_conditions: list[str] = []
    where_conditions: list[str] = []
    for normalized_pattern, event_type, priority in regex_items:
        escaped_pattern = _escape_clickhouse_literal(normalized_pattern)
        escaped_type = _escape_clickhouse_literal(event_type)
        escaped_priority = _escape_clickhouse_literal(priority)
        equals_expr = f"{NORMALIZED_DESCRIPTION_EXPR} = '{escaped_pattern}'"
        type_conditions.append(f"{equals_expr}, '{escaped_type}'")
        priority_conditions.append(f"{equals_expr}, '{escaped_priority}'")
        where_conditions.append(equals_expr)

    if not type_conditions:
        raise ValueError("No regex mappings available to build ClickHouse update query")

    type_multi_if_expr = ",\n                ".join(type_conditions + ["type"])
    priority_multi_if_expr = ",\n                ".join(priority_conditions + ["priority"])
    where_expr = " OR\n            ".join(where_conditions)
    return f'''
        ALTER TABLE {source_table}
        UPDATE
            type = multiIf(
                {type_multi_if_expr}
            ),
            priority = multiIf(
                {priority_multi_if_expr}
            )
        WHERE {where_expr}
    '''


def _iter_regex_batches(items: list[tuple[str, str, str]], batch_size: int) -> list[list[tuple[str, str, str]]]:
    return [items[index:index + batch_size] for index in range(0, len(items), batch_size)]


def _build_verify_query(source_table: str, regex_items: list[tuple[str, str, str]]) -> str:
    mapping_rows: list[str] = []
    for normalized_pattern, event_type, priority in regex_items:
        escaped_pattern = _escape_clickhouse_literal(normalized_pattern)
        escaped_type = _escape_clickhouse_literal(event_type)
        escaped_priority = _escape_clickhouse_literal(priority)
        mapping_rows.append(f"('{escaped_pattern}', '{escaped_type}', '{escaped_priority}')")

    if not mapping_rows:
        raise ValueError("No regex mappings available to build ClickHouse verify query")

    mapping_expr = ",\n                ".join(mapping_rows)
    return f'''
        SELECT
            actual_rows.normalized_description,
            actual_rows.current_type,
            actual_rows.current_priority,
            expected_map.expected_type,
            expected_map.expected_priority,
            actual_rows.row_count
        FROM (
            SELECT
                {NORMALIZED_DESCRIPTION_EXPR} AS normalized_description,
                upperUTF8(ifNull(type, '')) AS current_type,
                upperUTF8(ifNull(priority, '')) AS current_priority,
                count() AS row_count
            FROM {source_table}
            GROUP BY normalized_description, current_type, current_priority
        ) AS actual_rows
        INNER JOIN (
            SELECT
                tupleElement(mapping, 1) AS normalized_description,
                tupleElement(mapping, 2) AS expected_type,
                tupleElement(mapping, 3) AS expected_priority
            FROM (
                SELECT arrayJoin([
                {mapping_expr}
                ]) AS mapping
            )
        ) AS expected_map USING (normalized_description)
        WHERE actual_rows.current_type != upperUTF8(ifNull(expected_map.expected_type, ''))
           OR actual_rows.current_priority != upperUTF8(ifNull(expected_map.expected_priority, ''))
        ORDER BY row_count DESC, normalized_description
    '''


def run(args: argparse.Namespace) -> None:
    params = _read_clickhouse_config(args.db_config, args.clickhouse_section)
    normalized_type_priority_map = _load_normalized_type_priority_map(Path(args.csv_path))

    if args.verify_only:
        query = _build_verify_query(args.source_table, normalized_type_priority_map)
        output = _run_clickhouse_query(params, query).strip()
        print(output)
        return

    backup_table = None
    if not args.skip_backup:
        backup_table = args.backup_table or _build_backup_table_name(args.source_table)
        print(f"Creating backup table: {backup_table}")
        for query in _build_backup_queries(args.source_table, backup_table):
            _run_clickhouse_query(params, query)
    else:
        print("Skipping backup table creation")

    regex_batches = _iter_regex_batches(normalized_type_priority_map, args.batch_size)
    print(
        f"Applying {len(regex_batches)} update batches from {len(normalized_type_priority_map)} normalized mappings; "
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
        "--csv-path",
        default=str(DEFAULT_CSV_PATH),
        help="CSV file containing description_pattern, TYPE, and predicted_priority mappings",
    )
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
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only report rows whose current type still differs from the CSV-mapped type",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()