#!/usr/bin/env python3
"""Query unique CODE/CODE_AUX/DESCRIPTION patterns from local Postgres.

This script helps when DESCRIPTION contains dynamic values such as ON/OFF,
IDs, counters, or timestamps. It groups rows by a normalized DESCRIPTION
pattern so you can see which raw DESCRIPTION values are really the same event
shape.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from psycopg2.extras import execute_values

from fetch_device_config import ENV_TO_DB_SECTION, connect_to_db, read_db_config

NORMALIZED_DESCRIPTION_EXPR = """
regexp_replace(UPPER(COALESCE("DESCRIPTION", '')), ' *[^A-Z ].*$', '%%')
""".strip()

VALID_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
MULTI_PERCENT_RE = re.compile(r"%+")

RAW_UNIQUE_QUERY = """
    SELECT DISTINCT
        "CODE",
        "CODE_AUX",
        type AS "TYPE",
        "DESCRIPTION"
    FROM {table}
    WHERE "TIMESTAMP" >= %s
      AND "TIMESTAMP" < %s
      {version_filter}
      {extra_filters}
    ORDER BY "CODE", "CODE_AUX", "TYPE", "DESCRIPTION"
    LIMIT %s
"""

NORMALIZED_UNIQUE_QUERY = f"""
    WITH base AS (
        SELECT
            "CODE",
            "CODE_AUX",
            type AS "TYPE",
            "DESCRIPTION",
            {NORMALIZED_DESCRIPTION_EXPR} AS DESCRIPTION_PATTERN
        FROM {{table}}
        WHERE "TIMESTAMP" >= %s
          AND "TIMESTAMP" < %s
          {{version_filter}}
          {{extra_filters}}
    )
    SELECT
        "CODE",
        "CODE_AUX",
        "TYPE",
        DESCRIPTION_PATTERN,
        MIN("DESCRIPTION") AS sample_description
    FROM base
    GROUP BY "CODE", "CODE_AUX", "TYPE", DESCRIPTION_PATTERN
    ORDER BY "CODE", "CODE_AUX", "TYPE", DESCRIPTION_PATTERN, sample_description
    LIMIT %s
