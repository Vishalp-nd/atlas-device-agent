"""observations_agent_graph.py — Atlas sub-agent for observations analytics.

This agent reads from PostgreSQL observations data in public.extracteddata and
answers analytics questions using guarded, read-only SQL tools.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Annotated, TypedDict

import pandas as pd


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

import psycopg2
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

PIPELINE_ROOT = Path(__file__).resolve().parents[1] / "pipeline"
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from fetch_device_config import read_db_config
from atlas.result_store import result_store, rows_to_csv_bytes
from atlas.gps_oh_summary_generator import (
    DEFAULT_OUTPUT_ROOT,
    FAMILY_CONFIG,
    _build_output_dir,
)

MAX_ITERATIONS = 12

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


def _make_tools(
    repo_root: Path,
    table_name: str,
    postgres_section: str,
    include_db_overview: bool = True,
) -> list:
    db_config_path = repo_root / "db_credentials.ini"
    table_ident = _quote_table_identifier(table_name)
    summary_output_root = Path(DEFAULT_OUTPUT_ROOT)

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

    def _normalize_summary_window(
        start_dt: str,
        end_dt: str,
        hours: int,
    ) -> tuple[str, str, str]:
        if start_dt.strip() or end_dt.strip():
            if not (start_dt.strip() and end_dt.strip()):
                raise ValueError("Both start_dt and end_dt are required for weekly summary tools.")
            start_value = _parse_iso_datetime(_normalize_explicit_bound(start_dt, "start_dt"), "start_dt")
            end_value = _parse_iso_datetime(_normalize_explicit_bound(end_dt, "end_dt"), "end_dt")
        else:
            safe_hours = max(1, min(hours, 24 * 90))
            end_value = datetime.utcnow()
            start_value = end_value - timedelta(hours=safe_hours)

        if end_value < start_value:
            raise ValueError("end_dt must be greater than or equal to start_dt.")

        return (
            start_value.strftime("%Y-%m-%d"),
            end_value.strftime("%Y-%m-%d"),
            f"{start_value.strftime('%Y-%m-%d')} to {end_value.strftime('%Y-%m-%d')}",
        )

    def _normalize_product_family(product_family: str) -> str:
        family = product_family.strip().lower()
        if not family:
            env_value = os.getenv("PRODUCT_LINES", "")
            family = env_value.split(",", 1)[0].strip().lower()
        if not family:
            raise ValueError("product_family is required.")
        if family not in FAMILY_CONFIG:
            valid = ", ".join(sorted(FAMILY_CONFIG.keys()))
            raise ValueError(f"Invalid product_family: {product_family!r}. Allowed values: {valid}")
        return family

    def _normalize_group_by(group_by: str) -> str:
        normalized = group_by.strip().lower() or "product_family"
        allowed = {"product_family", "device_id", "ota", "product_family+device_id", "product_family+ota"}
        if normalized not in allowed:
            raise ValueError(
                "Invalid group_by. Allowed values: product_family, device_id, ota, "
                "product_family+device_id, product_family+ota"
            )
        return normalized

    def _summary_metadata_suffix(device_id: str, ota: str, group_by: str) -> str:
        parts: list[str] = []
        if device_id.strip():
            parts.append(f"device_{re.sub(r'[^A-Za-z0-9_.-]+', '-', device_id.strip())}")
        if ota.strip():
            parts.append(f"ota_{re.sub(r'[^A-Za-z0-9_.-]+', '-', ota.strip())}")
        normalized_group = group_by.strip().lower()
        if normalized_group and normalized_group != "product_family":
            parts.append(f"group_{normalized_group.replace('+', '_')}")
        return "__".join(parts)

    def _find_summary_artifacts(
        product_family: str,
        start_date: str,
        end_date: str,
        device_id: str,
        ota: str,
        group_by: str,
    ) -> list[Path]:
        output_dir = Path(_build_output_dir(str(summary_output_root), product_family, start_date, end_date))
        if not output_dir.is_dir():
            return []

        suffix = _summary_metadata_suffix(device_id, ota, group_by)
        matches: list[Path] = []
        for path in sorted(output_dir.glob("*.xlsx"), key=lambda item: item.stat().st_mtime, reverse=True):
            if suffix and suffix not in path.stem:
                continue
            matches.append(path)
        return matches

    def _register_summary_downloads(paths: list[Path]) -> list[dict[str, str]]:
        downloads: list[dict[str, str]] = []
        for path in paths:
            rid = result_store.put_file(path)
            _collect_download(rid, path.name)
            downloads.append({"id": rid, "filename": path.name})
        return downloads

    def _parse_csv_list(raw_value: str) -> list[str]:
        return [item.strip() for item in raw_value.split(",") if item.strip()]

    def _load_cached_workbook_frames(path: Path) -> dict[str, pd.DataFrame]:
        return pd.read_excel(path, sheet_name=None)

    def _filter_sheet_frame(
        frame: pd.DataFrame,
        device_ids: list[str],
        columns: list[str],
    ) -> pd.DataFrame:
        filtered = frame.copy()

        if device_ids:
            device_col = next(
                (col for col in filtered.columns if str(col).strip().lower() in {"device id", "device_id", "deviceid"}),
                None,
            )
            if device_col is not None:
                normalized = {item.strip() for item in device_ids}
                filtered = filtered[filtered[device_col].astype(str).isin(normalized)]

        if columns:
            missing = [col for col in columns if col not in filtered.columns]
            if missing:
                raise ValueError(f"Requested columns not found: {', '.join(missing)}")
            filtered = filtered.loc[:, columns]

        return filtered

    def _rename_generated_artifacts(
        output_dir: Path,
        generated_paths: list[Path],
        device_id: str,
        ota: str,
        group_by: str,
    ) -> list[Path]:
        suffix = _summary_metadata_suffix(device_id, ota, group_by)
        if not suffix:
            return generated_paths

        renamed: list[Path] = []
        for path in generated_paths:
            target = output_dir / f"{path.stem}__{suffix}{path.suffix}"
            if target != path:
                path.rename(target)
                renamed.append(target)
            else:
                renamed.append(path)
        return renamed

    def _generate_weekly_summary_files(
        product_family: str,
        start_date: str,
        end_date: str,
        device_id: str,
        ota: str,
        group_by: str,
    ) -> list[Path]:
        output_dir = Path(_build_output_dir(str(summary_output_root), product_family, start_date, end_date))
        output_dir.mkdir(parents=True, exist_ok=True)
        before = {path.resolve() for path in output_dir.glob("*.xlsx")}

        command = [
            sys.executable,
            str(repo_root / "atlas" / "gps_oh_summary_generator.py"),
            "--start",
            start_date,
            "--end",
            end_date,
            "--product-family",
            product_family,
            "--output",
            str(summary_output_root),
        ]
        completed = subprocess.run(command, cwd=repo_root, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(
                "weekly summary generation failed: "
                f"{completed.stderr.strip() or completed.stdout.strip() or 'unknown error'}"
            )

        after = sorted(
            (path.resolve() for path in output_dir.glob("*.xlsx") if path.resolve() not in before),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not after:
            after = _find_summary_artifacts(product_family, start_date, end_date, "", "", "product_family")
        if not after:
            raise FileNotFoundError(f"No weekly summary files were generated in {output_dir}")
        return _rename_generated_artifacts(output_dir, after, device_id, ota, group_by)

    def _connect_ro():
        if not db_config_path.exists():
            raise FileNotFoundError(
                f"DB config not found: {db_config_path}. "
                "Expected db_credentials.ini at repo root."
            )
        params = read_db_config(str(db_config_path), postgres_section)
        conn = psycopg2.connect(**params)
        conn.autocommit = True
        return conn

    def _build_filter_clause(
        hours: int,
        device_id: str = "",
        ota: str = "",
        start_dt: str = "",
        end_dt: str = "",
    ) -> tuple[str, list]:
        """Build WHERE clause. start_dt/end_dt (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) take priority over hours."""
        from datetime import datetime as _dt
        where: list[str] = []
        params: list = []

        if start_dt.strip() or end_dt.strip():
            if start_dt.strip():
                where.append("start_time >= %s")
                params.append(_normalize_explicit_bound(start_dt, "start_dt"))
            if end_dt.strip():
                where.append("start_time <= %s")
                params.append(_normalize_explicit_bound(end_dt, "end_dt"))
        else:
            safe_hours = max(1, min(hours, 24 * 90))
            where.append("start_time >= NOW() - (%s * INTERVAL '1 hour')")
            params.append(safe_hours)

        if device_id.strip():
            where.append("device_id = %s")
            params.append(device_id.strip())
        if ota.strip():
            where.append("ota = %s")
            params.append(ota.strip())
        return " AND ".join(where) if where else "TRUE", params

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
        logger.info("[tool:db_overview] called — table=%s section=%s", table_name, postgres_section)
        try:
            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt
            )
            conn = _connect_ro()
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_rows,
                    MIN(start_time) AS min_start_time,
                    MAX(start_time) AS max_start_time,
                    COUNT(*) FILTER (
                        WHERE jsonb_typeof(videometadata) = 'array'
                        AND jsonb_array_length(videometadata) > 0
                    ) AS rows_with_videometadata,
                    COUNT(*) FILTER (WHERE num_frames_out IS NOT NULL) AS rows_with_num_frames_out,
                    COUNT(DISTINCT device_id) AS devices_count,
                    COUNT(DISTINCT ota) AS ota_count
                FROM {table_ident}
                WHERE {where_sql}
                """,
                params,
            )
            row = cur.fetchone()
            conn.close()

            payload = {
                "postgres_section": postgres_section,
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
                "rows_with_videometadata": row[3],
                "rows_with_num_frames_out": row[4],
                "distinct_devices": row[5],
                "distinct_ota": row[6],
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:
            logger.error("[tool:db_overview] failed: %s", exc)
            return f"db_overview failed: {exc}"

    @tool
    def table_stats(limit_devices: int = 20) -> str:
        """Return compact table health stats and top active devices."""
        logger.info("[tool:table_stats] called — table=%s", table_name)
        try:
            safe_limit = max(1, min(limit_devices, 100))
            conn = _connect_ro()
            cur = conn.cursor()

            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(*) FILTER (WHERE start_time IS NULL) AS null_start_time,
                    COUNT(*) FILTER (WHERE device_id IS NULL OR device_id = '') AS null_device_id,
                    COUNT(*) FILTER (
                        WHERE jsonb_typeof(videometadata) <> 'array'
                        OR jsonb_array_length(videometadata) = 0
                    ) AS empty_or_missing_videometadata,
                    COUNT(*) FILTER (WHERE num_frames_out IS NULL) AS null_num_frames_out
                FROM {table_ident}
                """
            )
            health = cur.fetchone()

            cur.execute(
                f"""
                SELECT device_id, COUNT(*) AS rows_count
                FROM {table_ident}
                WHERE device_id IS NOT NULL AND device_id <> ''
                GROUP BY device_id
                ORDER BY rows_count DESC
                LIMIT %s
                """,
                (safe_limit,),
            )
            top_devices = cur.fetchall()
            conn.close()

            payload = {
                "table_name": table_name,
                "row_health": {
                    "total_rows": health[0],
                    "null_start_time": health[1],
                    "null_device_id": health[2],
                    "empty_or_missing_videometadata": health[3],
                    "null_num_frames_out": health[4],
                },
                "top_devices": top_devices,
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:
            logger.error("[tool:table_stats] failed: %s", exc)
            return f"table_stats failed: {exc}"

    @tool
    def query_observations(sql: str, limit: int = 200) -> str:
        """Run a read-only SELECT query on observations data.

        Requirements:
        - SQL must begin with SELECT
        - No semicolons
        - Use LIMIT in SQL for large scans (or use limit argument)
        """
        logger.info("[tool:query_observations] called — limit=%d sql=%s", limit, sql)
        if not _safe_query(sql):
            logger.warning("[tool:query_observations] rejected unsafe SQL: %s", sql)
            return "Rejected. Only a single read-only SELECT statement without ';' is allowed."

        try:
            conn = _connect_ro()
            cur = conn.cursor()
            cur.execute(sql)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(max(1, min(limit, 1000)))
            conn.close()
            csv_bytes = rows_to_csv_bytes(columns, rows)
            rid = result_store.put(csv_bytes, "query_results.csv")
            _collect_download(rid, "query_results.csv")
            result = {
                "columns": columns,
                "rows": rows,
                "returned_rows": len(rows),
                "_download_id": rid,
            }
            return json.dumps(result, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:query_observations] failed: %s", exc)
            return f"query_observations failed: {exc}"

    @tool
    def list_cached_weekly_summaries(
        product_family: str,
        start_dt: str = "",
        end_dt: str = "",
        hours: int = 24 * 7,
        device_id: str = "",
        ota: str = "",
        group_by: str = "product_family",
    ) -> str:
        """List cached OH/GPS weekly Excel summaries for a date window and grouping/filter shape."""
        logger.info(
            "[tool:list_cached_weekly_summaries] called — family=%r start=%r end=%r device=%r ota=%r group_by=%r",
            product_family,
            start_dt,
            end_dt,
            device_id,
            ota,
            group_by,
        )
        try:
            family = _normalize_product_family(product_family)
            normalized_group = _normalize_group_by(group_by)
            start_date, end_date, window_label = _normalize_summary_window(start_dt, end_dt, hours)
            artifacts = _find_summary_artifacts(family, start_date, end_date, device_id, ota, normalized_group)
            downloads = _register_summary_downloads(artifacts)
            payload = {
                "product_family": family,
                "window": {"start_dt": start_date, "end_dt": end_date, "label": window_label},
                "filters": {
                    "device_id": device_id.strip() or None,
                    "ota": ota.strip() or None,
                    "group_by": normalized_group,
                },
                "cache_hit": bool(artifacts),
                "artifact_count": len(artifacts),
                "artifacts": [
                    {
                        "filename": path.name,
                        "path": str(path.relative_to(repo_root)),
                        "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    }
                    for path in artifacts
                ],
                "downloads": downloads,
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:
            logger.error("[tool:list_cached_weekly_summaries] failed: %s", exc)
            return f"list_cached_weekly_summaries failed: {exc}"

    @tool
    def get_or_create_weekly_summary(
        product_family: str,
        start_dt: str = "",
        end_dt: str = "",
        hours: int = 24 * 7,
        device_id: str = "",
        ota: str = "",
        group_by: str = "product_family",
        force_regenerate: bool = False,
    ) -> str:
        """Return cached weekly OH/GPS Excel summaries or generate them and register downloads."""
        logger.info(
            "[tool:get_or_create_weekly_summary] called — family=%r start=%r end=%r force=%s",
            product_family,
            start_dt,
            end_dt,
            force_regenerate,
        )
        try:
            family = _normalize_product_family(product_family)
            normalized_group = _normalize_group_by(group_by)
            start_date, end_date, window_label = _normalize_summary_window(start_dt, end_dt, hours)

            artifacts = [] if force_regenerate else _find_summary_artifacts(
                family,
                start_date,
                end_date,
                device_id,
                ota,
                normalized_group,
            )
            cache_hit = bool(artifacts)
            if not artifacts:
                artifacts = _generate_weekly_summary_files(
                    family,
                    start_date,
                    end_date,
                    device_id,
                    ota,
                    normalized_group,
                )

            downloads = _register_summary_downloads(artifacts)
            payload = {
                "product_family": family,
                "window": {"start_dt": start_date, "end_dt": end_date, "label": window_label},
                "filters": {
                    "device_id": device_id.strip() or None,
                    "ota": ota.strip() or None,
                    "group_by": normalized_group,
                },
                "cache_hit": cache_hit,
                "generated": not cache_hit,
                "artifact_count": len(artifacts),
                "artifacts": [path.name for path in artifacts],
                "downloads": downloads,
                "note": "Weekly OH/GPS summaries remain Excel workbooks; downloads are registered from disk-backed cache.",
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:
            logger.error("[tool:get_or_create_weekly_summary] failed: %s", exc)
            return f"get_or_create_weekly_summary failed: {exc}"

    @tool
    def inspect_cached_summary_schema(
        product_family: str,
        start_dt: str = "",
        end_dt: str = "",
        hours: int = 24 * 7,
        device_id: str = "",
        ota: str = "",
        group_by: str = "product_family",
    ) -> str:
        """Inspect cached weekly workbook sheets and columns before requesting a subset extraction."""
        logger.info(
            "[tool:inspect_cached_summary_schema] called — family=%r start=%r end=%r",
            product_family,
            start_dt,
            end_dt,
        )
        try:
            family = _normalize_product_family(product_family)
            normalized_group = _normalize_group_by(group_by)
            start_date, end_date, window_label = _normalize_summary_window(start_dt, end_dt, hours)
            artifacts = _find_summary_artifacts(family, start_date, end_date, device_id, ota, normalized_group)
            if not artifacts:
                return (
                    "inspect_cached_summary_schema failed: no cached weekly summary found for the requested "
                    "window and filters. Generate or fetch the workbook first."
                )

            workbook = artifacts[0]
            frames = _load_cached_workbook_frames(workbook)
            payload = {
                "product_family": family,
                "window": {"start_dt": start_date, "end_dt": end_date, "label": window_label},
                "source_workbook": workbook.name,
                "sheets": [
                    {
                        "sheet_name": sheet_name,
                        "row_count": int(len(frame.index)),
                        "columns": [str(col) for col in frame.columns],
                    }
                    for sheet_name, frame in frames.items()
                ],
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:
            logger.error("[tool:inspect_cached_summary_schema] failed: %s", exc)
            return f"inspect_cached_summary_schema failed: {exc}"

    @tool
    def extract_cached_summary_subset(
        product_family: str,
        start_dt: str = "",
        end_dt: str = "",
        hours: int = 24 * 7,
        sheet_names: str = "",
        device_ids: str = "",
        columns: str = "",
        ota: str = "",
        group_by: str = "product_family",
    ) -> str:
        """Extract selected sheets, devices, and columns from a cached weekly workbook into CSV downloads."""
        logger.info(
            "[tool:extract_cached_summary_subset] called — family=%r sheets=%r devices=%r columns=%r",
            product_family,
            sheet_names,
            device_ids,
            columns,
        )
        try:
            family = _normalize_product_family(product_family)
            normalized_group = _normalize_group_by(group_by)
            start_date, end_date, window_label = _normalize_summary_window(start_dt, end_dt, hours)
            requested_sheets = _parse_csv_list(sheet_names)
            requested_devices = _parse_csv_list(device_ids)
            requested_columns = _parse_csv_list(columns)

            artifacts = _find_summary_artifacts(
                family,
                start_date,
                end_date,
                requested_devices[0] if len(requested_devices) == 1 else "",
                ota,
                normalized_group,
            )
            if not artifacts:
                return (
                    "extract_cached_summary_subset failed: no cached weekly summary found for the requested "
                    "window and filters. Generate or fetch the workbook first."
                )

            workbook = artifacts[0]
            frames = _load_cached_workbook_frames(workbook)
            selected_sheet_names = requested_sheets or list(frames.keys())
            missing_sheets = [sheet for sheet in selected_sheet_names if sheet not in frames]
            if missing_sheets:
                raise ValueError(f"Requested sheets not found: {', '.join(missing_sheets)}")

            downloads: list[dict[str, str]] = []
            sheet_summaries: list[dict[str, object]] = []
            for sheet_name in selected_sheet_names:
                filtered = _filter_sheet_frame(frames[sheet_name], requested_devices, requested_columns)
                csv_bytes = filtered.to_csv(index=False).encode("utf-8")
                safe_sheet = re.sub(r"[^A-Za-z0-9_.-]+", "_", sheet_name).strip("_") or "sheet"
                filename = f"{workbook.stem}__{safe_sheet}.csv"
                rid = result_store.put(csv_bytes, filename)
                _collect_download(rid, filename)
                downloads.append({"id": rid, "filename": filename})
                sheet_summaries.append(
                    {
                        "sheet_name": sheet_name,
                        "row_count": int(len(filtered.index)),
                        "columns": [str(col) for col in filtered.columns],
                        "download_id": rid,
                        "filename": filename,
                    }
                )

            payload = {
                "product_family": family,
                "window": {"start_dt": start_date, "end_dt": end_date, "label": window_label},
                "source_workbook": workbook.name,
                "filters": {
                    "device_ids": requested_devices or None,
                    "ota": ota.strip() or None,
                    "group_by": normalized_group,
                    "columns": requested_columns or None,
                },
                "sheets": sheet_summaries,
                "downloads": downloads,
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:
            logger.error("[tool:extract_cached_summary_subset] failed: %s", exc)
            return f"extract_cached_summary_subset failed: {exc}"

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
        try:
            safe_expected = max(1, min(expected_samples_per_file, 600))
            safe_groups = max(1, min(max_groups, 200))
            if not (fleet_level or device_level or ota_level):
                fleet_level = True

            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt
            )

            conn = _connect_ro()
            cur = conn.cursor()
            payload = {
                "table_name": table_name,
                "postgres_section": postgres_section,
                "window": {"start_dt": start_dt.strip() or None, "end_dt": end_dt.strip() or None, "hours": hours if not (start_dt.strip() or end_dt.strip()) else None},
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
                    "device_level/ota_level JSON rows are capped by max_groups (<=200); CSV contains all matching rows",
                ],
            }

            if fleet_level:
                cur.execute(
                    f"""
                    WITH filtered AS (
                        SELECT *
                        FROM {table_ident}
                        WHERE {where_sql}
                    ),
                    base AS (
                        SELECT
                            COUNT(*) AS file_count,
                            MIN(start_time) AS min_start_time,
                            MAX(start_time) AS max_start_time,
                            COUNT(*) FILTER (
                                WHERE jsonb_typeof(videometadata) = 'array'
                                AND jsonb_array_length(videometadata) > 0
                            ) AS files_with_videometadata
                        FROM filtered
                    ),
                    samples AS (
                        SELECT
                            CASE
                                WHEN COALESCE(
                                    elem->>'accuracy',
                                    elem->>'gpsAccuracy',
                                    elem->>'gps_accuracy'
                                ) ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN COALESCE(
                                        elem->>'accuracy',
                                        elem->>'gpsAccuracy',
                                        elem->>'gps_accuracy'
                                    )::double precision
                                ELSE NULL
                            END AS acc
                        FROM filtered f
                        CROSS JOIN LATERAL jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(f.videometadata) = 'array' THEN f.videometadata
                                ELSE '[]'::jsonb
                            END
                        ) AS elem
                    ),
                    agg AS (
                        SELECT
                            COUNT(*) AS parsed_sample_rows,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0) AS valid_accuracy_count,
                            COUNT(*) FILTER (WHERE acc IS NULL OR acc <= 0) AS invalid_accuracy_in_samples,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 2.0) AS le_2m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 3.5) AS le_3_5m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 6.0) AS le_6m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 10.0) AS le_10m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 10.0) AS gt_10m,
                            AVG(acc) FILTER (WHERE acc IS NOT NULL AND acc > 0) AS avg_accuracy_m
                        FROM samples
                    )
                    SELECT
                        b.file_count,
                        b.files_with_videometadata,
                        b.min_start_time,
                        b.max_start_time,
                        COALESCE(a.parsed_sample_rows, 0) AS parsed_sample_rows,
                        COALESCE(a.valid_accuracy_count, 0) AS valid_accuracy_count,
                        COALESCE(a.invalid_accuracy_in_samples, 0) AS invalid_accuracy_in_samples,
                        COALESCE(a.le_2m, 0) AS le_2m,
                        COALESCE(a.le_3_5m, 0) AS le_3_5m,
                        COALESCE(a.le_6m, 0) AS le_6m,
                        COALESCE(a.le_10m, 0) AS le_10m,
                        COALESCE(a.gt_10m, 0) AS gt_10m,
                        a.avg_accuracy_m,
                        (b.file_count * %s)::bigint AS expected_accuracy_count,
                        GREATEST((b.file_count * %s)::bigint - COALESCE(a.valid_accuracy_count, 0), 0)::bigint AS invalid_or_missing_accuracy_count,
                        CASE
                            WHEN (b.file_count * %s) > 0
                            THEN (GREATEST((b.file_count * %s)::bigint - COALESCE(a.valid_accuracy_count, 0), 0) * 100.0) / (b.file_count * %s)
                            ELSE NULL
                        END AS gps_loss_percent
                    FROM base b
                    CROSS JOIN agg a
                    """,
                    [*params, safe_expected, safe_expected, safe_expected, safe_expected, safe_expected],
                )
                row = cur.fetchone()

                payload["time_range"] = {
                    "min": row[2].isoformat() if row[2] else None,
                    "max": row[3].isoformat() if row[3] else None,
                }
                payload["file_count"] = row[0]
                payload["files_with_videometadata"] = row[1]
                payload["expected_accuracy_count"] = row[13]
                payload["parsed_sample_rows"] = row[4]
                payload["valid_accuracy_count"] = row[5]
                payload["invalid_accuracy_in_samples"] = row[6]
                payload["invalid_or_missing_accuracy_count"] = row[14]
                payload["gps_loss_percent"] = float(row[15]) if row[15] is not None else None
                payload["accuracy_buckets_cumulative"] = {
                    "le_2m": row[7],
                    "le_3_5m": row[8],
                    "le_6m": row[9],
                    "le_10m": row[10],
                    "gt_10m": row[11],
                }
                payload["avg_accuracy_m"] = float(row[12]) if row[12] is not None else None

                csv_cols = [
                    "file_count",
                    "files_with_videometadata",
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
                csv_row = [
                    payload["file_count"],
                    payload["files_with_videometadata"],
                    payload["expected_accuracy_count"],
                    payload["valid_accuracy_count"],
                    payload["invalid_or_missing_accuracy_count"],
                    payload["gps_loss_percent"],
                    payload["avg_accuracy_m"],
                    payload["accuracy_buckets_cumulative"]["le_2m"],
                    payload["accuracy_buckets_cumulative"]["le_3_5m"],
                    payload["accuracy_buckets_cumulative"]["le_6m"],
                    payload["accuracy_buckets_cumulative"]["le_10m"],
                    payload["accuracy_buckets_cumulative"]["gt_10m"],
                ]
                csv_bytes = rows_to_csv_bytes(csv_cols, [csv_row])
                rid = result_store.put(csv_bytes, "gps_kpi_summary.csv")
                _collect_download(rid, "gps_kpi_summary.csv")
                payload["_download_id"] = rid

            if device_level:
                cur.execute(
                    f"""
                    WITH filtered AS (
                        SELECT *
                        FROM {table_ident}
                        WHERE {where_sql}
                    ),
                    base AS (
                        SELECT
                            device_id,
                            COUNT(*) AS file_count,
                            MIN(start_time) AS min_start_time,
                            MAX(start_time) AS max_start_time,
                            COUNT(*) FILTER (
                                WHERE jsonb_typeof(videometadata) = 'array'
                                AND jsonb_array_length(videometadata) > 0
                            ) AS files_with_videometadata
                        FROM filtered
                        WHERE device_id IS NOT NULL AND device_id <> ''
                        GROUP BY device_id
                    ),
                    samples AS (
                        SELECT
                            f.device_id,
                            CASE
                                WHEN COALESCE(
                                    elem->>'accuracy',
                                    elem->>'gpsAccuracy',
                                    elem->>'gps_accuracy'
                                ) ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN COALESCE(
                                        elem->>'accuracy',
                                        elem->>'gpsAccuracy',
                                        elem->>'gps_accuracy'
                                    )::double precision
                                ELSE NULL
                            END AS acc
                        FROM filtered f
                        CROSS JOIN LATERAL jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(f.videometadata) = 'array' THEN f.videometadata
                                ELSE '[]'::jsonb
                            END
                        ) AS elem
                        WHERE f.device_id IS NOT NULL AND f.device_id <> ''
                    ),
                    agg AS (
                        SELECT
                            device_id,
                            COUNT(*) AS parsed_sample_rows,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0) AS valid_accuracy_count,
                            COUNT(*) FILTER (WHERE acc IS NULL OR acc <= 0) AS invalid_accuracy_in_samples,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 2.0) AS le_2m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 3.5) AS le_3_5m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 6.0) AS le_6m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 10.0) AS le_10m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 10.0) AS gt_10m,
                            AVG(acc) FILTER (WHERE acc IS NOT NULL AND acc > 0) AS avg_accuracy_m
                        FROM samples
                        GROUP BY device_id
                    )
                    SELECT
                        b.device_id,
                        b.file_count,
                        b.files_with_videometadata,
                        b.min_start_time,
                        b.max_start_time,
                        COALESCE(a.parsed_sample_rows, 0) AS parsed_sample_rows,
                        COALESCE(a.valid_accuracy_count, 0) AS valid_accuracy_count,
                        COALESCE(a.invalid_accuracy_in_samples, 0) AS invalid_accuracy_in_samples,
                        COALESCE(a.le_2m, 0) AS le_2m,
                        COALESCE(a.le_3_5m, 0) AS le_3_5m,
                        COALESCE(a.le_6m, 0) AS le_6m,
                        COALESCE(a.le_10m, 0) AS le_10m,
                        COALESCE(a.gt_10m, 0) AS gt_10m,
                        a.avg_accuracy_m,
                        (b.file_count * %s)::bigint AS expected_accuracy_count,
                        GREATEST((b.file_count * %s)::bigint - COALESCE(a.valid_accuracy_count, 0), 0)::bigint AS invalid_or_missing_accuracy_count,
                        CASE
                            WHEN (b.file_count * %s) > 0
                            THEN (GREATEST((b.file_count * %s)::bigint - COALESCE(a.valid_accuracy_count, 0), 0) * 100.0) / (b.file_count * %s)
                            ELSE NULL
                        END AS gps_loss_percent
                    FROM base b
                    LEFT JOIN agg a ON a.device_id = b.device_id
                    ORDER BY b.file_count DESC, b.device_id
                    """,
                    [*params, safe_expected, safe_expected, safe_expected, safe_expected, safe_expected],
                )
                device_rows = cur.fetchall()
                llm_device_rows = device_rows[:safe_groups]

                device_payload = []
                for r in llm_device_rows:
                    device_payload.append(
                        {
                            "device_id": r[0],
                            "file_count": r[1],
                            "files_with_videometadata": r[2],
                            "time_range": {
                                "min": r[3].isoformat() if r[3] else None,
                                "max": r[4].isoformat() if r[4] else None,
                            },
                            "parsed_sample_rows": r[5],
                            "valid_accuracy_count": r[6],
                            "invalid_accuracy_in_samples": r[7],
                            "expected_accuracy_count": r[14],
                            "invalid_or_missing_accuracy_count": r[15],
                            "gps_loss_percent": float(r[16]) if r[16] is not None else None,
                            "accuracy_buckets_cumulative": {
                                "le_2m": r[8],
                                "le_3_5m": r[9],
                                "le_6m": r[10],
                                "le_10m": r[11],
                                "gt_10m": r[12],
                            },
                            "avg_accuracy_m": float(r[13]) if r[13] is not None else None,
                        }
                    )

                payload["device_level"] = {
                    "total_groups": len(device_rows),
                    "returned_groups": len(device_payload),
                    "truncated_for_llm": len(device_rows) > len(device_payload),
                    "rows": device_payload,
                }

                device_cols = [
                    "device_id",
                    "file_count",
                    "files_with_videometadata",
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
                device_csv_rows = [
                    (
                        r[0],
                        r[1],
                        r[2],
                        r[3],
                        r[4],
                        r[14],
                        r[6],
                        r[15],
                        float(r[16]) if r[16] is not None else None,
                        float(r[13]) if r[13] is not None else None,
                        r[8],
                        r[9],
                        r[10],
                        r[11],
                        r[12],
                    )
                    for r in device_rows
                ]
                rid = result_store.put(rows_to_csv_bytes(device_cols, device_csv_rows), "gps_kpi_by_device.csv")
                _collect_download(rid, "gps_kpi_by_device.csv")

            if ota_level:
                cur.execute(
                    f"""
                    WITH filtered AS (
                        SELECT *
                        FROM {table_ident}
                        WHERE {where_sql}
                    ),
                    base AS (
                        SELECT
                            ota,
                            COUNT(*) AS file_count,
                            MIN(start_time) AS min_start_time,
                            MAX(start_time) AS max_start_time,
                            COUNT(*) FILTER (
                                WHERE jsonb_typeof(videometadata) = 'array'
                                AND jsonb_array_length(videometadata) > 0
                            ) AS files_with_videometadata
                        FROM filtered
                        WHERE ota IS NOT NULL AND ota <> ''
                        GROUP BY ota
                    ),
                    samples AS (
                        SELECT
                            f.ota,
                            CASE
                                WHEN COALESCE(
                                    elem->>'accuracy',
                                    elem->>'gpsAccuracy',
                                    elem->>'gps_accuracy'
                                ) ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                    THEN COALESCE(
                                        elem->>'accuracy',
                                        elem->>'gpsAccuracy',
                                        elem->>'gps_accuracy'
                                    )::double precision
                                ELSE NULL
                            END AS acc
                        FROM filtered f
                        CROSS JOIN LATERAL jsonb_array_elements(
                            CASE
                                WHEN jsonb_typeof(f.videometadata) = 'array' THEN f.videometadata
                                ELSE '[]'::jsonb
                            END
                        ) AS elem
                        WHERE f.ota IS NOT NULL AND f.ota <> ''
                    ),
                    agg AS (
                        SELECT
                            ota,
                            COUNT(*) AS parsed_sample_rows,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0) AS valid_accuracy_count,
                            COUNT(*) FILTER (WHERE acc IS NULL OR acc <= 0) AS invalid_accuracy_in_samples,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 2.0) AS le_2m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 3.5) AS le_3_5m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 6.0) AS le_6m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 0 AND acc <= 10.0) AS le_10m,
                            COUNT(*) FILTER (WHERE acc IS NOT NULL AND acc > 10.0) AS gt_10m,
                            AVG(acc) FILTER (WHERE acc IS NOT NULL AND acc > 0) AS avg_accuracy_m
                        FROM samples
                        GROUP BY ota
                    )
                    SELECT
                        b.ota,
                        b.file_count,
                        b.files_with_videometadata,
                        b.min_start_time,
                        b.max_start_time,
                        COALESCE(a.parsed_sample_rows, 0) AS parsed_sample_rows,
                        COALESCE(a.valid_accuracy_count, 0) AS valid_accuracy_count,
                        COALESCE(a.invalid_accuracy_in_samples, 0) AS invalid_accuracy_in_samples,
                        COALESCE(a.le_2m, 0) AS le_2m,
                        COALESCE(a.le_3_5m, 0) AS le_3_5m,
                        COALESCE(a.le_6m, 0) AS le_6m,
                        COALESCE(a.le_10m, 0) AS le_10m,
                        COALESCE(a.gt_10m, 0) AS gt_10m,
                        a.avg_accuracy_m,
                        (b.file_count * %s)::bigint AS expected_accuracy_count,
                        GREATEST((b.file_count * %s)::bigint - COALESCE(a.valid_accuracy_count, 0), 0)::bigint AS invalid_or_missing_accuracy_count,
                        CASE
                            WHEN (b.file_count * %s) > 0
                            THEN (GREATEST((b.file_count * %s)::bigint - COALESCE(a.valid_accuracy_count, 0), 0) * 100.0) / (b.file_count * %s)
                            ELSE NULL
                        END AS gps_loss_percent
                    FROM base b
                    LEFT JOIN agg a ON a.ota = b.ota
                    ORDER BY b.file_count DESC, b.ota
                    """,
                    [*params, safe_expected, safe_expected, safe_expected, safe_expected, safe_expected],
                )
                ota_rows = cur.fetchall()
                llm_ota_rows = ota_rows[:safe_groups]

                ota_payload = []
                for r in llm_ota_rows:
                    ota_payload.append(
                        {
                            "ota": r[0],
                            "file_count": r[1],
                            "files_with_videometadata": r[2],
                            "time_range": {
                                "min": r[3].isoformat() if r[3] else None,
                                "max": r[4].isoformat() if r[4] else None,
                            },
                            "parsed_sample_rows": r[5],
                            "valid_accuracy_count": r[6],
                            "invalid_accuracy_in_samples": r[7],
                            "expected_accuracy_count": r[14],
                            "invalid_or_missing_accuracy_count": r[15],
                            "gps_loss_percent": float(r[16]) if r[16] is not None else None,
                            "accuracy_buckets_cumulative": {
                                "le_2m": r[8],
                                "le_3_5m": r[9],
                                "le_6m": r[10],
                                "le_10m": r[11],
                                "gt_10m": r[12],
                            },
                            "avg_accuracy_m": float(r[13]) if r[13] is not None else None,
                        }
                    )

                payload["ota_level"] = {
                    "total_groups": len(ota_rows),
                    "returned_groups": len(ota_payload),
                    "truncated_for_llm": len(ota_rows) > len(ota_payload),
                    "rows": ota_payload,
                }

                ota_cols = [
                    "ota",
                    "file_count",
                    "files_with_videometadata",
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
                ota_csv_rows = [
                    (
                        r[0],
                        r[1],
                        r[2],
                        r[3],
                        r[4],
                        r[14],
                        r[6],
                        r[15],
                        float(r[16]) if r[16] is not None else None,
                        float(r[13]) if r[13] is not None else None,
                        r[8],
                        r[9],
                        r[10],
                        r[11],
                        r[12],
                    )
                    for r in ota_rows
                ]
                rid = result_store.put(rows_to_csv_bytes(ota_cols, ota_csv_rows), "gps_kpi_by_ota.csv")
                _collect_download(rid, "gps_kpi_by_ota.csv")

            conn.close()
            return json.dumps(payload, indent=2)
        except Exception as exc:
            logger.error("[tool:gps_kpi_summary] failed: %s", exc)
            return f"gps_kpi_summary failed: {exc}"

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

        Time window: provide start_dt/end_dt (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) for an
        explicit range, or hours (default 24) for a rolling window from now.
        Filter by device_id or ota to narrow results.
        """
        logger.info("[tool:video_loss_summary] called — hours=%d start_dt=%r end_dt=%r", hours, start_dt, end_dt)
        try:
            safe_expected = max(1, min(expected_frames_per_file, 600))
            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt
            )

            conn = _connect_ro()
            cur = conn.cursor()

            cur.execute(
                f"""
                WITH filtered AS (
                    SELECT *
                    FROM {table_ident}
                    WHERE {where_sql}
                ),
                per_file AS (
                    SELECT
                        device_id,
                        s3_path,
                        start_time,
                        COALESCE(
                            num_frames_out,
                            CASE
                                WHEN jsonb_typeof(videometadata) = 'array' THEN jsonb_array_length(videometadata)
                                ELSE NULL
                            END,
                            0
                        )::bigint AS observed_frames,
                        CASE
                            WHEN num_frames_out IS NULL
                                 AND (jsonb_typeof(videometadata) <> 'array' OR jsonb_array_length(videometadata) = 0)
                            THEN 1 ELSE 0
                        END AS missing_frame_signal
                    FROM filtered
                ),
                summary AS (
                    SELECT
                        COUNT(*) AS file_count,
                        MIN(start_time) AS min_start_time,
                        MAX(start_time) AS max_start_time,
                        SUM(observed_frames) AS observed_frames_total,
                        SUM(missing_frame_signal) AS rows_missing_frame_signal
                    FROM per_file
                )
                SELECT
                    s.file_count,
                    s.min_start_time,
                    s.max_start_time,
                    s.observed_frames_total,
                    (s.file_count * %s)::bigint AS expected_frames_total,
                    GREATEST((s.file_count * %s)::bigint - s.observed_frames_total, 0)::bigint AS missing_frames_total,
                    s.rows_missing_frame_signal,
                    CASE
                        WHEN (s.file_count * %s) > 0
                        THEN (GREATEST((s.file_count * %s)::bigint - s.observed_frames_total, 0) * 100.0) / (s.file_count * %s)
                        ELSE NULL
                    END AS video_loss_percent
                FROM summary s
                """,
                [*params, safe_expected, safe_expected, safe_expected, safe_expected, safe_expected],
            )
            summary_row = cur.fetchone()

            cur.execute(
                f"""
                WITH filtered AS (
                    SELECT *
                    FROM {table_ident}
                    WHERE {where_sql}
                ),
                per_file AS (
                    SELECT
                        device_id,
                        s3_path,
                        start_time,
                        COALESCE(
                            num_frames_out,
                            CASE
                                WHEN jsonb_typeof(videometadata) = 'array' THEN jsonb_array_length(videometadata)
                                ELSE NULL
                            END,
                            0
                        )::bigint AS observed_frames
                    FROM filtered
                )
                SELECT
                    device_id,
                    s3_path,
                    start_time,
                    observed_frames,
                    GREATEST(%s - observed_frames, 0)::bigint AS missing_frames,
                    CASE
                        WHEN %s > 0 THEN (GREATEST(%s - observed_frames, 0) * 100.0) / %s
                        ELSE NULL
                    END AS missing_percent
                FROM per_file
                ORDER BY missing_frames DESC, start_time DESC
                LIMIT 10
                """,
                [*params, safe_expected, safe_expected, safe_expected, safe_expected],
            )
            top_hotspots = cur.fetchall()
            conn.close()

            payload = {
                "table_name": table_name,
                "postgres_section": postgres_section,
                "window": {"start_dt": start_dt.strip() or None, "end_dt": end_dt.strip() or None, "hours": hours if not (start_dt.strip() or end_dt.strip()) else None},
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
                    "observed_frames uses num_frames_out first, then falls back to videometadata length",
                    "video_loss_percent uses (missing_frames_total * 100) / expected_frames_total",
                ],
            }
            # Store hotspot rows as downloadable CSV
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
        try:
            safe_n = max(1, min(top_n_devices, 100))
            where_sql, params = _build_filter_clause(
                hours=hours, device_id=device_id, ota=ota, start_dt=start_dt, end_dt=end_dt
            )

            conn = _connect_ro()
            cur = conn.cursor()

            # Fleet-level summary
            cur.execute(
                f"""
                SELECT
                    COUNT(*)                                                    AS total_files,
                    COUNT(DISTINCT device_id)                                   AS distinct_devices,
                    COUNT(DISTINCT ota)                                         AS distinct_ota_versions,
                    MIN(start_time)                                             AS earliest_start,
                    MAX(start_time)                                             AS latest_start,
                    COUNT(*) FILTER (WHERE ignition_status = 1)                 AS ignition_on_files,
                    COUNT(*) FILTER (WHERE ignition_status IS NULL)             AS null_ignition_files,
                    COUNT(*) FILTER (
                        WHERE jsonb_typeof(videometadata) = 'array'
                        AND jsonb_array_length(videometadata) > 0
                    )                                                           AS files_with_videometadata,
                    COUNT(*) FILTER (WHERE num_frames_out IS NOT NULL)          AS files_with_frame_count,
                    ROUND(AVG(COALESCE(num_frames_out, 0))::numeric, 1)        AS avg_frames_per_file,
                    COUNT(*) FILTER (WHERE metadatastatus = 'full')             AS full_metadata_files,
                    COUNT(*) FILTER (WHERE metadatastatus IS NULL)              AS null_metadatastatus_files
                FROM {table_ident}
                WHERE {where_sql}
                """,
                params,
            )
            fleet = cur.fetchone()

            # Per-device breakdown (top N by file count)
            cur.execute(
                f"""
                SELECT
                    device_id,
                    ota,
                    COUNT(*)                                                        AS file_count,
                    MIN(start_time)                                                 AS first_session,
                    MAX(start_time)                                                 AS last_session,
                    COUNT(*) FILTER (WHERE ignition_status = 1)                     AS ignition_on,
                    COUNT(*) FILTER (
                        WHERE jsonb_typeof(videometadata) = 'array'
                        AND jsonb_array_length(videometadata) > 0
                    )                                                               AS has_videometadata,
                    COUNT(*) FILTER (WHERE num_frames_out IS NOT NULL)              AS has_frame_count,
                    ROUND(AVG(COALESCE(num_frames_out, 0))::numeric, 1)            AS avg_frames,
                    COUNT(*) FILTER (WHERE metadatastatus = 'full')                 AS full_metadata
                FROM {table_ident}
                WHERE {where_sql}
                  AND device_id IS NOT NULL AND device_id <> ''
                GROUP BY device_id, ota
                ORDER BY file_count DESC
                LIMIT %s
                """,
                [*params, safe_n],
            )
            per_device = cur.fetchall()
            conn.close()

            payload = {
                "table_name": table_name,
                "window": {"start_dt": start_dt.strip() or None, "end_dt": end_dt.strip() or None, "hours": hours if not (start_dt.strip() or end_dt.strip()) else None},
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
                    "files_with_videometadata": fleet[7],
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
                        "has_videometadata": r[6],
                        "has_frame_count": r[7],
                        "avg_frames": float(r[8]) if r[8] is not None else None,
                        "full_metadata": r[9],
                    }
                    for r in per_device
                ],
                "note": "All aggregation done in SQL. No raw rows returned regardless of table size.",
            }
            # Store per-device breakdown as downloadable CSV
            device_cols = ["device_id", "ota", "file_count", "first_session", "last_session",
                           "ignition_on", "has_videometadata", "has_frame_count", "avg_frames", "full_metadata"]
            device_rows = [
                (r["device_id"], r["ota"], r["file_count"], r["first_session"], r["last_session"],
                 r["ignition_on"], r["has_videometadata"], r["has_frame_count"],
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

    tools = [
        query_observations,
        list_cached_weekly_summaries,
        get_or_create_weekly_summary,
        inspect_cached_summary_schema,
        extract_cached_summary_subset,
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
    table_name: str = "public.extracteddata",
    postgres_section: str = "IRAVATH_DB",
    include_db_overview: bool = True,
):
    tools = _make_tools(repo_root, table_name, postgres_section, include_db_overview=include_db_overview)
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
    table_name: str = "public.extracteddata",
    postgres_section: str = "IRAVATH_DB",
    include_db_overview: bool = True,
    history: list[BaseMessage] | None = None,
) -> tuple[str, list[dict]]:
    """Run the observations agent. Returns (answer_text, downloads) where
    downloads is a list of {id, filename} dicts for CSV results produced during the run."""
    logger.info(
        "[run] observations agent start — table=%s section=%s query_preview=%r",
        table_name,
        postgres_section,
        query[:200],
    )
    _run_ctx.downloads = []  # reset per-run download accumulator
    graph = build_observations_graph(
        repo_root,
        table_name,
        postgres_section,
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
