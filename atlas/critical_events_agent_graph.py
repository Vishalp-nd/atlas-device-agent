"""
critical_events_agent_graph.py — Atlas sub-agent for local critical-event analytics.

This agent reads from a local PostgreSQL table populated by
pipeline/critical_events_pipeline.py and combines query results
with SKILL.md knowledge for richer insights.

Expected operating pattern:
- inspect the DB first to identify the relevant process, code, type split, or trend
- if the query or DB results point to a service/process, prefer reading the matching
    `*-critical-errors` skill when available
- also use `critical-event-code-analysis` when the user needs code/CODE_AUX/
    DESCRIPTION interpretation
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Annotated, TypedDict
from zoneinfo import ZoneInfo


def _setup_logger() -> logging.Logger:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "critical_events_agent.log"

    logger = logging.getLogger("atlas.critical_events_agent")
    if logger.handlers:
        return logger  # already configured (e.g. on Streamlit rerun)

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

from fetch_device_config import connect_to_snowflake, read_db_config
from atlas.result_store import result_store
from staging_critical_info_report import generate_reports

MAX_ITERATIONS = 12
SNOWFLAKE_STAGING_SECTION = "SNOWFLAKE_STAG_DB"
SNOWFLAKE_STAGING_TABLE = "STAGE_IDMS_MAIN_DB.PUBLIC.DEVICE_CRITICAL_EVENT"
_run_ctx = threading.local()


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


def _discover_skill_files(skills_root: Path) -> dict[str, Path]:
    """Return map of skill key -> SKILL.md path for nested skills layouts."""
    skills: dict[str, Path] = {}
    if not skills_root.exists():
        return skills
    for skill_md in sorted(skills_root.rglob("SKILL.md")):
        key = skill_md.parent.relative_to(skills_root).as_posix()
        skills[key] = skill_md
    return skills


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


def _collect_download(result_id: str, filename: str) -> None:
    if not hasattr(_run_ctx, "downloads"):
        _run_ctx.downloads = []
    _run_ctx.downloads.append({"id": result_id, "filename": filename})


def _current_ist_payload() -> str:
    from datetime import datetime

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
    postgres_section: str,
    include_db_overview: bool = False,
) -> list:
    skills_root = repo_root / "skills" / "cinfo-skills"
    db_config_path = repo_root / "db_credentials.ini"

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

    def _connect_staging_snowflake():
        if not db_config_path.exists():
            raise FileNotFoundError(
                f"DB config not found: {db_config_path}. "
                "Expected db_credentials.ini at repo root."
            )
        conn = connect_to_snowflake(str(db_config_path), SNOWFLAKE_STAGING_SECTION)
        if conn is None:
            raise ConnectionError(
                f"Failed to connect to Snowflake using section {SNOWFLAKE_STAGING_SECTION}"
            )
        return conn

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
    def db_overview() -> str:
        """Return high-level stats from local PostgreSQL critical-events table.

        Use this first to understand data size, date range, label split, and top codes.
        """
        logger.info("[tool:db_overview] called — table=%s section=%s", table_name, postgres_section)
        try:
            conn = _connect_ro()
            cur = conn.cursor()
            cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            total_rows = cur.fetchone()[0]

            cur.execute(
                f'SELECT MIN("TIMESTAMP"), MAX("TIMESTAMP") FROM "{table_name}"'
            )
            min_ts, max_ts = cur.fetchone()

            cur.execute(
                f'''
                SELECT type, COUNT(*) AS cnt
                FROM "{table_name}"
                GROUP BY type
                ORDER BY cnt DESC
                '''
            )
            label_dist = cur.fetchall()

            cur.execute(
                f'''
                SELECT "CODE", COUNT(*) AS cnt
                FROM "{table_name}"
                GROUP BY "CODE"
                ORDER BY cnt DESC
                LIMIT 10
                '''
            )
            top_codes = cur.fetchall()

            conn.close()
            payload = {
                "postgres_section": postgres_section,
                "table_name": table_name,
                "total_rows": total_rows,
                "time_range": {
                    "min": min_ts.isoformat() if min_ts else None,
                    "max": max_ts.isoformat() if max_ts else None,
                },
                "label_distribution": label_dist,
                "top_codes": top_codes,
            }
            logger.debug("[tool:db_overview] result — total_rows=%s time_range=%s..%s", total_rows, min_ts, max_ts)
            return json.dumps(payload, indent=2)
        except Exception as exc:
            logger.error("[tool:db_overview] failed: %s", exc)
            return f"db_overview failed: {exc}"

    @tool
    def query_critical_events(sql: str, limit: int = 200) -> str:
        """Run a read-only SELECT query on the PostgreSQL critical-events table.

        Requirements:
        - SQL must begin with SELECT
        - No semicolons
        - Use LIMIT in SQL for large queries (or use limit argument)
        """
        logger.info("[tool:query_critical_events] called — limit=%d sql=%s", limit, sql)
        if not _safe_query(sql):
            logger.warning("[tool:query_critical_events] rejected unsafe SQL: %s", sql)
            return "Rejected. Only a single read-only SELECT statement without ';' is allowed."

        try:
            conn = _connect_ro()
            cur = conn.cursor()
            cur.execute(sql)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(max(1, min(limit, 1000)))
            conn.close()
            result = {
                "columns": columns,
                "rows": rows,
                "returned_rows": len(rows),
            }
            logger.debug("[tool:query_critical_events] returned %d rows, columns=%s", len(rows), columns)
            return json.dumps(result, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:query_critical_events] failed: %s", exc)
            return f"query_critical_events failed: {exc}"

    @tool
    def query_staging_critical_events(sql: str, limit: int = 200) -> str:
        """Run a read-only SELECT query on Snowflake staging critical-events data.

        Use this when the user wants staging data or wants to compare staging with production.

        Requirements:
        - SQL must begin with SELECT
        - No semicolons
        - Query STAGE_IDMS_MAIN_DB.PUBLIC.DEVICE_CRITICAL_EVENT
        - Use LIMIT in SQL for large queries (or use limit argument)
        """
        logger.info("[tool:query_staging_critical_events] called — limit=%d sql=%s", limit, sql)
        if not _safe_query(sql):
            logger.warning("[tool:query_staging_critical_events] rejected unsafe SQL: %s", sql)
            return "Rejected. Only a single read-only SELECT statement without ';' is allowed."

        conn = None
        cur = None
        try:
            conn = _connect_staging_snowflake()
            cur = conn.cursor()
            cur.execute(sql)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(max(1, min(limit, 1000)))
            result = {
                "backend": "snowflake_staging",
                "table_name": SNOWFLAKE_STAGING_TABLE,
                "columns": columns,
                "rows": rows,
                "returned_rows": len(rows),
            }
            logger.debug(
                "[tool:query_staging_critical_events] returned %d rows, columns=%s",
                len(rows),
                columns,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as exc:
            logger.error("[tool:query_staging_critical_events] failed: %s", exc)
            return f"query_staging_critical_events failed: {exc}"
        finally:
            if cur is not None:
                cur.close()
            if conn is not None:
                conn.close()

    @tool
    def generate_staging_critical_info_report(
        start_date: str,
        end_date: str,
        deviceid: str = "",
        ota: str = "",
    ) -> str:
        """Generate OTA-specific staging critical-info HTML reports and register them as downloads.

        Required arguments:
        - start_date: inclusive start date in YYYY-MM-DD format
        - end_date: inclusive end date in YYYY-MM-DD format

        Optional arguments:
        - deviceid: comma-separated device IDs; falls back to CINFO_DEVICES from .env when empty
        - ota: comma-separated OTA versions; falls back to CINFO_REPORT from .env when empty
        """
        logger.info(
            "[tool:generate_staging_critical_info_report] called — start_date=%s end_date=%s deviceid=%s ota=%s",
            start_date,
            end_date,
            deviceid,
            ota,
        )
        try:
            ota_versions = [item.strip() for item in ota.split(",") if item.strip()] or None
            device_ids = [item.strip() for item in deviceid.split(",") if item.strip()] or None
            report_paths = generate_reports(
                repo_root,
                start_date=date.fromisoformat(start_date),
                end_date=date.fromisoformat(end_date),
                ota_versions=ota_versions,
                device_ids=device_ids,
            )
            if not report_paths:
                return "No reports were generated. Check the provided filters or CINFO_REPORT in .env."

            lines: list[str] = []
            for path in report_paths:
                result_id = result_store.put_file(path)
                _collect_download(result_id, path.name)
                lines.append(
                    f"Generated {path.name}. Download: /atlas/agents/critical-events/download/{result_id}"
                )
            return "\n".join(lines)
        except Exception as exc:
            logger.error("[tool:generate_staging_critical_info_report] failed: %s", exc)
            return f"generate_staging_critical_info_report failed: {exc}"

    @tool
    def list_skills() -> str:
        """List available SKILL.md names and one-line descriptions for insight mapping."""
        logger.info("[tool:list_skills] called — skills_root=%s", skills_root)
        lines: list[str] = []
        for skill_name, skill_md in _discover_skill_files(skills_root).items():
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            desc = ""
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    m = re.search(
                        r'^description:\s*["\']?(.+?)["\']?\s*$',
                        parts[1],
                        re.MULTILINE,
                    )
                    if m:
                        desc = m.group(1).strip()[:150]
            lines.append(f"- {skill_name}: {desc}" if desc else f"- {skill_name}")
        logger.debug("[tool:list_skills] found %d skills", len(lines))
        return "\n".join(lines) if lines else "No skills found."

    @tool
    def read_skill(skill_name: str) -> str:
        """Read SKILL.md text for one skill to ground explanations with framework context."""
        logger.info("[tool:read_skill] called — skill_name=%s", skill_name)
        skill_files = _discover_skill_files(skills_root)
        skill_path = skill_files.get(skill_name)
        query = skill_name.lower()

        if skill_path is None:
            basename_matches = [
                key for key in skill_files if Path(key).name.lower() == query
            ]
            if len(basename_matches) == 1:
                selected = basename_matches[0]
                logger.debug("[tool:read_skill] basename-matched '%s' -> '%s'", skill_name, selected)
                skill_path = skill_files[selected]
            elif len(basename_matches) > 1:
                logger.warning("[tool:read_skill] ambiguous basename for %s: %s", skill_name, basename_matches)
                return (
                    f"Skill name '{skill_name}' is ambiguous. "
                    f"Matches: {', '.join(sorted(basename_matches))}. "
                    "Use the full name from list_skills."
                )

        if skill_path is None:
            candidates = [
                key for key in skill_files
                if query in key.lower() or query in Path(key).name.lower()
            ]
            if len(candidates) == 1:
                selected = candidates[0]
                logger.debug("[tool:read_skill] fuzzy-matched '%s' -> '%s'", skill_name, selected)
                skill_path = skill_files[selected]
            elif len(candidates) > 1:
                logger.warning("[tool:read_skill] ambiguous fuzzy match for %s: %s", skill_name, candidates)
                return (
                    f"Skill '{skill_name}' matched multiple entries: "
                    f"{', '.join(sorted(candidates))}. "
                    "Use the full name from list_skills."
                )
            else:
                logger.warning("[tool:read_skill] skill not found: %s", skill_name)
                return f"Skill '{skill_name}' not found."
        content = skill_path.read_text(encoding="utf-8", errors="replace")
        logger.debug("[tool:read_skill] read %d chars from %s", len(content), skill_path)
        return content

    tools = [
        current_date_time,
        query_critical_events,
        query_staging_critical_events,
        generate_staging_critical_info_report,
        list_skills,
        read_skill,
    ]
    if include_db_overview:
        tools.insert(0, db_overview)
    return tools


class CriticalEventsAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: int


def build_critical_events_graph(
    repo_root: Path,
    table_name: str = "criticalinfo_snowflakes_data",
    postgres_section: str = "IRAVATH_DB",
    include_db_overview: bool = False,
):
    tools = _make_tools(repo_root, table_name, postgres_section, include_db_overview=include_db_overview)
    llm = _get_llm().bind_tools(tools)
    tool_node = ToolNode(tools)

    def call_llm(state: CriticalEventsAgentState) -> dict:
        iteration = state["iterations"] + 1
        msgs = state["messages"]
        logger.info("[llm] invoking — iteration=%d total_messages=%d", iteration, len(msgs))
        for i, msg in enumerate(msgs):
            role = type(msg).__name__
            content_preview = str(getattr(msg, "content", ""))[:200]
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                logger.debug("[llm] msg[%d] %s — tool_calls=%s", i, role, [tc["name"] for tc in tool_calls])
            else:
                logger.debug("[llm] msg[%d] %s — content_preview=%r", i, role, content_preview)
        response = llm.invoke(msgs)
        resp_tool_calls = getattr(response, "tool_calls", None)
        if resp_tool_calls:
            logger.info("[llm] response — tool_calls=%s", [tc["name"] for tc in resp_tool_calls])
        else:
            logger.info("[llm] response — final text length=%d", len(str(getattr(response, "content", ""))))
        return {"messages": [response], "iterations": iteration}

    def route(state: CriticalEventsAgentState) -> str:
        if state["iterations"] >= MAX_ITERATIONS:
            logger.warning("[route] MAX_ITERATIONS (%d) reached — stopping", MAX_ITERATIONS)
            return END
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            logger.debug("[route] -> tools (iteration=%d)", state["iterations"])
            return "tools"
        logger.debug("[route] -> END (iteration=%d)", state["iterations"])
        return END

    graph = StateGraph(CriticalEventsAgentState)
    graph.add_node("llm", call_llm)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "llm")
    return graph.compile()


def run_critical_events_agent(
    query: str,
    system_prompt: str,
    repo_root: Path,
    table_name: str = "criticalinfo_snowflakes_data",
    postgres_section: str = "IRAVATH_DB",
    include_db_overview: bool = False,
    history: list[BaseMessage] | None = None,
) -> str:
    """Run the critical-events agent and return the final answer string.

    `history` (if given) is inserted between the system prompt and the new
    query, letting callers get multi-turn continuity without going through
    the supervisor's intent classification.
    """
    logger.info(
        "[run] starting agent — table=%s section=%s query_preview=%r",
        table_name,
        postgres_section,
        query[:200],
    )
    logger.debug("[run] system_prompt_preview=%r", system_prompt[:300])
    graph = build_critical_events_graph(
        repo_root,
        table_name,
        postgres_section,
        include_db_overview=include_db_overview,
    )
    initial_state: CriticalEventsAgentState = {
        "messages": [
            SystemMessage(content=system_prompt),
            *(history or []),
            HumanMessage(content=query),
        ],
        "iterations": 0,
    }
    _run_ctx.downloads = []
    try:
        final_state = graph.invoke(initial_state)
        last = final_state["messages"][-1]
        result = _message_text(last) or "No response."

        # Tool execution may occur on worker threads, so don't rely only on thread-local
        # download collection. Also extract IDs from tool outputs in final messages.
        collected: dict[str, dict[str, str]] = {
            d.get("id", ""): d for d in getattr(_run_ctx, "downloads", []) if d.get("id")
        }
        for msg in final_state.get("messages", []):
            content = getattr(msg, "content", None)
            if not isinstance(content, str):
                continue
            text = content.strip()

            for match in re.finditer(r"/atlas/agents/critical-events/download/([0-9a-fA-F]+)", text):
                rid = match.group(1)
                if rid in collected:
                    continue
                entry = result_store.get(rid)
                filename = entry[1] if entry else "download"
                collected[rid] = {"id": rid, "filename": filename}

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
            if rid and rid not in collected:
                entry = result_store.get(rid)
                filename = entry[1] if entry else "download"
                collected[rid] = {"id": rid, "filename": filename}

        downloads = list(collected.values())
        logger.info(
            "[run] agent finished — total_iterations=%d result_length=%d downloads=%d",
            final_state["iterations"],
            len(result),
            len(downloads),
        )
        return result, downloads
    finally:
        if hasattr(_run_ctx, "downloads"):
            delattr(_run_ctx, "downloads")