"""

DEFAULT_PRIORITY_REMAP = {
    "P1": "P0",
    "P2": "P1",
    "P3": "P2",
    "P4": "P3",
    "P5": "P4",
}


def _build_extra_filters(process_names: list[str], tenant_ids: list[str]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []

    if process_names:
        placeholders = ", ".join(["%s"] * len(process_names))
        clauses.append(f"AND \"PROCESS_NAME\" IN ({placeholders})")
        params.extend(process_names)

    if tenant_ids:
        placeholders = ", ".join(["%s"] * len(tenant_ids))
        clauses.append(f"AND \"TENANT_ID\" IN ({placeholders})")
        params.extend(tenant_ids)

    return "\n      ".join(clauses), params


def query_unique_descriptions(
    pg_conn,
    table_name: str,
    start_date: str,
    end_date: str,
    device_version_substring: str | None,
    process_names: list[str],
    tenant_ids: list[str],
    limit: int,
    normalized: bool,
):
    if not VALID_TABLE_RE.fullmatch(table_name):
        raise ValueError(
            f"Invalid table name '{table_name}'. Use format schema.table or table_name."
        )

    extra_filters, extra_params = _build_extra_filters(process_names, tenant_ids)
    version_filter = 'AND "DEVICE_VERSION" ILIKE %s' if device_version_substring else ""
    query_template = NORMALIZED_UNIQUE_QUERY if normalized else RAW_UNIQUE_QUERY
    query = (
        query_template
        .replace("{table}", table_name)
        .replace("{version_filter}", version_filter)
        .replace("{extra_filters}", extra_filters)
    )
    params = [start_date, end_date]
    if device_version_substring:
        params.append(f"%{device_version_substring}%")
    params.extend(extra_params)
    params.append(limit)

    with pg_conn.cursor() as cursor:
        cursor.execute(query, params)
        columns = [col.name for col in cursor.description]
        rows = cursor.fetchall()

    if not rows:
        return []

    first_row = rows[0]
    if isinstance(first_row, dict):
        # RealDictCursor already returns mapping rows.
        return [dict(row) for row in rows]

    return [dict(zip(columns, row)) for row in rows]


def _write_csv(output_path: Path, rows: list[dict]) -> None:
    if not rows:
        return

    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _to_like_pattern(pattern: str | None) -> str:
    if not pattern:
        return ""

    # Collapse any consecutive % wildcards (SQL already produces % directly now).
    return MULTI_PERCENT_RE.sub("%", pattern)


def _parse_priority_remap(raw: str) -> dict[str, str]:
    """Parse remap format OLD:NEW,OLD:NEW into a dictionary."""
    remap: dict[str, str] = {}
    if not raw.strip():
        return remap

    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(
                f"Invalid priority remap token '{token}'. Use format OLD:NEW (e.g. P1:P0)."
            )
        old, new = token.split(":", 1)
        old = old.strip().upper()
        new = new.strip().upper()
        if not old or not new:
            raise ValueError(
                f"Invalid priority remap token '{token}'. Use non-empty OLD:NEW values."
            )
        remap[old] = new

    return remap


def _build_priority_case_sql(remap: dict[str, str], column_expr: str = "priority") -> str:
    if not remap:
        return column_expr

    clauses = " ".join(
        f"WHEN '{old}' THEN '{new}'" for old, new in sorted(remap.items())
    )
    return f"CASE UPPER(COALESCE({column_expr}, '')) {clauses} ELSE {column_expr} END"


def _upsert_unique_table(
    pg_conn,
    target_table: str,
    rows: list[dict],
    remap_priority: dict[str, str],
    statement_timeout_ms: int = 0,
) -> tuple[int, int, int]:
    """Write rows into target unique table.

    Returns tuple: (staging_rows, updated_rows, inserted_rows)
    """
    if not VALID_TABLE_RE.fullmatch(target_table):
        raise ValueError(
            f"Invalid table name '{target_table}'. Use format schema.table or table_name."
        )

    if not rows:
        return 0, 0, 0

    with pg_conn.cursor() as cursor:
        # Session default statement_timeout can be too low for large batches;
        # raise it (or disable it with 0) for just this write transaction.
        cursor.execute("SET LOCAL statement_timeout = %s", (statement_timeout_ms,))

        cursor.execute(
            """
            CREATE TEMP TABLE tmp_unique_critical_info_stage (
                "CODE" double precision,
                "CODE_AUX" bigint,
                "TYPE" text,
                description_pattern text,
                sample_description text
            ) ON COMMIT DROP
            """
        )

        payload = [
            (
                row.get("CODE"),
                row.get("CODE_AUX"),
                row.get("TYPE"),
                row.get("description_pattern"),
                row.get("sample_description"),
            )
            for row in rows
        ]

        execute_values(
            cursor,
            """
            INSERT INTO tmp_unique_critical_info_stage (
                "CODE", "CODE_AUX", "TYPE", description_pattern, sample_description
            ) VALUES %s
            """,
            payload,
            page_size=5000,
        )

        # Fresh temp tables have no planner stats; without this the optimizer
        # assumes a handful of rows and can pick a nested-loop join against
        # tens/hundreds of thousands of staged rows, which is what times out.
        cursor.execute("ANALYZE tmp_unique_critical_info_stage")

        priority_sql = _build_priority_case_sql(remap_priority, "t.priority")

        cursor.execute(
            f"""
            UPDATE {target_table} t
            SET
                sample_description = s.sample_description,
                priority = CASE
                    WHEN t."TYPE" = 'ERROR' THEN {priority_sql}
                    ELSE NULL
                END
            FROM tmp_unique_critical_info_stage s
            WHERE t."CODE" = s."CODE"
              AND t."CODE_AUX" = s."CODE_AUX"
              AND t."TYPE" = s."TYPE"
              AND COALESCE(t.description_pattern, '') = COALESCE(s.description_pattern, '')
            """
        )
        updated_rows = cursor.rowcount

        mapped_priority_expr = _build_priority_case_sql(remap_priority, "u.priority")
        cursor.execute(
            f"""
            INSERT INTO {target_table} (
                "CODE",
                "CODE_AUX",
                "TYPE",
                description_pattern,
                sample_description,
                reason,
                priority
            )
            SELECT
                s."CODE",
                s."CODE_AUX",
                s."TYPE",
                s.description_pattern,
                s.sample_description,
                NULL AS reason,
                CASE
                    WHEN s."TYPE" = 'ERROR' THEN {mapped_priority_expr}
                    ELSE NULL
                END AS priority
            FROM tmp_unique_critical_info_stage s
            LEFT JOIN {target_table} u
              ON u."CODE" = s."CODE"
             AND u."CODE_AUX" = s."CODE_AUX"
             AND u."TYPE" = s."TYPE"
             AND COALESCE(u.description_pattern, '') = COALESCE(s.description_pattern, '')
            WHERE u."CODE" IS NULL
            """
        )
        inserted_rows = cursor.rowcount

        # Ensure existing rows in table also move from P1..P5 to P0..P4.
        if remap_priority:
            global_priority_sql = _build_priority_case_sql(remap_priority, "priority")
            cursor.execute(
                f"""
                UPDATE {target_table}
                SET priority = {global_priority_sql}
                WHERE "TYPE" = 'ERROR'
                  AND priority IS NOT NULL
                """
            )

        # Enforce invariant: only ERROR rows can carry priority.
        cursor.execute(
            f"""
            UPDATE {target_table}
            SET priority = NULL
            WHERE "TYPE" <> 'ERROR'
              AND priority IS NOT NULL
            """
        )

        return len(rows), updated_rows, inserted_rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List raw or normalized unique CODE/CODE_AUX/DESCRIPTION combinations from Postgres"
    )
    parser.add_argument("--db-config", default=str(Path(__file__).resolve().parents[1] / "db_credentials.ini"))
    parser.add_argument(
        "--db-section",
        default="IRAVATH_DB",
        help="Postgres section in db_credentials.ini (default: IRAVATH_DB -> atlas_db)",
    )
    parser.add_argument(
        "--env",
        choices=["prod", "production", "staging", "stag"],
        default=None,
        help="Optional env alias to resolve DB section via ENV_TO_DB_SECTION",
    )
    parser.add_argument(
        "--table",
        default="public.criticalinfo_snowflakes_data",
        help="Postgres table to query (default: public.criticalinfo_snowflakes_data)",
    )
    parser.add_argument("--db-host", default=None, help="Optional Postgres host override")
    parser.add_argument("--db-port", type=int, default=None, help="Optional Postgres port override")
    parser.add_argument("--db-name", default=None, help="Optional Postgres database override")
    parser.add_argument("--db-user", default=None, help="Optional Postgres user override")
    parser.add_argument("--db-password", default=None, help="Optional Postgres password override")
    parser.add_argument("--start-date", required=True, help="Inclusive lower bound, format YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Exclusive upper bound, format YYYY-MM-DD")
    parser.add_argument(
        "--version",
        default=None,
        help="Optional substring match for DEVICE_VERSION, e.g. 6.15.rc.1",
    )
    parser.add_argument("--process-name", action="append", default=[], help="Optional PROCESS_NAME filter. Repeatable.")
    parser.add_argument("--tenant-id", action="append", default=[], help="Optional TENANT_ID filter. Repeatable.")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum result rows")
    parser.add_argument(
        "--mode",
        choices=["raw", "normalized"],
        default="normalized",
        help="Group by raw DESCRIPTION or normalized DESCRIPTION pattern",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional CSV output path")
    parser.add_argument(
        "--target-table",
        default="public.unique_critical_info",
        help="Target table for direct writes (default: public.unique_critical_info)",
    )
    parser.add_argument(
        "--write-to-table",
        action="store_true",
        default=True,
        help="Write results directly into target table (default: enabled)",
    )
    parser.add_argument(
        "--no-write-to-table",
        action="store_false",
        dest="write_to_table",
        help="Disable direct table writes (useful when only CSV/console output is needed)",
    )
    parser.add_argument(
        "--priority-remap",
        default="P1:P0,P2:P1,P3:P2,P4:P3,P5:P4",
        help="Priority remap rules OLD:NEW comma-separated (default: P1->P0 ... P5->P4)",
    )
    parser.add_argument(
        "--skip-classification",
        action="store_true",
        help="Skip automatic reason/priority classification step after write",
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=0,
        help="Postgres statement_timeout (ms) for the write transaction; 0 disables it (default: 0)",
    )
    args = parser.parse_args()

    db_section = ENV_TO_DB_SECTION[args.env] if args.env else args.db_section
    try:
        db_params = read_db_config(args.db_config, db_section)
    except Exception as exc:
        print(f"Failed to read DB config section '{db_section}': {exc}")
        return 1

    if args.db_host:
        db_params["host"] = args.db_host
    if args.db_port is not None:
        db_params["port"] = args.db_port
    if args.db_name:
        db_params["database"] = args.db_name
    if args.db_user:
        db_params["user"] = "poll_user"
    if args.db_password:
        db_params["password"] = "admin"

    try:
        priority_remap = _parse_priority_remap(args.priority_remap)
    except ValueError as exc:
        print(f"Invalid --priority-remap: {exc}")
        return 1

    if not priority_remap:
        priority_remap = DEFAULT_PRIORITY_REMAP

    pg_conn = connect_to_db(db_params)
    if not pg_conn:
        print("Failed to connect to Postgres.")
        return 1

    try:
        rows = query_unique_descriptions(
            pg_conn=pg_conn,
            table_name=args.table,
            start_date=args.start_date,
            end_date=args.end_date,
            device_version_substring=args.version,
            process_names=args.process_name,
            tenant_ids=args.tenant_id,
            limit=args.limit,
            normalized=args.mode == "normalized",
        )
        if args.mode == "normalized":
            for row in rows:
                row["description_pattern"] = _to_like_pattern(row.get("description_pattern"))

        if not rows:
            print("No rows returned.")
            return 0

        if args.write_to_table:
            try:
                staged, updated, inserted = _upsert_unique_table(
                    pg_conn=pg_conn,
                    target_table=args.target_table,
                    rows=rows,
                    remap_priority=priority_remap,
                    statement_timeout_ms=args.statement_timeout_ms,
                )

                pg_conn.commit()
            except Exception:
                pg_conn.rollback()
                raise

            print(
                f"Wrote to {args.target_table}: staged={staged}, updated={updated}, inserted={inserted}"
            )

        if args.output:
            _write_csv(args.output, rows)
            print(f"Wrote {len(rows)} rows to {args.output}")
        elif not args.write_to_table:
            for row in rows[:20]:
                print(row)
            if len(rows) > 20:
                print(f"Displayed 20 of {len(rows)} rows")
    finally:
        pg_conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())