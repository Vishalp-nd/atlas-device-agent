"""observations_agent_graph.py — Atlas sub-agent for observations analytics.

This agent reads observations data from ClickHouse and answers analytics
questions using guarded, read-only SQL tools.

Two tables back this agent:
- `observation_data`: one row per recorded file/session.
- `video_metadata`: per-GPS-sample rows, joined to observations on
  (device_id, file_name). This is the flattened form of what used to be the
  `videometadata` JSONB column.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Annotated, TypedDict
from zoneinfo import ZoneInfo


def _setup_logger() -> logging.Logger:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "observations_agent.log"

    logger = logging.getLogger("atlas.observations_agent")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


logger = _setup_logger()

import clickhouse_connect
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from atlas.result_store import result_store, rows_to_csv_bytes

MAX_ITERATIONS = 12
VIDEO_METADATA_TABLE = "video_metadata"

# Per-agent-run download accumulator; populated by tools, consumed by run_observations_agent.
_run_ctx = __import__('threading').local()


def _get_llm() -> ChatAnthropic:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(str(env_path), override=False)
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-5").strip()
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set in .env")
    kwargs = {"api_key": api_key, "model": model}
    if model != "claude-sonnet-5":
        kwargs["temperature"] = 0.0
    return ChatAnthropic(**kwargs)


def _safe_query(sql: str) -> bool:
    text = sql.strip().lower()
    if not text.startswith("select"):
        return False
    blocked = ["insert ", "update ", "delete ", "drop ", "alter ", "create ", "pragma ", ";"]
    return not any(token in text for token in blocked)


def _quote_table_identifier(table_name: str) -> str:
    """Allow only schema-qualified identifiers made of [a-zA-Z0-9_]."""
    cleaned = table_name.strip()
    parts = cleaned.split(".")
    if len(parts) not in (1, 2):
        raise ValueError("Invalid table name. Use <table> or <schema.table>.")

    out: list[str] = []
    for part in parts:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            raise ValueError("Invalid table identifier component.")
        out.append(f'"{part}"')
    return ".".join(out)


def _collect_download(result_id: str, filename: str) -> None:
    """Append a completed download entry to the current run's context."""
    if not hasattr(_run_ctx, "downloads"):
        _run_ctx.downloads = []
    _run_ctx.downloads.append({"id": result_id, "filename": filename})


def _current_ist_payload() -> str:
    current_dt = datetime.now(ZoneInfo("Asia/Kolkata"))
    payload = {
        "timezone": "Asia/Kolkata",
        "timezone_abbreviation": current_dt.tzname(),
        "current_date": current_dt.strftime("%Y-%m-%d"),
        "current_time": current_dt.strftime("%H:%M:%S"),
        "current_datetime": current_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "iso_datetime": current_dt.isoformat(),
        "weekday": current_dt.strftime("%A"),
    }
    return json.dumps(payload, indent=2)


