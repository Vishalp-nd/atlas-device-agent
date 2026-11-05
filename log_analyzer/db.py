"""db.py — Postgres access for Log Analyzer runs (atlas_db, schema log_analyzer)."""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from datetime import date, datetime

import psycopg2
import psycopg2.extras

from db_login.db_login import db_connect

from .config import DB_SECTION, SCHEMA_SQL, runs_relative

log = logging.getLogger("log_analyzer.db")

_schema_lock = threading.Lock()
_schema_ready = False

RUN_COLUMNS = (
    "id, mode, environment, ota_version, requested_devices, devices, start_date, end_date, product, options, "
    "status, stage, progress, error, device_errors, pid, requested_by, created_at, started_at, "
    "finished_at, duration_s, output_dir, log_path"
)


_local = threading.local()


def _connection():
    """This thread's connection, reconnecting if it was closed or broken."""
    conn = getattr(_local, "conn", None)
    if conn is None or conn.closed:
        conn = _local.conn = db_connect(DB_SECTION)
    return conn


@contextlib.contextmanager
def connect():
    """A per-thread connection (reused); commits on success, rolls back on error."""
    conn = _connection()
    try:
        yield conn
        conn.commit()
    except psycopg2.OperationalError:
        # Server restarted / connection dropped: forget it so the next call reconnects
        with contextlib.suppress(Exception):
            conn.close()
        _local.conn = None
        raise
    except Exception:
        conn.rollback()
        raise


def ensure_schema() -> None:
    """Create the log_analyzer schema/tables if missing (idempotent, once per process)."""
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        with connect() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_SQL.read_text())
        _schema_ready = True
        log.info("log_analyzer schema ready (%s)", DB_SECTION)


def _json_safe(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        out[key] = value
    return out


def create_run(mode: str, environment: str, ota_version: str | None, requested_devices: list[str],
               start_date: date, end_date: date, product: str | None, options: dict,
               requested_by: str | None) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO log_analyzer.runs
               (mode, environment, ota_version, requested_devices, start_date, end_date, product, options,
                requested_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (mode, environment, ota_version, requested_devices, start_date, end_date, product,
             json.dumps(options), requested_by),
        )
        return cur.fetchone()[0]


_UPDATABLE = {"devices", "status", "stage", "progress", "error", "device_errors", "pid", "started_at",
              "finished_at", "duration_s", "output_dir", "log_path"}
_JSON_FIELDS = {"progress", "device_errors"}


def update_run(run_id: int, **fields) -> None:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"Cannot update run fields {unknown}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = %s" for name in fields)
    values = [json.dumps(v) if name in _JSON_FIELDS else v for name, v in fields.items()]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE log_analyzer.runs SET {assignments} WHERE id = %s", (*values, run_id))


def get_run(run_id: int) -> dict | None:
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {RUN_COLUMNS} FROM log_analyzer.runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
    return _json_safe(dict(row)) if row else None


def list_runs(limit: int = 50, status: str | None = None, environment: str | None = None) -> list[dict]:
    conditions, params = [], []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if environment:
        conditions.append("environment = %s")
        params.append(environment)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {RUN_COLUMNS} FROM log_analyzer.runs {where} ORDER BY id DESC LIMIT %s",
                    (*params, limit))
        return [_json_safe(dict(r)) for r in cur.fetchall()]


def runs_with_status(statuses: tuple[str, ...]) -> list[dict]:
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {RUN_COLUMNS} FROM log_analyzer.runs WHERE status = ANY(%s)", (list(statuses),))
        return [_json_safe(dict(r)) for r in cur.fetchall()]


def add_steps(run_id: int, steps: list[dict]) -> None:
    """Insert timing entries (run_logging.timed format); unknown keys go into detail."""
    if not steps:
        return
    rows = []
    for s in steps:
        detail = {k: v for k, v in s.items()
                  if k not in ("step", "device", "day", "status", "started_at", "duration_s")}
        rows.append((run_id, s["step"], s.get("device"), s.get("day"), s.get("status", "ok"),
                     s.get("started_at"), s["duration_s"], json.dumps(detail, default=str)))
    with connect() as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO log_analyzer.run_steps
               (run_id, step, device, day, status, started_at, duration_s, detail) VALUES %s""",
            rows, page_size=1000,
        )


def step_summary(run_id: int) -> list[dict]:
    """Per step: count, total/avg/max seconds, slowest device."""
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT step, count(*) AS count, round(sum(duration_s)::numeric, 2)::float AS total_s,
                      round(avg(duration_s)::numeric, 2)::float AS avg_s,
                      round(max(duration_s)::numeric, 2)::float AS max_s,
                      (array_agg(device ORDER BY duration_s DESC))[1] AS slowest_device,
                      min(started_at) AS first_started_at,
                      bool_or(status <> 'ok') AS any_failed
               FROM log_analyzer.run_steps WHERE run_id = %s
               GROUP BY step ORDER BY min(started_at) NULLS LAST, step""",
            (run_id,),
        )
        return [_json_safe(dict(r)) for r in cur.fetchall()]


