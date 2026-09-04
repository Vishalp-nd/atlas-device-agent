#!/usr/bin/env python3
"""
Fetch critical-event rows from Snowflake, classify TYPE/priority, and store in ClickHouse.

Flow:
1) Pull rows from `device_critical_event` in batches
2) Classify TYPE (INFO/ERROR) and priority (P0-P4/P100-P104) per row: normalized-description lookup
   in unique_cinfo_op_mapped.json first, SVM model fallback for TYPE, semantic-similarity fallback
   for priority (see cinfo_classifier.py)
3) Insert into a ClickHouse table with all source columns plus type and priority

Each (window, OTA-versions) run is tracked in a registry table (criticalinfo_poll_runs) and the
window's existing rows are deleted before the fresh classified insert, so a rerun -- including a
retry after a mid-run crash -- never duplicates rows and a window already completed is skipped by
default (see --force).
"""

from __future__ import annotations

import argparse
import csv
import configparser
import io
import os
import pickle
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
from dotenv import load_dotenv

from cinfo_classifier import (
    CinfoClassifier,
    DEFAULT_JSON_PATH,
    append_new_patterns_to_json,
    upsert_new_patterns_to_clickhouse,
)
from fetch_device_config import connect_to_snowflake

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "svm_type_classifier.pkl"
DEFAULT_TABLE_NAME = "criticalinfo_snowflakes_data"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_PRIORITY_MAP_TABLE = "unique_cinfo_priority_map"
DEFAULT_REGISTRY_TABLE = "criticalinfo_poll_runs"
LOCK_FILE = Path(__file__).resolve().parent / "OUTPUT" / ".critical_events_pipeline.lock"


