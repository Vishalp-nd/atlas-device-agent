#!/usr/bin/env python3
"""
Fetch critical-event rows from Snowflake, classify with SVM, and store in PostgreSQL.

Flow:
1) Pull rows from `device_critical_event` in batches
2) Predict TYPE (INFO/ERROR) from CODE + DESCRIPTION
3) Insert into a local PostgreSQL table with all source columns plus TYPE
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import pickle
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import psycopg2.extras
from dotenv import load_dotenv

from fetch_device_config import connect_to_db, connect_to_snowflake, read_db_config

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "svm_type_classifier.pkl"
DEFAULT_TABLE_NAME = "criticalinfo_snowflakes_classified"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


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
    "S3_PATH",
    "TENANT_ID",
    "UPSERT_TIME",
    "LOADED_TO_SNOWFLAKE_ON",
]
INT_LIKE_COLUMNS = ["CODE_AUX", "COUNT", "TENANT_ID"]


def _init_postgres(conn, table_name: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                \"DEVICE_ID\" text,
                \"TIMESTAMP\" timestamp without time zone,
                \"PROCESS_NAME\" text,
                \"CODE\" double precision,
                \"CODE_AUX\" bigint,
                \"COUNT\" bigint,
                \"DESCRIPTION\" text,
                \"DEVICE_VERSION\" text,
                \"SYS_UPTIME\" double precision,
                \"S3_PATH\" text,
                \"TENANT_ID\" bigint,
                \"UPSERT_TIME\" timestamp without time zone,
                \"LOADED_TO_SNOWFLAKE_ON\" timestamp without time zone,
                type text,
                UNIQUE (\"DEVICE_ID\", \"TIMESTAMP\", \"PROCESS_NAME\", \"CODE\", \"DESCRIPTION\")
            )
            """
        )
        cursor.execute(
            f'CREATE INDEX IF NOT EXISTS idx_ce_ts ON {table_name} ("TIMESTAMP")'
        )
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_ce_type ON {table_name} (type)"
        )
        cursor.execute(
            f'CREATE INDEX IF NOT EXISTS idx_ce_code ON {table_name} ("CODE")'
        )
        cursor.execute(
            f'CREATE INDEX IF NOT EXISTS idx_ce_proc ON {table_name} ("PROCESS_NAME")'
        )
        try:
            cursor.execute(
                f"""
                ALTER TABLE {table_name}
                ADD CONSTRAINT {table_name}_uniq_device_ts_proc_code_desc
                UNIQUE (\"DEVICE_ID\", \"TIMESTAMP\", \"PROCESS_NAME\", \"CODE\", \"DESCRIPTION\")
                """
            )
        except Exception as exc:
            conn.rollback()
            with conn.cursor() as retry_cursor:
                retry_cursor.execute(
                    """
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = %s
                    """,
                    (f"{table_name}_uniq_device_ts_proc_code_desc",),
                )
                if retry_cursor.fetchone() is None:
                    print(
                        "Warning: could not create unique constraint for deduplication. "
                        f"Continuing without ON CONFLICT support: {exc}"
                    )
    conn.commit()


