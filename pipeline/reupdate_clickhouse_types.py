#!/usr/bin/env python3
"""Back up a ClickHouse critical info table and re-update type and priority using the CSV map.

Rows are normalized in ClickHouse the same way the CSV patterns were generated.
Unmatched rows are ignored and left unchanged.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
import codecs

from critical_events_pipeline import _read_clickhouse_config, _run_clickhouse_query


DEFAULT_CLICKHOUSE_SECTION = "CLICKHOUSE_DB"
DEFAULT_SOURCE_TABLE = "criticalinfo_snowflakes_data"
DEFAULT_BATCH_SIZE = 100
DEFAULT_CSV_PATH = Path(__file__).resolve().parent / "unique_cinfo_op_mapped.csv"
DEFAULT_JSON_PATH = Path(__file__).resolve().parent / "unique_cinfo_op_mapped.json"
NORMALIZED_DESCRIPTION_EXPR = (
    "replaceRegexpAll(ifNull(\"DESCRIPTION\", ''), '\\S*\\d\\S*', '<N>')"
)


def _load_normalized_type_priority_map_from_csv(csv_path: Path) -> list[tuple[str, str, str]]:
    normalized_map: dict[str, tuple[str, str]] = {}
    with csv_path.open(newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            pattern = _decode_csv_pattern(row.get("description_pattern") or "")
            event_type = (row.get("TYPE") or "").strip().upper()
            priority = (row.get("priority") or "").strip().upper()
            if row.get("description_pattern") is None or not event_type or not priority:
                continue
            normalized_map[pattern] = (event_type, priority)
    return [
        (pattern, event_type, priority)
        for pattern, (event_type, priority) in normalized_map.items()
    ]


def _load_normalized_type_priority_map_from_json(json_path: Path) -> list[tuple[str, str, str]]:
    normalized_map: dict[str, tuple[str, str]] = {}
    with json_path.open() as json_file:
        rows = json.load(json_file)

    for row in rows:
        pattern = _decode_csv_pattern(row.get("description_pattern") or "")
        event_type = (row.get("TYPE") or "").strip().upper()
        priority = (row.get("priority") or "").strip().upper()
        if row.get("description_pattern") is None or not event_type or not priority:
            continue
        normalized_map[pattern] = (event_type, priority)

    return [
        (pattern, event_type, priority)
        for pattern, (event_type, priority) in normalized_map.items()
    ]


def _load_normalized_type_priority_map(mapping_path: Path) -> list[tuple[str, str, str]]:
    if mapping_path.suffix.lower() == ".json":
        return _load_normalized_type_priority_map_from_json(mapping_path)
    return _load_normalized_type_priority_map_from_csv(mapping_path)


def _escape_clickhouse_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _decode_csv_pattern(value: str) -> str:
    if not value:
        return value

    # CSV exports may preserve escape sequences literally; decode them so the
    # in-memory pattern matches ClickHouse normalized DESCRIPTION values.
    # unicode_escape round-trips bytes through latin-1, which mangles any
    # non-ASCII character (an em dash becomes mojibake), so only decode escape
    # sequences when there is nothing non-ASCII to corrupt.
    decoded = codecs.decode(value, "unicode_escape") if value.isascii() else value
    return decoded.replace("\r\n", "\n")


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
        SETTINGS mutations_sync = 2
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


def _build_priority_summary_query(source_table: str) -> str:
    return f'''
        SELECT
            count() AS total_rows,
            countIf(ifNull(priority, '') = '') AS empty_priority_rows,
            countIf(ifNull(priority, '') != '') AS non_empty_priority_rows
        FROM {source_table}
    '''


def _build_priority_breakdown_query(source_table: str) -> str:
    return f'''
        SELECT
            upperUTF8(ifNull(priority, '')) AS priority_value,
            count() AS row_count
        FROM {source_table}
        GROUP BY priority_value
        ORDER BY row_count DESC, priority_value
    '''


def _build_mutations_query(source_table: str) -> str:
    return f'''
        SELECT
            mutation_id,
            is_done,
            parts_to_do,
            create_time,
            latest_fail_reason
        FROM system.mutations
        WHERE table = '{_escape_clickhouse_literal(source_table)}'
        ORDER BY create_time DESC
        LIMIT 20
    '''


def run(args: argparse.Namespace) -> None:
    params = _read_clickhouse_config(args.db_config, args.clickhouse_section)

    if args.check_mutations:
        print("=== Recent mutations (is_done=0 means still running) ===")
        print(_run_clickhouse_query(params, _build_mutations_query(args.source_table)).strip())
        return

    normalized_type_priority_map = _load_normalized_type_priority_map(Path(args.mapping_path))

    if args.priority_summary:
        print("=== Priority Summary ===")
        print(_run_clickhouse_query(params, _build_priority_summary_query(args.source_table)).strip())
        print("\n=== Priority Breakdown ===")
        print(_run_clickhouse_query(params, _build_priority_breakdown_query(args.source_table)).strip())
        return

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
        "--mapping-path",
        default=str(DEFAULT_JSON_PATH),
        help="JSON or CSV file containing description_pattern, TYPE, and priority mappings",
    )
    parser.add_argument(
        "--csv-path",
        dest="mapping_path",
        help="Deprecated alias for --mapping-path",
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
    parser.add_argument(
        "--check-mutations",
        action="store_true",
        help="Print recent ClickHouse mutations for the source table and exit; "
             "is_done=0 means an ALTER UPDATE is still applying",
    )
    parser.add_argument(
        "--priority-summary",
        action="store_true",
        help="Print total rows, empty/non-empty priority counts, and priority distribution for the source table",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()