def _make_tools(
    repo_root: Path,
    table_name: str,
    clickhouse_section: str,
    include_db_overview: bool = True,
) -> list:
    db_config_path = repo_root / "db_credentials.ini"
    table_ident = _quote_table_identifier(table_name)
    video_ident = _quote_table_identifier(VIDEO_METADATA_TABLE)

    def _parse_iso_datetime(value: str, field_name: str) -> datetime:
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"Invalid {field_name}: {value!r}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
            ) from exc

    def _normalize_explicit_bound(value: str, field_name: str) -> str:
        raw = value.strip()
        parsed = _parse_iso_datetime(raw, field_name)
        if len(raw) == 10:
            if field_name == "start_dt":
                parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=0)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

    def _connect_ro():
        if not db_config_path.exists():
            raise FileNotFoundError(
                f"DB config not found: {db_config_path}. "
                "Expected db_credentials.ini at repo root."
            )
        parser = configparser.ConfigParser()
        parser.read(db_config_path)
        if not parser.has_section(clickhouse_section):
            raise ValueError(f"Section '{clickhouse_section}' not found in {db_config_path}")
        # db_credentials.ini carries the native port; clickhouse_connect speaks HTTP.
        port = int(parser.get(clickhouse_section, "port", fallback="9000"))
        return clickhouse_connect.get_client(
            host=parser.get(clickhouse_section, "host", fallback="127.0.0.1"),
            port=8123 if port == 9000 else port,
            username=parser.get(clickhouse_section, "user", fallback="default"),
            password=parser.get(clickhouse_section, "password", fallback=""),
            database=parser.get(clickhouse_section, "database", fallback="default"),
        )

    def _build_filter_clause(
        hours: int,
        device_id: str = "",
        ota: str = "",
        start_dt: str = "",
        end_dt: str = "",
        alias: str = "",
    ) -> tuple[str, dict]:
        """Build a ClickHouse WHERE clause plus named parameters.

        start_dt/end_dt (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) take priority over hours.
        `alias` optionally qualifies the columns for joined queries.
        """
        prefix = f"{alias}." if alias else ""
        where: list[str] = []
        params: dict = {}

        if start_dt.strip() or end_dt.strip():
            if start_dt.strip():
                where.append(f"{prefix}start_time >= {{start_dt:DateTime64(3)}}")
                params["start_dt"] = _normalize_explicit_bound(start_dt, "start_dt")
            if end_dt.strip():
                where.append(f"{prefix}start_time <= {{end_dt:DateTime64(3)}}")
                params["end_dt"] = _normalize_explicit_bound(end_dt, "end_dt")
        else:
            safe_hours = max(1, min(hours, 24 * 90))
            where.append(f"{prefix}start_time >= now() - INTERVAL {{hours:UInt32}} HOUR")
            params["hours"] = safe_hours

        if device_id.strip():
            where.append(f"{prefix}device_id = {{device_id:String}}")
            params["device_id"] = device_id.strip()
        if ota.strip():
            where.append(f"{prefix}ota = {{ota:String}}")
            params["ota"] = ota.strip()
        return " AND ".join(where) if where else "1", params

    @tool
    def current_date_time() -> str:
        """Return the current date and time in IST (Asia/Kolkata).

        Use this before resolving relative dates like today, yesterday, last week,
        or this month from the user's request.
        """
        logger.info("[tool:current_date_time] called — timezone=Asia/Kolkata")
        try:
            return _current_ist_payload()
        except Exception as exc:
            logger.error("[tool:current_date_time] failed: %s", exc)
            return f"current_date_time failed: {exc}"

    @tool
    def db_overview(
        hours: int = 24,
        device_id: str = "",
        ota: str = "",
        start_dt: str = "",
        end_dt: str = "",
    ) -> str:
        """Return high-level stats from observations table.

        Time window: provide start_dt/end_dt (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) for an
        explicit range, or hours (default 24) for a rolling window from now.
        Filter by device_id or ota to narrow results.
        """
        logger.info("[tool:db_overview] called — table=%s section=%s", table_name, clickhouse_section)
        client = None
        try:
            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt
            )
            client = _connect_ro()
            row = client.query(
                f"""
                SELECT
                    count() AS total_rows,
                    min(start_time) AS min_start_time,
                    max(start_time) AS max_start_time,
                    countIf(num_frames_out IS NOT NULL) AS rows_with_num_frames_out,
                    uniqExact(device_id) AS devices_count,
                    uniqExact(ota) AS ota_count
                FROM {table_ident}
                WHERE {where_sql}
                """,
                parameters=params,
            ).result_rows[0]

            payload = {
                "backend": "clickhouse",
                "clickhouse_section": clickhouse_section,
                "table_name": table_name,
                "window": {"start_dt": start_dt.strip() or None, "end_dt": end_dt.strip() or None, "hours": hours if not (start_dt.strip() or end_dt.strip()) else None},
                "filters": {
                    "device_id": device_id.strip() or None,
                    "ota": ota.strip() or None,
                },
                "total_rows": row[0],
                "time_range": {
                    "min": row[1].isoformat() if row[1] else None,
                    "max": row[2].isoformat() if row[2] else None,
                },
                "rows_with_num_frames_out": row[3],
                "distinct_devices": row[4],
                "distinct_ota": row[5],
                "note": (
                    "GPS/video sample counts live in the separate "
                    f"{VIDEO_METADATA_TABLE} table; use video_metadata_overview "
                    "or query_observations_with_video for those."
                ),
            }
            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:db_overview] failed: %s", exc)
            return f"db_overview failed: {exc}"
        finally:
            if client is not None:
                client.close()

    @tool
    def table_stats(limit_devices: int = 20) -> str:
        """Return compact table health stats and top active devices."""
        logger.info("[tool:table_stats] called — table=%s", table_name)
        client = None
        try:
            safe_limit = max(1, min(limit_devices, 100))
            client = _connect_ro()

            health = client.query(
                f"""
                SELECT
                    count() AS total_rows,
                    countIf(start_time IS NULL) AS null_start_time,
                    countIf(device_id = '') AS null_device_id,
                    countIf(num_frames_out IS NULL) AS null_num_frames_out
                FROM {table_ident}
                """
            ).result_rows[0]

            top_devices = client.query(
                f"""
                SELECT device_id, count() AS rows_count
                FROM {table_ident}
                WHERE device_id <> ''
                GROUP BY device_id
                ORDER BY rows_count DESC
                LIMIT {{limit:UInt32}}
                """,
                parameters={"limit": safe_limit},
            ).result_rows

            payload = {
                "table_name": table_name,
                "row_health": {
                    "total_rows": health[0],
                    "null_start_time": health[1],
                    "null_device_id": health[2],
                    "null_num_frames_out": health[3],
                },
                "top_devices": top_devices,
            }
            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:table_stats] failed: %s", exc)
            return f"table_stats failed: {exc}"
        finally:
            if client is not None:
                client.close()

    def _run_select(tool_name: str, sql: str, limit: int, sources: list[str]) -> str:
        """Shared read-only SELECT runner for the three query tools."""
        logger.info("[tool:%s] called — limit=%d sql=%s", tool_name, limit, sql)
        if not _safe_query(sql):
            logger.warning("[tool:%s] rejected unsafe SQL: %s", tool_name, sql)
            return "Rejected. Only a single read-only SELECT statement without ';' is allowed."

        client = None
        try:
            client = _connect_ro()
            query_result = client.query(sql)
            columns = list(query_result.column_names)
            rows = query_result.result_rows[: max(1, min(limit, 1000))]
            csv_bytes = rows_to_csv_bytes(columns, rows)
            rid = result_store.put(csv_bytes, "query_results.csv")
            _collect_download(rid, "query_results.csv")
            result = {
                "backend": "clickhouse",
                "sources": sources,
                "columns": columns,
                "rows": rows,
                "returned_rows": len(rows),
                "_download_id": rid,
            }
            return json.dumps(result, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:%s] failed: %s", tool_name, exc)
            return f"{tool_name} failed: {exc}"
        finally:
            if client is not None:
                client.close()

    @tool
    def query_observations(sql: str, limit: int = 200) -> str:
        """Run a read-only SELECT on the per-session observations table only.

        Use this for questions about files/sessions themselves: counts by device or
        OTA, uptime, voltage, processing flags, alert counts, frame counts.

        Requirements:
        - SQL must begin with SELECT, no semicolons
        - ClickHouse SQL syntax
        - Query `observation_data` (one row per recorded file)
        - Use LIMIT in SQL for large scans (or use the limit argument)
        """
        return _run_select("query_observations", sql, limit, [table_name])

    @tool
    def query_video_metadata(sql: str, limit: int = 200) -> str:
        """Run a read-only SELECT on the per-GPS-sample video metadata table only.

        Use this for questions answerable from GPS samples alone: sample counts,
        speed/altitude/bearing/accuracy distributions, GPS validity, lat/long.

        Columns: file_name, device_id, start_time, end_time, seq_no, valid,
        altitude, bearing, accuracy, lat, long, speed, raw_timestamp,
        altitudeMSL, timestamp.

        Requirements:
        - SQL must begin with SELECT, no semicolons
        - ClickHouse SQL syntax
        - Query `video_metadata`; it has no `ota` column, so filter OTA via
          query_observations_with_video instead
        - Use LIMIT in SQL for large scans (or use the limit argument)
        """
        return _run_select("query_video_metadata", sql, limit, [VIDEO_METADATA_TABLE])

    @tool
    def query_observations_with_video(sql: str, limit: int = 200) -> str:
        """Run a read-only SELECT joining observations to their GPS samples.

        Use this only when a question needs both sides, for example GPS sample
        counts per OTA, or speed stats filtered by device attributes.

        Join the two tables on (device_id, file_name); `video_metadata` averages
        roughly 60 rows per observation row, so aggregate the video side in a
        subquery before joining rather than joining raw and grouping afterwards.

        `observation_data` contains duplicate (device_id, file_name) rows, so
        de-duplicate the observations side before joining or GPS totals will be
        inflated:

            SELECT o.ota, sum(v.pts) AS gps_points
            FROM (
                SELECT DISTINCT device_id, file_name, ota
                FROM observation_data
            ) AS o
            LEFT JOIN (
                SELECT device_id, file_name, count() AS pts
                FROM video_metadata
                GROUP BY device_id, file_name
            ) AS v USING (device_id, file_name)
            GROUP BY o.ota

        Requirements:
        - SQL must begin with SELECT, no semicolons
        - ClickHouse SQL syntax
        - Use LIMIT in SQL for large scans (or use the limit argument)
        """
        return _run_select(
            "query_observations_with_video", sql, limit, [table_name, VIDEO_METADATA_TABLE]
        )

    @tool
    def video_metadata_overview(
        hours: int = 24,
        device_id: str = "",
        ota: str = "",
        start_dt: str = "",
        end_dt: str = "",
    ) -> str:
        """Return high-level GPS-sample stats from the video metadata table.

        Reports sample counts, how many observation files have samples, and GPS
        validity/speed ranges for the window. Filters are applied on the
        observations side, so this joins both tables.
        """
        logger.info("[tool:video_metadata_overview] called — hours=%s device=%r ota=%r", hours, device_id, ota)
        client = None
        try:
            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt, alias="o"
            )
            client = _connect_ro()
            # observation_data holds duplicate (device_id, file_name) rows, so the
            # file list is de-duplicated before joining or the GPS totals fan out.
            row = client.query(
                f"""
                SELECT
                    count() AS observation_files,
                    countIf(v.pts > 0) AS files_with_video_metadata,
                    sum(v.pts) AS total_gps_samples,
                    sum(v.valid_pts) AS valid_gps_samples,
                    minIf(v.min_speed, v.pts > 0) AS min_speed,
                    maxIf(v.max_speed, v.pts > 0) AS max_speed
                FROM (
                    SELECT DISTINCT device_id, file_name
                    FROM {table_ident} AS o
                    WHERE {where_sql}
                ) AS f
                LEFT JOIN (
                    SELECT
                        device_id,
                        file_name,
                        count() AS pts,
                        countIf(valid = 1) AS valid_pts,
                        min(speed) AS min_speed,
                        max(speed) AS max_speed
                    FROM {video_ident}
                    GROUP BY device_id, file_name
                ) AS v USING (device_id, file_name)
                """,
                parameters=params,
            ).result_rows[0]

            payload = {
                "backend": "clickhouse",
                "tables": [table_name, VIDEO_METADATA_TABLE],
                "window": {
                    "start_dt": start_dt.strip() or None,
                    "end_dt": end_dt.strip() or None,
                    "hours": hours if not (start_dt.strip() or end_dt.strip()) else None,
                },
                "filters": {"device_id": device_id.strip() or None, "ota": ota.strip() or None},
                "observation_files": row[0],
                "files_with_video_metadata": row[1],
                "total_gps_samples": row[2],
                "valid_gps_samples": row[3],
                "speed_range": {"min": row[4], "max": row[5]},
            }
            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:video_metadata_overview] failed: %s", exc)
            return f"video_metadata_overview failed: {exc}"
        finally:
            if client is not None:
                client.close()

    @tool
    def gps_kpi_summary(
        hours: int = 24,
        device_id: str = "",
        ota: str = "",
        expected_samples_per_file: int = 60,
        start_dt: str = "",
        end_dt: str = "",
        fleet_level: bool = True,
        device_level: bool = False,
        ota_level: bool = False,
        max_groups: int = 200,
    ) -> str:
        """Return GPS quality KPIs (loss %, accuracy buckets, avg accuracy).

        Reads GPS accuracy from the `video_metadata` samples belonging to each
        observation file.

        Time window: provide start_dt/end_dt (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) for an
        explicit range, or hours (default 24) for a rolling window from now.
        Filter by device_id or ota to narrow results.

        Level switches:
        - fleet_level: aggregate all filtered rows into one fleet summary
        - device_level: aggregate per device_id (top max_groups by file_count)
        - ota_level: aggregate per ota (top max_groups by file_count)
        - max_groups: max rows returned in JSON for device/ota levels (capped at 200).
          CSV exports still include all rows for the filtered data.

        Any combination of level switches is supported.
        """
        logger.info(
            "[tool:gps_kpi_summary] called — hours=%d start_dt=%r end_dt=%r fleet=%s device=%s ota=%s",
            hours,
            start_dt,
            end_dt,
            fleet_level,
            device_level,
            ota_level,
        )
        client = None
        try:
            safe_expected = max(1, min(expected_samples_per_file, 600))
            safe_groups = max(1, min(max_groups, 200))
            if not (fleet_level or device_level or ota_level):
                fleet_level = True

            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt
            )
            params = {**params, "expected": safe_expected}

            def _kpi_sql(group_col: str | None) -> str:
                """Build the KPI query; group_col=None aggregates the whole fleet.

                observation_data holds duplicate (device_id, file_name) rows, so files
                are collapsed to one row per key before joining the GPS samples.
                """
                # CTE aggregates are aliased with an f_ prefix so they never shadow the
                # source columns that the filter clause references.
                group_expr = {"device_id": "f.device_id", "ota": "f.f_ota"}.get(group_col or "")
                select_group = f"{group_expr} AS group_key," if group_expr else ""
                group_clause = f"GROUP BY {group_expr}" if group_expr else ""
                order_clause = "ORDER BY file_count DESC, group_key" if group_expr else ""
                group_filter = f"WHERE {group_expr} <> ''" if group_expr else ""
                return f"""
                WITH files AS (
                    SELECT
                        device_id,
                        file_name,
                        any(ota) AS f_ota,
                        min(start_time) AS f_min_start,
                        max(start_time) AS f_max_start
                    FROM {table_ident} AS o
                    WHERE {where_sql}
                    GROUP BY device_id, file_name
                ),
                vagg AS (
                    SELECT
                        device_id,
                        file_name,
                        count() AS pts,
                        countIf(accuracy > 0) AS valid_acc,
                        countIf(accuracy IS NULL OR accuracy <= 0) AS invalid_acc,
                        countIf(accuracy > 0 AND accuracy <= 2.0) AS le_2m,
                        countIf(accuracy > 0 AND accuracy <= 3.5) AS le_3_5m,
                        countIf(accuracy > 0 AND accuracy <= 6.0) AS le_6m,
                        countIf(accuracy > 0 AND accuracy <= 10.0) AS le_10m,
                        countIf(accuracy > 10.0) AS gt_10m,
                        sumIf(accuracy, accuracy > 0) AS acc_sum
                    FROM {video_ident}
                    GROUP BY device_id, file_name
                )
                SELECT
                    {select_group}
                    count() AS file_count,
                    countIf(v.pts > 0) AS files_with_video_metadata,
                    min(f.f_min_start) AS min_start_time,
                    max(f.f_max_start) AS max_start_time,
                    sum(v.pts) AS parsed_sample_rows,
                    sum(v.valid_acc) AS valid_accuracy_count,
                    sum(v.invalid_acc) AS invalid_accuracy_in_samples,
                    sum(v.le_2m) AS le_2m,
                    sum(v.le_3_5m) AS le_3_5m,
                    sum(v.le_6m) AS le_6m,
                    sum(v.le_10m) AS le_10m,
                    sum(v.gt_10m) AS gt_10m,
                    sum(v.acc_sum) / nullIf(sum(v.valid_acc), 0) AS avg_accuracy_m,
                    toInt64(count() * {{expected:UInt32}}) AS expected_accuracy_count,
                    greatest(toInt64(count() * {{expected:UInt32}}) - toInt64(sum(v.valid_acc)), 0) AS invalid_or_missing_accuracy_count,
                    (greatest(toInt64(count() * {{expected:UInt32}}) - toInt64(sum(v.valid_acc)), 0) * 100.0)
                        / nullIf(toInt64(count() * {{expected:UInt32}}), 0) AS gps_loss_percent
                FROM files AS f
                LEFT JOIN vagg AS v USING (device_id, file_name)
                {group_filter}
                {group_clause}
                {order_clause}
                """

            def _kpi_record(row: tuple, offset: int) -> dict:
                """Map one result row to the KPI payload shape.

                offset is 1 when the row carries a leading group key column.
                """
                return {
                    "file_count": row[offset],
                    "files_with_video_metadata": row[offset + 1],
                    "time_range": {
                        "min": row[offset + 2].isoformat() if row[offset + 2] else None,
                        "max": row[offset + 3].isoformat() if row[offset + 3] else None,
                    },
                    "parsed_sample_rows": row[offset + 4],
                    "valid_accuracy_count": row[offset + 5],
                    "invalid_accuracy_in_samples": row[offset + 6],
                    "expected_accuracy_count": row[offset + 13],
                    "invalid_or_missing_accuracy_count": row[offset + 14],
                    "gps_loss_percent": float(row[offset + 15]) if row[offset + 15] is not None else None,
                    "accuracy_buckets_cumulative": {
                        "le_2m": row[offset + 7],
                        "le_3_5m": row[offset + 8],
                        "le_6m": row[offset + 9],
                        "le_10m": row[offset + 10],
                        "gt_10m": row[offset + 11],
                    },
                    "avg_accuracy_m": float(row[offset + 12]) if row[offset + 12] is not None else None,
                }

            def _csv_row(record: dict, group_value: str | None) -> tuple:
                buckets = record["accuracy_buckets_cumulative"]
                leading = (group_value,) if group_value is not None else ()
                return leading + (
                    record["file_count"],
                    record["files_with_video_metadata"],
                    record["time_range"]["min"],
                    record["time_range"]["max"],
                    record["expected_accuracy_count"],
                    record["valid_accuracy_count"],
                    record["invalid_or_missing_accuracy_count"],
                    record["gps_loss_percent"],
                    record["avg_accuracy_m"],
                    buckets["le_2m"],
                    buckets["le_3_5m"],
                    buckets["le_6m"],
                    buckets["le_10m"],
                    buckets["gt_10m"],
                )

            base_csv_cols = [
                "file_count",
                "files_with_video_metadata",
                "min_start_time",
                "max_start_time",
                "expected_accuracy_count",
                "valid_accuracy_count",
                "invalid_or_missing_accuracy_count",
                "gps_loss_percent",
                "avg_accuracy_m",
                "le_2m",
                "le_3_5m",
                "le_6m",
                "le_10m",
                "gt_10m",
            ]

            client = _connect_ro()
            payload = {
                "backend": "clickhouse",
                "tables": [table_name, VIDEO_METADATA_TABLE],
                "clickhouse_section": clickhouse_section,
                "window": {
                    "start_dt": start_dt.strip() or None,
                    "end_dt": end_dt.strip() or None,
                    "hours": hours if not (start_dt.strip() or end_dt.strip()) else None,
                },
                "filters": {
                    "device_id": device_id.strip() or None,
                    "ota": ota.strip() or None,
                },
                "expected_samples_per_file": safe_expected,
                "levels_requested": {
                    "fleet_level": fleet_level,
                    "device_level": device_level,
                    "ota_level": ota_level,
                },
                "max_groups": safe_groups,
                "coverage_notes": [
                    "gps_loss_percent uses (invalid_or_missing_accuracy_count * 100) / expected_accuracy_count",
                    "expected_accuracy_count defaults to file_count * expected_samples_per_file",
                    "accuracy is read from the video_metadata GPS samples joined on (device_id, file_name)",
                    "duplicate observation rows are collapsed to one row per (device_id, file_name)",
                    "device_level/ota_level JSON rows are capped by max_groups (<=200); CSV contains all matching rows",
                ],
            }

            if fleet_level:
                row = client.query(_kpi_sql(None), parameters=params).result_rows[0]
                record = _kpi_record(row, 0)
                payload.update(record)
                csv_bytes = rows_to_csv_bytes(base_csv_cols, [_csv_row(record, None)])
                rid = result_store.put(csv_bytes, "gps_kpi_summary.csv")
                _collect_download(rid, "gps_kpi_summary.csv")
                payload["_download_id"] = rid

            for enabled, group_col, key_name, level_name, filename in (
                (device_level, "device_id", "device_id", "device_level", "gps_kpi_by_device.csv"),
                (ota_level, "ota", "ota", "ota_level", "gps_kpi_by_ota.csv"),
            ):
                if not enabled:
                    continue
                rows = client.query(_kpi_sql(group_col), parameters=params).result_rows
                records = [(r[0], _kpi_record(r, 1)) for r in rows]
                payload[level_name] = {
                    "total_groups": len(records),
                    "returned_groups": min(len(records), safe_groups),
                    "truncated_for_llm": len(records) > safe_groups,
                    "rows": [{key_name: key, **rec} for key, rec in records[:safe_groups]],
                }
                csv_rows = [_csv_row(rec, key) for key, rec in records]
                rid = result_store.put(
                    rows_to_csv_bytes([key_name, *base_csv_cols], csv_rows), filename
                )
                _collect_download(rid, filename)

            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:gps_kpi_summary] failed: %s", exc)
            return f"gps_kpi_summary failed: {exc}"
        finally:
            if client is not None:
                client.close()

    @tool
    def video_loss_summary(
        hours: int = 24,
        device_id: str = "",
        ota: str = "",
        expected_frames_per_file: int = 60,
        start_dt: str = "",
        end_dt: str = "",
    ) -> str:
        """Return video-loss KPIs (loss %, missing frames, per-session hotspots).

        Observed frame counts come from `num_frames_out` where it is numeric, and
        otherwise fall back to the number of `video_metadata` samples for the file.

        Time window: provide start_dt/end_dt (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) for an
        explicit range, or hours (default 24) for a rolling window from now.
        Filter by device_id or ota to narrow results.
        """
        logger.info("[tool:video_loss_summary] called — hours=%d start_dt=%r end_dt=%r", hours, start_dt, end_dt)
        client = None
        try:
            safe_expected = max(1, min(expected_frames_per_file, 600))
            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt
            )
            params = {**params, "expected": safe_expected}

            # Duplicate observation rows are collapsed so frame totals are per file.
            # CTE aggregates use an f_ prefix so they never shadow the source columns
            # that the filter clause references.
            per_file_cte = f"""
                WITH files AS (
                    SELECT
                        device_id,
                        file_name,
                        any(s3_path) AS f_s3_path,
                        min(start_time) AS f_start_time,
                        any(num_frames_out) AS f_num_frames_out
                    FROM {table_ident} AS o
                    WHERE {where_sql}
                    GROUP BY device_id, file_name
                ),
                vagg AS (
                    SELECT device_id, file_name, count() AS pts
                    FROM {video_ident}
                    GROUP BY device_id, file_name
                ),
                per_file AS (
                    SELECT
                        f.device_id AS device_id,
                        f.f_s3_path AS s3_path,
                        f.f_start_time AS start_time,
                        coalesce(
                            if(match(f.f_num_frames_out, '^[0-9]+$'), toInt64OrNull(f.f_num_frames_out), NULL),
                            CAST(nullIf(v.pts, 0) AS Nullable(Int64)),
                            toInt64(0)
                        ) AS observed_frames,
                        if(
                            (f.f_num_frames_out IS NULL OR NOT match(f.f_num_frames_out, '^[0-9]+$'))
                            AND coalesce(v.pts, 0) = 0,
                            1, 0
                        ) AS missing_frame_signal
                    FROM files AS f
                    LEFT JOIN vagg AS v USING (device_id, file_name)
                )
            """

            client = _connect_ro()
            summary_row = client.query(
                f"""
                {per_file_cte}
                SELECT
                    count() AS file_count,
                    min(start_time) AS min_start_time,
                    max(start_time) AS max_start_time,
                    sum(observed_frames) AS observed_frames_total,
                    toInt64(count() * {{expected:UInt32}}) AS expected_frames_total,
                    greatest(toInt64(count() * {{expected:UInt32}}) - toInt64(sum(observed_frames)), 0) AS missing_frames_total,
                    sum(missing_frame_signal) AS rows_missing_frame_signal,
                    (greatest(toInt64(count() * {{expected:UInt32}}) - toInt64(sum(observed_frames)), 0) * 100.0)
                        / nullIf(toInt64(count() * {{expected:UInt32}}), 0) AS video_loss_percent
                FROM per_file
                """,
                parameters=params,
            ).result_rows[0]

            top_hotspots = client.query(
                f"""
                {per_file_cte}
                SELECT
                    device_id,
                    s3_path,
                    start_time,
                    observed_frames,
                    greatest(toInt64({{expected:UInt32}}) - observed_frames, 0) AS missing_frames,
                    (greatest(toInt64({{expected:UInt32}}) - observed_frames, 0) * 100.0) / {{expected:UInt32}} AS missing_percent
                FROM per_file
                ORDER BY missing_frames DESC, start_time DESC
                LIMIT 10
                """,
                parameters=params,
            ).result_rows

            payload = {
                "backend": "clickhouse",
                "tables": [table_name, VIDEO_METADATA_TABLE],
                "clickhouse_section": clickhouse_section,
                "window": {
                    "start_dt": start_dt.strip() or None,
                    "end_dt": end_dt.strip() or None,
                    "hours": hours if not (start_dt.strip() or end_dt.strip()) else None,
                },
                "filters": {
                    "device_id": device_id.strip() or None,
                    "ota": ota.strip() or None,
                },
                "time_range": {
                    "min": summary_row[1].isoformat() if summary_row[1] else None,
                    "max": summary_row[2].isoformat() if summary_row[2] else None,
                },
                "file_count": summary_row[0],
                "expected_frames_per_file": safe_expected,
                "observed_frames_total": summary_row[3],
                "expected_frames_total": summary_row[4],
                "missing_frames_total": summary_row[5],
                "rows_missing_frame_signal": summary_row[6],
                "video_loss_percent": float(summary_row[7]) if summary_row[7] is not None else None,
                "top_loss_hotspots": top_hotspots,
                "coverage_notes": [
                    "observed_frames uses num_frames_out first, then falls back to the video_metadata sample count",
                    "video_loss_percent uses (missing_frames_total * 100) / expected_frames_total",
                    "duplicate observation rows are collapsed to one row per (device_id, file_name)",
                ],
            }
            hotspot_cols = ["device_id", "s3_path", "start_time",
                            "observed_frames", "missing_frames", "missing_percent"]
            csv_bytes = rows_to_csv_bytes(hotspot_cols, top_hotspots)
            rid = result_store.put(csv_bytes, "video_loss_hotspots.csv")
            _collect_download(rid, "video_loss_hotspots.csv")
            payload["_download_id"] = rid
            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:video_loss_summary] failed: %s", exc)
            return f"video_loss_summary failed: {exc}"
        finally:
            if client is not None:
                client.close()

    @tool
    def session_health_summary(
        hours: int = 24,
        device_id: str = "",
        ota: str = "",
        top_n_devices: int = 20,
        start_dt: str = "",
        end_dt: str = "",
    ) -> str:
        """Return per-device aggregated fleet health for a time window.

        Use for broad period summaries: file counts, ignition, frame coverage,
        metadata presence — all aggregated in SQL, no raw rows returned.

        Time window: provide start_dt/end_dt (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) for an
        explicit range, or hours (default 24) for a rolling window from now.
        Filter by device_id or ota to narrow results.
        """
        logger.info("[tool:session_health_summary] called — hours=%d start_dt=%r end_dt=%r", hours, start_dt, end_dt)
        client = None
        try:
            safe_n = max(1, min(top_n_devices, 100))
            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt
            )
            params = {**params, "limit": safe_n}

            # Duplicate observation rows are collapsed so counts are per file.
            # CTE aggregates use an f_ prefix so they never shadow the source columns
            # that the filter clause references.
            files_cte = f"""
                WITH files AS (
                    SELECT
                        device_id,
                        file_name,
                        any(ota) AS f_ota,
                        min(start_time) AS f_start_time,
                        any(ignition_status) AS f_ignition_status,
                        any(num_frames_out) AS f_num_frames_out,
                        any(metadatastatus) AS f_metadatastatus
                    FROM {table_ident} AS o
                    WHERE {where_sql}
                    GROUP BY device_id, file_name
                ),
                vagg AS (
                    SELECT device_id, file_name, count() AS pts
                    FROM {video_ident}
                    GROUP BY device_id, file_name
                ),
                joined AS (
                    SELECT f.*, coalesce(v.pts, 0) AS pts
                    FROM files AS f
                    LEFT JOIN vagg AS v USING (device_id, file_name)
                )
            """
            frames_expr = "if(match(f_num_frames_out, '^[0-9]+$'), toInt64OrNull(f_num_frames_out), NULL)"

            client = _connect_ro()
            fleet = client.query(
                f"""
                {files_cte}
                SELECT
                    count() AS total_files,
                    uniqExact(device_id) AS distinct_devices,
                    uniqExact(f_ota) AS distinct_ota_versions,
                    min(f_start_time) AS earliest_start,
                    max(f_start_time) AS latest_start,
                    countIf(f_ignition_status = 1) AS ignition_on_files,
                    countIf(f_ignition_status IS NULL) AS null_ignition_files,
                    countIf(pts > 0) AS files_with_video_metadata,
                    countIf(match(f_num_frames_out, '^[0-9]+$')) AS files_with_frame_count,
                    round(avg(coalesce({frames_expr}, 0)), 1) AS avg_frames_per_file,
                    countIf(f_metadatastatus = 'full') AS full_metadata_files,
                    countIf(f_metadatastatus = '') AS null_metadatastatus_files
                FROM joined
                """,
                parameters=params,
            ).result_rows[0]

            per_device = client.query(
                f"""
                {files_cte}
                SELECT
                    device_id,
                    f_ota AS ota,
                    count() AS file_count,
                    min(f_start_time) AS first_session,
                    max(f_start_time) AS last_session,
                    countIf(f_ignition_status = 1) AS ignition_on,
                    countIf(pts > 0) AS has_video_metadata,
                    countIf(match(f_num_frames_out, '^[0-9]+$')) AS has_frame_count,
                    round(avg(coalesce({frames_expr}, 0)), 1) AS avg_frames,
                    countIf(f_metadatastatus = 'full') AS full_metadata
                FROM joined
                WHERE device_id <> ''
                GROUP BY device_id, f_ota
                ORDER BY file_count DESC
                LIMIT {{limit:UInt32}}
                """,
                parameters=params,
            ).result_rows

            payload = {
                "backend": "clickhouse",
                "tables": [table_name, VIDEO_METADATA_TABLE],
                "table_name": table_name,
                "window": {
                    "start_dt": start_dt.strip() or None,
                    "end_dt": end_dt.strip() or None,
                    "hours": hours if not (start_dt.strip() or end_dt.strip()) else None,
                },
                "filters": {
                    "device_id": device_id.strip() or None,
                    "ota": ota.strip() or None,
                },
                "fleet_summary": {
                    "total_files": fleet[0],
                    "distinct_devices": fleet[1],
                    "distinct_ota_versions": fleet[2],
                    "time_range": {
                        "earliest_start": fleet[3].isoformat() if fleet[3] else None,
                        "latest_start": fleet[4].isoformat() if fleet[4] else None,
                    },
                    "ignition_on_files": fleet[5],
                    "null_ignition_files": fleet[6],
                    "files_with_video_metadata": fleet[7],
                    "files_with_frame_count": fleet[8],
                    "avg_frames_per_file": float(fleet[9]) if fleet[9] is not None else None,
                    "full_metadata_files": fleet[10],
                    "null_metadatastatus_files": fleet[11],
                },
                "top_devices": [
                    {
                        "device_id": r[0],
                        "ota": r[1],
                        "file_count": r[2],
                        "first_session": r[3].isoformat() if r[3] else None,
                        "last_session": r[4].isoformat() if r[4] else None,
                        "ignition_on": r[5],
                        "has_video_metadata": r[6],
                        "has_frame_count": r[7],
                        "avg_frames": float(r[8]) if r[8] is not None else None,
                        "full_metadata": r[9],
                    }
                    for r in per_device
                ],
                "note": "All aggregation done in SQL. No raw rows returned regardless of table size.",
            }
            device_cols = ["device_id", "ota", "file_count", "first_session", "last_session",
                           "ignition_on", "has_video_metadata", "has_frame_count", "avg_frames", "full_metadata"]
            device_rows = [
                (r["device_id"], r["ota"], r["file_count"], r["first_session"], r["last_session"],
                 r["ignition_on"], r["has_video_metadata"], r["has_frame_count"],
                 r["avg_frames"], r["full_metadata"])
                for r in payload["top_devices"]
            ]
            csv_bytes = rows_to_csv_bytes(device_cols, device_rows)
            rid = result_store.put(csv_bytes, "session_health_devices.csv")
            _collect_download(rid, "session_health_devices.csv")
            payload["_download_id"] = rid
            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:session_health_summary] failed: %s", exc)
            return f"session_health_summary failed: {exc}"
        finally:
            if client is not None:
                client.close()

    tools = [
        current_date_time,
        query_observations,
        query_video_metadata,
        query_observations_with_video,
        video_metadata_overview,
        gps_kpi_summary,
        video_loss_summary,
        table_stats,
        session_health_summary,
    ]
    if include_db_overview:
        tools.insert(0, db_overview)
    return tools


class ObservationsAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: int


def _message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = str(item.get("text", "")).strip()
            else:
                text = str(getattr(item, "text", "")).strip()
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()


def build_observations_graph(
    repo_root: Path,
    table_name: str = "observation_data",
    clickhouse_section: str = "CLICKHOUSE_DB",
    include_db_overview: bool = True,
):
    tools = _make_tools(repo_root, table_name, clickhouse_section, include_db_overview=include_db_overview)
    llm = _get_llm().bind_tools(tools)
    tool_node = ToolNode(tools)

    def call_llm(state: ObservationsAgentState) -> dict:
        response = llm.invoke(state["messages"])
        return {"messages": [response], "iterations": state["iterations"] + 1}

    def route(state: ObservationsAgentState) -> str:
        if state["iterations"] >= MAX_ITERATIONS:
            return END
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph = StateGraph(ObservationsAgentState)
    graph.add_node("llm", call_llm)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "llm")
    return graph.compile()


def run_observations_agent(
    query: str,
    system_prompt: str,
    repo_root: Path,
    table_name: str = "observation_data",
    clickhouse_section: str = "CLICKHOUSE_DB",
    include_db_overview: bool = True,
    history: list[BaseMessage] | None = None,
) -> tuple[str, list[dict]]:
    """Run the observations agent. Returns (answer_text, downloads) where
    downloads is a list of {id, filename} dicts for CSV results produced during the run."""
    logger.info(
        "[run] observations agent start — table=%s section=%s query_preview=%r",
        table_name,
        clickhouse_section,
        query[:200],
    )
    _run_ctx.downloads = []  # reset per-run download accumulator
    graph = build_observations_graph(
        repo_root,
        table_name,
        clickhouse_section,
        include_db_overview=include_db_overview,
    )
    initial_state: ObservationsAgentState = {
        "messages": [
            SystemMessage(content=system_prompt),
            *(history or []),
            HumanMessage(content=query),
        ],
        "iterations": 0,
    }
    final_state = graph.invoke(initial_state)
    last = final_state["messages"][-1]
    result = _message_text(last) or "No response."

    # Tool execution may occur on worker threads, so don't rely only on thread-local
    # download collection. Also extract IDs from tool JSON outputs in final messages.
    collected: dict[str, dict] = {
        d.get("id", ""): d for d in getattr(_run_ctx, "downloads", []) if d.get("id")
    }
    for msg in final_state.get("messages", []):
        content = getattr(msg, "content", None)
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue

        payload_downloads = payload.get("downloads")
        if isinstance(payload_downloads, list):
            for item in payload_downloads:
                if not isinstance(item, dict):
                    continue
                rid = item.get("id")
                if not rid or rid in collected:
                    continue
                filename = item.get("filename")
                if not filename:
                    entry = result_store.get(rid)
                    filename = entry[1] if entry else "download"
                collected[rid] = {"id": rid, "filename": filename}

        rid = payload.get("_download_id")
        if not rid or rid in collected:
            continue
        entry = result_store.get(rid)
        filename = entry[1] if entry else "result.csv"
        collected[rid] = {"id": rid, "filename": filename}

    downloads = list(collected.values())
    logger.info(
        "[run] observations agent finished — iterations=%d result_length=%d downloads=%d",
        final_state["iterations"],
        len(result),
        len(downloads),
    )
    return result, downloads