def _has_dedup_constraint(conn, table_name: str) -> bool:
    constraint_name = f"{table_name}_uniq_device_ts_proc_code_desc"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = %s
            """,
            (constraint_name,),
        )
        return cursor.fetchone() is not None


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
    model,
    rows: list[tuple[object, ...]],
    selected_columns: list[str],
) -> tuple[list[tuple], int]:
    df = pd.DataFrame(rows, columns=selected_columns)
    for missing_column in SOURCE_COLUMNS:
        if missing_column not in df.columns:
            df[missing_column] = None
    df = df.reindex(columns=SOURCE_COLUMNS)

    features = df["DESCRIPTION"].fillna("").astype(str)

    try:
        predicted = model.predict(features)
    except Exception as exc:
        raise RuntimeError(
            "Model prediction failed. The loaded model is likely incompatible with the current "
            "description-only pipeline. Retrain the model with pipeline/svm_type_classifier.py "
            "and rerun the pipeline."
        ) from exc

    if len(predicted) != len(df):
        raise RuntimeError(
            f"Model returned {len(predicted)} predictions for {len(df)} rows. "
            "This usually means an old CODE+DESCRIPTION model is being used with the new "
            "description-only pipeline. Retrain the model and rerun."
        )

    # Snowflake numeric columns may arrive as floats when NULLs are present.
    # Coerce bigint-like columns back to true integers for PostgreSQL COPY.
    for column in INT_LIKE_COLUMNS:
        numeric_series = pd.to_numeric(df[column], errors="coerce")
        fractional_mask = numeric_series.notna() & (numeric_series % 1 != 0)
        if fractional_mask.any():
            sample = numeric_series[fractional_mask].iloc[0]
            raise RuntimeError(
                f"Column {column} contains non-integer value {sample!r}; cannot load into bigint"
            )
        df[column] = numeric_series.astype("Int64")

    # Vectorised output — avoids itertuples loop over potentially millions of rows
    df["type"] = predicted.astype(str)
    safe_df = df.astype(object).where(pd.notnull(df), None)
    out_rows = [tuple(row) for row in safe_df.values.tolist()]

    return out_rows, len(rows)


def _upsert_rows(conn, table_name: str, rows: list[tuple], page_size: int, has_constraint: bool = True) -> int:
    if not rows:
        return 0

    staging_table = "critical_event_stage"
    copy_buffer = io.StringIO()
    writer = csv.writer(copy_buffer, lineterminator="\n")
    writer.writerows(rows)
    copy_buffer.seek(0)

    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TEMP TABLE IF NOT EXISTS {staging_table} (
                \"DEVICE_ID\" text,
                \"TIMESTAMP\" timestamp without time zone,
                \"PROCESS_NAME\" text,
                \"CODE\" double precision,
                \"CODE_AUX\" bigint,
                \"COUNT\" bigint,
                \"DESCRIPTION\" text,
                \"DEVICE_VERSION\" text,
                \"SYS_UPTIME\" double precision,
                \"S3_PATH\" text,
                \"TENANT_ID\" bigint,
                \"UPSERT_TIME\" timestamp without time zone,
                \"LOADED_TO_SNOWFLAKE_ON\" timestamp without time zone,
                type text
            ) ON COMMIT DELETE ROWS
            """,
        )
        cursor.copy_expert(
            f"""
            COPY {staging_table} (
                \"DEVICE_ID\",
                \"TIMESTAMP\",
                \"PROCESS_NAME\",
                \"CODE\",
                \"CODE_AUX\",
                \"COUNT\",
                \"DESCRIPTION\",
                \"DEVICE_VERSION\",
                \"SYS_UPTIME\",
                \"S3_PATH\",
                \"TENANT_ID\",
                \"UPSERT_TIME\",
                \"LOADED_TO_SNOWFLAKE_ON\",
                type
            )
            FROM STDIN WITH (FORMAT CSV)
            """,
            copy_buffer,
        )
        if has_constraint:
            cursor.execute(
                f"""
                INSERT INTO {table_name} (
                    \"DEVICE_ID\",
                    \"TIMESTAMP\",
                    \"PROCESS_NAME\",
                    \"CODE\",
                    \"CODE_AUX\",
                    \"COUNT\",
                    \"DESCRIPTION\",
                    \"DEVICE_VERSION\",
                    \"SYS_UPTIME\",
                    \"S3_PATH\",
                    \"TENANT_ID\",
                    \"UPSERT_TIME\",
                    \"LOADED_TO_SNOWFLAKE_ON\",
                    type
                )
                SELECT
                    \"DEVICE_ID\",
                    \"TIMESTAMP\",
                    \"PROCESS_NAME\",
                    \"CODE\",
                    \"CODE_AUX\",
                    \"COUNT\",
                    \"DESCRIPTION\",
                    \"DEVICE_VERSION\",
                    \"SYS_UPTIME\",
                    \"S3_PATH\",
                    \"TENANT_ID\",
                    \"UPSERT_TIME\",
                    \"LOADED_TO_SNOWFLAKE_ON\",
                    type
                FROM {staging_table}
                ON CONFLICT (\"DEVICE_ID\", \"TIMESTAMP\", \"PROCESS_NAME\", \"CODE\", \"DESCRIPTION\") DO NOTHING
                """
            )
        else:
            cursor.execute(
                f"""
                INSERT INTO {table_name} (
                    \"DEVICE_ID\",
                    \"TIMESTAMP\",
                    \"PROCESS_NAME\",
                    \"CODE\",
                    \"CODE_AUX\",
                    \"COUNT\",
                    \"DESCRIPTION\",
                    \"DEVICE_VERSION\",
                    \"SYS_UPTIME\",
                    \"S3_PATH\",
                    \"TENANT_ID\",
                    \"UPSERT_TIME\",
                    \"LOADED_TO_SNOWFLAKE_ON\",
                    type
                )
                SELECT
                    \"DEVICE_ID\",
                    \"TIMESTAMP\",
                    \"PROCESS_NAME\",
                    \"CODE\",
                    \"CODE_AUX\",
                    \"COUNT\",
                    \"DESCRIPTION\",
                    \"DEVICE_VERSION\",
                    \"SYS_UPTIME\",
                    \"S3_PATH\",
                    \"TENANT_ID\",
                    \"UPSERT_TIME\",
                    \"LOADED_TO_SNOWFLAKE_ON\",
                    type
                FROM {staging_table}
                """
            )
        inserted = cursor.rowcount
    return inserted