def _parse_csv_env(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _load_runtime_filters() -> tuple[list[str], list[str]]:
    load_dotenv(str(DEFAULT_ENV_PATH), override=False)

    if "IGNORED_CODES" not in os.environ:
        raise RuntimeError(
            "Missing required env var IGNORED_CODES. Configure it in the repo-root .env "
            "(use an empty value to disable ignored-code filtering)."
        )
    if "ALLOWED_OTA_VERSIONS" not in os.environ:
        raise RuntimeError(
            "Missing required env var ALLOWED_OTA_VERSIONS. Configure it in the repo-root .env "
            "with comma-separated OTA versions."
        )

    ignored_raw = os.environ.get("IGNORED_CODES", "")
    allowed_raw = os.environ.get("ALLOWED_OTA_VERSIONS", "")

    ignored_codes = _parse_csv_env(ignored_raw)
    allowed_ota_versions = _parse_csv_env(allowed_raw)
    if not allowed_ota_versions:
        raise RuntimeError(
            "ALLOWED_OTA_VERSIONS is empty. Configure it in the repo-root .env "
            "with comma-separated OTA versions."
        )
    return ignored_codes, allowed_ota_versions


IGNORED_CODES, ALLOWED_OTA_VERSIONS = _load_runtime_filters()
SOURCE_COLUMNS = [
    "DEVICE_ID",
    "TIMESTAMP",
    "PROCESS_NAME",
    "CODE",
    "CODE_AUX",
    "COUNT",
    "DESCRIPTION",
    "DEVICE_VERSION",
    "SYS_UPTIME",
    "TENANT_ID",
    "S3_PATH",
    "UPSERT_TIME",
    "LOADED_TO_SNOWFLAKE_ON",
]
INT_LIKE_COLUMNS = ["CODE_AUX", "COUNT", "TENANT_ID"]


def _acquire_lock():
    import fcntl

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = LOCK_FILE.open("w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another critical_events_pipeline instance is running. Waiting for it to finish...")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    return lock_fd


def _release_lock(lock_fd) -> None:
    import fcntl

    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()


def _to_clickhouse_datetime(value: str) -> str:
    return str(value).replace("T", " ")[:19]


def _read_clickhouse_config(config_file: str, section: str) -> dict[str, object]:
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


def _clickhouse_client_args(params: dict[str, object]) -> list[str]:
    host = str(params["host"])
    port = str(params["port"])
    user = str(params["user"])
    database = str(params["database"])
    password = str(params.get("password", ""))

    # The repo's local ClickHouse runs in Docker, while the host may still have
    # an older clickhouse-client installed. Route localhost:9000 traffic through
    # the container so client/server syntax stays aligned.
    if host in {"localhost", "127.0.0.1"} and port == "9000":
        args = [
            "sudo",
            "docker",
            "exec",
            "-i",
            "clickhouse",
            "clickhouse-client",
            "--user",
            user,
            "--database",
            database,
        ]
        if password:
            args.extend(["--password", password])
        return args

    args = [
        "clickhouse-client",
        "--host",
        host,
        "--port",
        port,
        "--user",
        user,
        "--database",
        database,
    ]
    if password:
        args.extend(["--password", password])
    return args


def _run_clickhouse_query(params: dict[str, object], query: str, input_text: str | None = None) -> str:
    import subprocess

    command = _clickhouse_client_args(params) + ["--query", query]
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ClickHouse query failed")
    return result.stdout


def _init_clickhouse(params: dict[str, object], table_name: str) -> None:
    _run_clickhouse_query(
        params,
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            \"DEVICE_ID\" String,
            \"TIMESTAMP\" DateTime,
            \"PROCESS_NAME\" String,
            \"CODE\" Float64,
            \"CODE_AUX\" Int64,
            \"COUNT\" UInt64,
            \"DESCRIPTION\" String,
            \"DEVICE_VERSION\" String,
            \"SYS_UPTIME\" Float64,
            \"S3_PATH\" String,
            \"TENANT_ID\" UInt64,
            \"UPSERT_TIME\" DateTime,
            \"LOADED_TO_SNOWFLAKE_ON\" DateTime,
            type String
        ) ENGINE = ReplacingMergeTree
        PARTITION BY toYYYYMM(\"TIMESTAMP\")
        ORDER BY (\"DEVICE_ID\", \"TIMESTAMP\", \"PROCESS_NAME\", \"CODE\", \"DESCRIPTION\")
        """,
    )
    # priority is not added here -- the live table already has it; this code makes no
    # schema changes (see cinfo_classifier for how priority values are computed/written).


def _ensure_registry_table(params: dict[str, object], table_name: str) -> None:
    _run_clickhouse_query(
        params,
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            window_start DateTime,
            window_end DateTime,
            ota_versions_signature String,
            status String,
            rows_fetched UInt64,
            rows_inserted UInt64,
            started_at DateTime,
            error_message Nullable(String)
        ) ENGINE = MergeTree
        ORDER BY (window_start, window_end, ota_versions_signature, started_at)
        """,
    )


def _latest_run_status(
    params: dict[str, object], table_name: str, window_start: str, window_end: str, signature: str
) -> str | None:
    query = f"""
        SELECT status FROM {table_name}
        WHERE window_start = '{_escape_sql_literal(window_start)}'
          AND window_end = '{_escape_sql_literal(window_end)}'
          AND ota_versions_signature = '{_escape_sql_literal(signature)}'
        ORDER BY started_at DESC
        LIMIT 1
    """
    output = _run_clickhouse_query(params, query).strip()
    return output or None


def _insert_registry_row(
    params: dict[str, object],
    table_name: str,
    window_start: str,
    window_end: str,
    signature: str,
    status: str,
    rows_fetched: int,
    rows_inserted: int,
    error_message: str | None = None,
) -> None:
    copy_buffer = io.StringIO()
    writer = csv.writer(copy_buffer, lineterminator="\n")
    writer.writerow(
        [
            "window_start",
            "window_end",
            "ota_versions_signature",
            "status",
            "rows_fetched",
            "rows_inserted",
            "started_at",
            "error_message",
        ]
    )
    writer.writerow(
        [
            window_start,
            window_end,
            signature,
            status,
            rows_fetched,
            rows_inserted,
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            error_message or "",
        ]
    )
    copy_buffer.seek(0)
    _run_clickhouse_query(
        params,
        f"INSERT INTO {table_name} FORMAT CSVWithNames",
        input_text=copy_buffer.getvalue(),
    )


def _delete_window_rows(params: dict[str, object], table_name: str, window_start: str, window_end: str) -> None:
    _run_clickhouse_query(
        params,
        f"""
        ALTER TABLE {table_name}
        DELETE WHERE \"TIMESTAMP\" >= '{_escape_sql_literal(window_start)}'
          AND \"TIMESTAMP\" < '{_escape_sql_literal(window_end)}'
        """,
    )


def _get_available_source_columns(sf_conn) -> list[str]:
    cursor = sf_conn.cursor()
    try:
        cursor.execute("DESC TABLE device_critical_event")
        rows = cursor.fetchall()
        return [str(row[0]) for row in rows]
    finally:
        cursor.close()


def _build_query(selected_columns: list[str], has_limit: bool) -> str:
    select_list = ",\n            ".join(selected_columns)
    where_clauses = [
        "TIMESTAMP >= %s",
        "TIMESTAMP < %s",
    ]
    if IGNORED_CODES:
        ignored_codes = ", ".join(str(code) for code in IGNORED_CODES)
        where_clauses.append(f"CODE NOT IN ({ignored_codes})")

    if ALLOWED_OTA_VERSIONS:
        allowed_versions = " OR ".join(
            f"CONTAINS(DEVICE_VERSION, '{_escape_sql_literal(version)}')"
            for version in ALLOWED_OTA_VERSIONS
        )
        where_clauses.append(f"({allowed_versions})")

    where_sql = "\n          AND ".join(where_clauses)
    query = f"""
        SELECT
            {select_list}
        FROM device_critical_event
        WHERE {where_sql}
    """
    if has_limit:
        query += "\n        LIMIT %s"
    return query


def _format_rate(rows: int, seconds: float) -> str:
    if seconds <= 0:
        return "inf"
    return f"{rows / seconds:,.0f} rows/s"


def _iter_snowflake_batches(
    sf_conn,
    selected_columns: list[str],
    start_ts: str,
    end_ts: str,
    batch_size: int,
    limit: int | None,
) -> Iterable[list[tuple[object, ...]]]:
    cursor = sf_conn.cursor()
    try:
        has_limit = limit is not None
        query = _build_query(selected_columns, has_limit)

        params: list = [start_ts, end_ts]
        if has_limit:
            params.append(limit)

        cursor.execute(query, tuple(params))
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            yield rows
    finally:
        cursor.close()


def _predict_batch(
    classifier: CinfoClassifier,
    rows: list[tuple[object, ...]],
    selected_columns: list[str],
) -> tuple[list[tuple], int]:
    df = pd.DataFrame(rows, columns=selected_columns)
    for missing_column in SOURCE_COLUMNS:
        if missing_column not in df.columns:
            df[missing_column] = None
    df = df.reindex(columns=SOURCE_COLUMNS)

    classified = classifier.classify(df, description_col="DESCRIPTION", code_col="CODE")
    classified = classifier.assign_missing_priorities(classified)

    # Snowflake numeric columns may arrive as floats when NULLs are present.
    # Coerce bigint-like columns back to true integers for PostgreSQL COPY.
    for column in INT_LIKE_COLUMNS:
        numeric_series = pd.to_numeric(classified[column], errors="coerce")
        fractional_mask = numeric_series.notna() & (numeric_series % 1 != 0)
        if fractional_mask.any():
            sample = numeric_series[fractional_mask].iloc[0]
            raise RuntimeError(
                f"Column {column} contains non-integer value {sample!r}; cannot load into bigint"
            )
        classified[column] = numeric_series.astype("Int64")

    # Vectorised output — avoids itertuples loop over potentially millions of rows
    classified["type"] = classified["type"].fillna("").astype(str)
    classified["priority"] = classified["priority"].fillna("").astype(str)
    output_columns = SOURCE_COLUMNS + ["type", "priority"]
    output_df = classified[output_columns]
    safe_df = output_df.astype(object).where(pd.notnull(output_df), None)
    out_rows = [tuple(row) for row in safe_df.values.tolist()]

    return out_rows, len(rows)


def _normalise_clickhouse_value(column_name: str, value: object) -> object:
    if value is None:
        return ""
    if column_name in {"TIMESTAMP", "UPSERT_TIME", "LOADED_TO_SNOWFLAKE_ON"}:
        return _to_clickhouse_datetime(value)
    return value


CLICKHOUSE_ROW_COLUMNS = SOURCE_COLUMNS + ["type", "priority"]


def _insert_clickhouse_rows(params: dict[str, object], table_name: str, rows: list[tuple]) -> int:
    if not rows:
        return 0

    copy_buffer = io.StringIO()
    writer = csv.writer(copy_buffer, lineterminator="\n")
    writer.writerow(CLICKHOUSE_ROW_COLUMNS)
    for row in rows:
        writer.writerow(
            [_normalise_clickhouse_value(column_name, value) for column_name, value in zip(CLICKHOUSE_ROW_COLUMNS, row)]
        )
    copy_buffer.seek(0)

    _run_clickhouse_query(
        params,
        f"INSERT INTO {table_name} FORMAT CSVWithNames",
        input_text=copy_buffer.getvalue(),
    )
    return len(rows)


def _run_pipeline_body(args: argparse.Namespace, classifier: CinfoClassifier, ch_params: dict[str, object]) -> tuple[int, int]:
    table_name = args.table_name

    sf_conn = connect_to_snowflake(
        args.db_config,
        args.snowflake_section,
        aws_profile=args.aws_profile,
    )
    if sf_conn is None:
        raise RuntimeError("Failed to connect to Snowflake")

    _init_clickhouse(ch_params, table_name)
    _delete_window_rows(ch_params, table_name, _to_clickhouse_datetime(args.start_ts), _to_clickhouse_datetime(args.end_ts))

    available_columns = _get_available_source_columns(sf_conn)
    selected_columns = [col for col in SOURCE_COLUMNS if col in available_columns]
    missing_columns = [col for col in SOURCE_COLUMNS if col not in selected_columns]
    if missing_columns:
        print(
            "Snowflake columns not present and will be inserted as NULL: "
            + ", ".join(missing_columns)
        )
    print(
        "Ignoring Snowflake rows for CODE values: "
        + ", ".join(str(code) for code in IGNORED_CODES)
    )
    print(
        "Restricting Snowflake rows to OTA versions: "
        + ", ".join(ALLOWED_OTA_VERSIONS)
    )

    total_fetched = 0
    total_inserted = 0
    batches = 0
    total_predict_seconds = 0.0
    total_insert_seconds = 0.0
    run_started = time.perf_counter()

    try:
        for raw_rows in _iter_snowflake_batches(
            sf_conn=sf_conn,
            selected_columns=selected_columns,
            start_ts=args.start_ts,
            end_ts=args.end_ts,
            batch_size=args.batch_size,
            limit=args.limit,
        ):
            batch_started = time.perf_counter()
            predict_started = time.perf_counter()
            classified_rows, fetched = _predict_batch(classifier, raw_rows, selected_columns)
            predict_seconds = time.perf_counter() - predict_started
            attempted = len(classified_rows)

            insert_started = time.perf_counter()
            inserted = _insert_clickhouse_rows(ch_params, table_name, classified_rows)
            insert_seconds = time.perf_counter() - insert_started

            total_fetched += fetched
            total_inserted += inserted
            batches += 1
            total_predict_seconds += predict_seconds
            total_insert_seconds += insert_seconds

            batch_seconds = time.perf_counter() - batch_started
            print(
                f"Batch {batches}: fetched={fetched}, attempted={attempted}, inserted={inserted}, "
                f"predict={predict_seconds:.2f}s ({_format_rate(fetched, predict_seconds)}), "
                f"insert={insert_seconds:.2f}s ({_format_rate(attempted, insert_seconds)} attempted/s), "
                f"total_batch={batch_seconds:.2f}s, "
                f"total_fetched={total_fetched}, total_inserted={total_inserted}"
            )
    finally:
        sf_conn.close()

    total_seconds = time.perf_counter() - run_started
    print("Done.")
    print(f"Target clickhouse table: {table_name}")
    print(f"Total fetched from Snowflake: {total_fetched}")
    print(f"Total inserted into clickhouse: {total_inserted}")
    print(f"Total predict time: {total_predict_seconds:.2f}s")
    print(f"Total insert time: {total_insert_seconds:.2f}s")
    print(f"Total runtime: {total_seconds:.2f}s ({_format_rate(total_fetched, total_seconds)})")

    return total_fetched, total_inserted


def run_pipeline(args: argparse.Namespace) -> None:
    model_path = Path(args.model)
    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")

    lock_fd = _acquire_lock()
    try:
        ota_signature = ",".join(sorted(ALLOWED_OTA_VERSIONS))
        window_start = _to_clickhouse_datetime(args.start_ts)
        window_end = _to_clickhouse_datetime(args.end_ts)

        ch_params = _read_clickhouse_config(args.db_config, args.clickhouse_section)
        _ensure_registry_table(ch_params, args.registry_table)
        latest_status = _latest_run_status(ch_params, args.registry_table, window_start, window_end, ota_signature)
        if latest_status == "completed" and not args.force:
            print(
                f"Window {args.start_ts} -> {args.end_ts} for OTA versions [{ota_signature}] "
                f"already completed in {args.registry_table}; skipping (use --force to redo)."
            )
            return
        _insert_registry_row(ch_params, args.registry_table, window_start, window_end, ota_signature, "running", 0, 0)

        with model_path.open("rb") as f:
            model = pickle.load(f)
        classifier = CinfoClassifier(json_path=Path(args.json_map_path), svm_model=model)

        try:
            total_fetched, total_inserted = _run_pipeline_body(args, classifier, ch_params)
        except Exception as exc:
            _insert_registry_row(
                ch_params, args.registry_table, window_start, window_end, ota_signature,
                "failed", 0, 0, error_message=str(exc)[:2000],
            )
            raise

        new_rows = classifier.new_patterns()
        if new_rows:
            appended = append_new_patterns_to_json(Path(args.json_map_path), new_rows)
            upserted = upsert_new_patterns_to_clickhouse(ch_params, args.priority_map_table, new_rows)
            print(
                f"Appended {appended} new pattern(s) to {args.json_map_path}; "
                f"upserted {upserted} into {args.priority_map_table}"
            )
        _insert_registry_row(
            ch_params, args.registry_table, window_start, window_end, ota_signature,
            "completed", total_fetched, total_inserted,
        )
    finally:
        _release_lock(lock_fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch critical events from Snowflake, classify, and store in ClickHouse",
    )
    parser.add_argument(
        "--db-config",
        default=str(Path(__file__).resolve().parent.parent / "db_credentials.ini"),
        help="Path to db_credentials.ini",
    )
    parser.add_argument(
        "--snowflake-section",
        default="SNOWFLAKE_DB",
        help="Section in db_credentials.ini to connect Snowflake",
    )
    parser.add_argument(
        "--aws-profile",
        default=None,
        help="AWS profile name to use for fetching the Snowflake private key from SSM",
    )
    parser.add_argument(
        "--start-ts",
        required=True,
        help="Inclusive start timestamp/date (e.g. 2026-06-25)",
    )
    parser.add_argument(
        "--end-ts",
        required=True,
        help="Exclusive end timestamp/date (e.g. 2026-06-26)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100_000,
        help="Snowflake fetchmany batch size (default 100k — keep between 50k and 500k)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional total LIMIT on Snowflake query",
    )
    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL_PATH),
        help="Path to trained SVM model pickle",
    )
    parser.add_argument(
        "--clickhouse-section",
        default="CLICKHOUSE_DB",
        help="Section in db_credentials.ini for ClickHouse",
    )
    parser.add_argument(
        "--table-name",
        default=DEFAULT_TABLE_NAME,
        help="Target ClickHouse table for classified critical events",
    )
    parser.add_argument(
        "--json-map-path",
        default=str(DEFAULT_JSON_PATH),
        help="JSON file mapping normalized description -> TYPE/priority (unique_cinfo_op_mapped.json)",
    )
    parser.add_argument(
        "--priority-map-table",
        default=DEFAULT_PRIORITY_MAP_TABLE,
        help="ClickHouse table newly-discovered normalized patterns are upserted into",
    )
    parser.add_argument(
        "--registry-table",
        default=DEFAULT_REGISTRY_TABLE,
        help="ClickHouse run-registry table tracking completed (window, OTA-versions) runs",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redo this window even if the registry already marks it completed",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