def device_timings(run_id: int) -> list[dict]:
    """Per device: download and analysis seconds."""
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT device,
                      round(sum(duration_s) FILTER (WHERE step = 'download')::numeric, 2)::float AS download_s,
                      round(sum(duration_s) FILTER (WHERE step = 'device_total')::numeric, 2)::float AS analyse_s,
                      count(*) FILTER (WHERE step = 'read_day') AS days
               FROM log_analyzer.run_steps WHERE run_id = %s AND device IS NOT NULL
               GROUP BY device ORDER BY device""",
            (run_id,),
        )
        return [_json_safe(dict(r)) for r in cur.fetchall()]


def save_reports(run_id: int, reports: dict[str, dict]) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM log_analyzer.run_reports WHERE run_id = %s", (run_id,))
        for ota, r in reports.items():
            v = r.get("verdicts", {})
            cur.execute(
                """INSERT INTO log_analyzer.run_reports
                   (run_id, ota_version, devices, boots, pass, fail, not_triggered, config_keys,
                    config_keys_failing, config_error, report_path, summary)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (run_id, ota, r["devices"], r["boots"], v.get("PASS", 0), v.get("FAIL", 0),
                 v.get("NOT_TRIGGERED", 0), r["config_keys"], r["config_keys_failing"],
                 r["config_error"], runs_relative(r["path"]), json.dumps(r.get("summary") or {})),
            )


def get_reports(run_id: int) -> list[dict]:
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM log_analyzer.run_reports WHERE run_id = %s ORDER BY ota_version", (run_id,))
        return [_json_safe(dict(r)) for r in cur.fetchall()]


def known_otas(environment: str, limit: int = 200) -> list[str]:
    """OTA versions seen in earlier runs of this environment (for the UI's suggestions)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT ota FROM (
                   SELECT rr.ota_version AS ota, max(rr.run_id) AS last
                   FROM log_analyzer.run_reports rr JOIN log_analyzer.runs r ON r.id = rr.run_id
                   WHERE r.environment = %s GROUP BY 1
                   UNION ALL
                   SELECT ota_version, max(id) FROM log_analyzer.runs
                   WHERE ota_version IS NOT NULL AND environment = %s GROUP BY 1
               ) t GROUP BY ota ORDER BY max(last) DESC LIMIT %s""",
            (environment, environment, limit),
        )
        return [r[0] for r in cur.fetchall()]


# ── report comparisons ──────────────────────────────────────────────────────────────────

COMPARISON_COLUMNS = ("id, kind, start_date, end_date, items, status, stage, error, pid, requested_by, "
                      "created_at, finished_at, report_path")
_COMPARISON_UPDATABLE = {"items", "status", "stage", "error", "pid", "finished_at", "report_path"}


def create_comparison(kind: str, start_date: date | None, end_date: date | None, items: list[dict],
                      requested_by: str | None) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO log_analyzer.comparisons (kind, start_date, end_date, items, requested_by)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (kind, start_date, end_date, json.dumps(items), requested_by),
        )
        return cur.fetchone()[0]


def update_comparison(comparison_id: int, **fields) -> None:
    unknown = set(fields) - _COMPARISON_UPDATABLE
    if unknown:
        raise ValueError(f"Cannot update comparison fields {unknown}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = %s" for name in fields)
    values = [json.dumps(v) if name == "items" else v for name, v in fields.items()]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE log_analyzer.comparisons SET {assignments} WHERE id = %s", (*values, comparison_id))


def get_comparison(comparison_id: int) -> dict | None:
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {COMPARISON_COLUMNS} FROM log_analyzer.comparisons WHERE id = %s", (comparison_id,))
        row = cur.fetchone()
    return _json_safe(dict(row)) if row else None


def list_comparisons(limit: int = 20, statuses: tuple[str, ...] | None = None) -> list[dict]:
    where, params = ("WHERE status = ANY(%s)", [list(statuses)]) if statuses else ("", [])
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {COMPARISON_COLUMNS} FROM log_analyzer.comparisons {where} ORDER BY id DESC LIMIT %s",
                    (*params, limit))
        return [_json_safe(dict(r)) for r in cur.fetchall()]
