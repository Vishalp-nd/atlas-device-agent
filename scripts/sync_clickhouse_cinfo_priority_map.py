#!/usr/bin/env python3
"""Create and populate ClickHouse unique_cinfo_priority_map from critical info data.

This mirrors scripts/extract_error_logs.py normalization: any whitespace-
delimited token containing a digit is treated as variable data and replaced
with <N>. The normalized DESCRIPTION becomes description_pattern.

For each unique (CODE, TYPE, description_pattern) in the source table, the
script inserts one row into unique_cinfo_priority_map with a representative
sample_description and a NULL priority.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import subprocess
from pathlib import Path


DEFAULT_CLICKHOUSE_SECTION = "CLICKHOUSE_DB"
DEFAULT_SOURCE_TABLE = "criticalinfo_snowflakes_data"
DEFAULT_TARGET_TABLE = "unique_cinfo_priority_map"


def read_clickhouse_config(config_file: Path, section: str) -> dict[str, object]:
    parser = configparser.ConfigParser()
    parser.read(config_file)
    if not parser.has_section(section):
        raise ValueError(f"Section '{section}' not found in {config_file}")

    return {
        "host": parser.get(section, "host", fallback="127.0.0.1"),
        "port": parser.getint(section, "port", fallback=9000),
        "user": parser.get(section, "user", fallback="default"),
        "password": parser.get(section, "password", fallback=""),
        "database": parser.get(section, "database", fallback="default"),
    }


def clickhouse_client_args(params: dict[str, object]) -> list[str]:
    args = [
        "clickhouse-client",
        "--host",
        str(params["host"]),
        "--port",
        str(params["port"]),
        "--user",
        str(params["user"]),
        "--database",
        str(params["database"]),
    ]
    password = str(params.get("password", ""))
    if password:
        args.extend(["--password", password])
    return args


def run_clickhouse_query(params: dict[str, object], query: str) -> str:
    result = subprocess.run(
        clickhouse_client_args(params) + ["--query", query],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ClickHouse query failed")
    return result.stdout


def run_clickhouse_tsv_query(params: dict[str, object], query: str) -> list[list[str]]:
    result = subprocess.run(
        clickhouse_client_args(params) + ["--format", "TabSeparated", "--query", query],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ClickHouse query failed")
    rows: list[list[str]] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        rows.append(next(csv.reader([line], delimiter='\t', quotechar='\x00')))
    return rows


def insert_clickhouse_tsv_rows(params: dict[str, object], target_table: str, rows: list[tuple[str, str, str, str, str]]) -> None:
    if not rows:
        return

    payload_lines = []
    for row in rows:
        payload_lines.append("\t".join(_escape_tsv(value) for value in row))
    payload = "\n".join(payload_lines) + "\n"

    result = subprocess.run(
        clickhouse_client_args(params)
        + [
            "--query",
            f'INSERT INTO {target_table} ("CODE", sample_description, description_pattern, "TYPE", priority) FORMAT TabSeparated',
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ClickHouse insert failed")


def _escape_tsv(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
    )


def build_create_table_sql(target_table: str) -> str:
    return f'''
    CREATE TABLE IF NOT EXISTS {target_table} (
        "CODE" Nullable(Float64),
        sample_description Nullable(String),
        description_pattern Nullable(String),
        "TYPE" Nullable(String),
        priority Nullable(String)
    ) ENGINE = ReplacingMergeTree
    ORDER BY (
        ifNull("CODE", toFloat64(-1)),
        ifNull("TYPE", ''),
        ifNull(description_pattern, '')
    )
    '''


def build_grouped_source_sql(source_table: str) -> str:
    normalized_expr = "replaceRegexpAll(ifNull(\"DESCRIPTION\", ''), '\\S*\\d\\S*', '<N>')"
    return f'''
    SELECT
        "CODE" AS code_value,
        min(ifNull("DESCRIPTION", '')) AS sample_description,
        {normalized_expr} AS description_pattern,
        type AS type_value,
        CAST(NULL, 'Nullable(String)') AS priority
    FROM {source_table}
    GROUP BY
        code_value,
        description_pattern,
        type_value
    '''


def build_existing_rows_sql(target_table: str) -> str:
    return f'''
    SELECT
        "CODE",
        sample_description,
        description_pattern,
        "TYPE",
        priority
    FROM {target_table}
    '''


def build_truncate_table_sql(target_table: str) -> str:
    return f"TRUNCATE TABLE {target_table}"


def sync_rows(params: dict[str, object], source_table: str, target_table: str) -> int:
    source_rows = run_clickhouse_tsv_query(params, build_grouped_source_sql(source_table))
    existing_rows = run_clickhouse_tsv_query(params, build_existing_rows_sql(target_table))

    existing_keys = {
        (row[0], row[3], row[2])
        for row in existing_rows
        if len(row) >= 4
    }

    rows_to_insert: list[tuple[str, str, str, str, str]] = []
    for row in source_rows:
        if len(row) < 5:
            continue
        key = (row[0], row[3], row[2])
        if key in existing_keys:
            continue
        rows_to_insert.append((row[0], row[1], row[2], row[3], row[4]))

    insert_clickhouse_tsv_rows(params, target_table, rows_to_insert)
    return len(rows_to_insert)


def rebuild_rows(params: dict[str, object], source_table: str, target_table: str) -> int:
    source_rows = run_clickhouse_tsv_query(params, build_grouped_source_sql(source_table))
    rows_to_insert: list[tuple[str, str, str, str, str]] = []
    for row in source_rows:
        if len(row) < 5:
            continue
        rows_to_insert.append((row[0], row[1], row[2], row[3], row[4]))

    run_clickhouse_query(params, build_truncate_table_sql(target_table))
    insert_clickhouse_tsv_rows(params, target_table, rows_to_insert)
    return len(rows_to_insert)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-config",
        default=str(repo_root / "db_credentials.ini"),
        help="Path to db_credentials.ini (default: repo-root db_credentials.ini)",
    )
    parser.add_argument(
        "--clickhouse-section",
        default=DEFAULT_CLICKHOUSE_SECTION,
        help=f"Section in db_credentials.ini for ClickHouse (default: {DEFAULT_CLICKHOUSE_SECTION})",
    )
    parser.add_argument(
        "--source-table",
        default=DEFAULT_SOURCE_TABLE,
        help=f"ClickHouse source table containing critical info rows (default: {DEFAULT_SOURCE_TABLE})",
    )
    parser.add_argument(
        "--target-table",
        default=DEFAULT_TARGET_TABLE,
        help=f"ClickHouse target table to create/populate (default: {DEFAULT_TARGET_TABLE})",
    )
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="Only create the target table; do not insert rows.",
    )
    parser.add_argument(
        "--truncate-and-rebuild",
        action="store_true",
        help="Truncate the target table and rebuild it fully from the source table.",
    )
    args = parser.parse_args()

    params = read_clickhouse_config(Path(args.db_config), args.clickhouse_section)
    run_clickhouse_query(params, build_create_table_sql(args.target_table))
    print(f"Ensured ClickHouse table exists: {args.target_table}")

    if args.create_only:
        return

    if args.truncate_and_rebuild:
        inserted = rebuild_rows(params, args.source_table, args.target_table)
        print(
            f"Truncated {args.target_table} and rebuilt it with {inserted} normalized rows "
            f"from {args.source_table}"
        )
        return

    inserted = sync_rows(params, args.source_table, args.target_table)
    print(
        f"Inserted {inserted} missing normalized description rows from "
        f"{args.source_table} into {args.target_table}"
    )


if __name__ == "__main__":
    main()