def run_pipeline(args: argparse.Namespace) -> None:
    model_path = Path(args.model)
    table_name = args.table_name

    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")

    with model_path.open("rb") as f:
        model = pickle.load(f)

    sf_conn = connect_to_snowflake(
        args.db_config,
        args.snowflake_section,
        aws_profile=args.aws_profile,
    )
    if sf_conn is None:
        raise RuntimeError("Failed to connect to Snowflake")

    pg_params = read_db_config(args.db_config, args.postgres_section)
    pg_conn = connect_to_db(pg_params)
    if pg_conn is None:
        raise RuntimeError("Failed to connect to PostgreSQL")

    _init_postgres(pg_conn, table_name)
    has_constraint = _has_dedup_constraint(pg_conn, table_name)  # checked once, not per batch
    pg_conn.commit()  # close open transaction so PG doesn't kill the connection during Snowflake calls
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
            classified_rows, fetched = _predict_batch(model, raw_rows, selected_columns)
            predict_seconds = time.perf_counter() - predict_started
            attempted = len(classified_rows)

            insert_started = time.perf_counter()
            inserted = _upsert_rows(pg_conn, table_name, classified_rows, args.insert_page_size, has_constraint)
            insert_seconds = time.perf_counter() - insert_started

            total_fetched += fetched
            total_inserted += inserted
            batches += 1
            total_predict_seconds += predict_seconds
            total_insert_seconds += insert_seconds
            if batches % args.commit_every == 0:
                commit_started = time.perf_counter()
                pg_conn.commit()
                commit_seconds = time.perf_counter() - commit_started
            else:
                commit_seconds = 0.0

            batch_seconds = time.perf_counter() - batch_started
            print(
                f"Batch {batches}: fetched={fetched}, attempted={attempted}, inserted={inserted}, "
                f"predict={predict_seconds:.2f}s ({_format_rate(fetched, predict_seconds)}), "
                f"insert={insert_seconds:.2f}s ({_format_rate(attempted, insert_seconds)} attempted/s), "
                f"commit={commit_seconds:.2f}s, total_batch={batch_seconds:.2f}s, "
                f"total_fetched={total_fetched}, total_inserted={total_inserted}"
            )
        final_commit_started = time.perf_counter()
        pg_conn.commit()
        final_commit_seconds = time.perf_counter() - final_commit_started

    finally:
        sf_conn.close()
        pg_conn.close()

    total_seconds = time.perf_counter() - run_started
    print("Done.")
    print(f"PostgreSQL table: {table_name}")
    print(f"Total fetched from Snowflake: {total_fetched}")
    print(f"Total inserted into PostgreSQL: {total_inserted}")
    print(f"Total predict time: {total_predict_seconds:.2f}s")
    print(f"Total insert time: {total_insert_seconds:.2f}s")
    print(f"Final commit time: {final_commit_seconds:.2f}s")
    print(f"Total runtime: {total_seconds:.2f}s ({_format_rate(total_fetched, total_seconds)})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch critical events from Snowflake, classify, and store in PostgreSQL",
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
        "--insert-page-size",
        type=int,
        default=10_000,
        help="Rows per PostgreSQL execute_values page (default 10k — keep between 5k and 20k)",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=5,
        help="Commit after this many batches",
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
        "--postgres-section",
        default="IRAVATH_DB",
        help="Section in db_credentials.ini for local PostgreSQL",
    )
    parser.add_argument(
        "--table-name",
        default=DEFAULT_TABLE_NAME,
        help="Target PostgreSQL table for classified critical events",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
