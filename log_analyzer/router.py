"""router.py — the Log Analyzer's FastAPI surface, mounted under /log-analyzer by atlas/main.py.

Trigger a boot-wise config/test-case report (lib/ota_report.py) for an OTA version
or a device list, and follow it: runs, per-step timings, live log, report files.
The UI (log_analyzer/ui/) runs separately (log_analyzer/serve_ui.py) and only talks to these routes.

Handlers are plain `def`: they do short blocking Postgres calls, which FastAPI
runs in its worker thread pool. The analysis itself runs in a separate worker
process per run (log_analyzer/worker.py), never in the API process.
"""

from __future__ import annotations

import logging
import mimetypes
import threading
import time
from pathlib import Path

import pandas as pd
import psycopg2
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from . import db, service
from .config import DEFAULT_ENVIRONMENT, ENVIRONMENTS, REPO_ROOT, RUNS_DIR, run_subpath
from .models import (Environment, Environments, OtaSuggestions, Run, RunCreateRequest, RunDetail, RunList, RunLog,
                     RunReport)

log = logging.getLogger("log_analyzer.router")

router = APIRouter(prefix="/log-analyzer", tags=["log-analyzer"])

_init_lock = threading.Lock()
_initialized = False


def _ready() -> None:
    """Schema + stale-run cleanup once per API process; 503 if atlas_db is unreachable."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        service.setup_logging()
        try:
            db.ensure_schema()
            service.reconcile()
        except (psycopg2.Error, OSError, ValueError) as exc:
            log.error("atlas_db unavailable: %s", exc)
            raise HTTPException(status_code=503, detail=f"Run database unavailable: {exc}") from exc
        _initialized = True


def _parse_date(value: str, field: str):
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise HTTPException(status_code=422, detail=f"Invalid {field} date: {value}")
    return parsed.date()


def _run_or_404(run_id: int) -> dict:
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


def _check_environment(environment: str | None) -> None:
    if environment is not None and environment not in ENVIRONMENTS:
        raise HTTPException(status_code=422, detail=f"Unknown environment {environment!r}")


@router.get("/health")
def health() -> dict:
    _ready()
    return {"status": "ok"}


@router.post("/runs", response_model=Run, status_code=201)
def create_run(req: RunCreateRequest) -> Run:
    """Queue a run and start its worker. Returns immediately; poll GET /runs/{id}."""
    _ready()
    t0 = time.perf_counter()
    start, end = _parse_date(req.start, "start"), _parse_date(req.end, "end")
    try:
        service.validate_range(start, end)
        devices = service.parse_devices(req.devices) if req.mode == "devices" else []
        otas = service.parse_otas(req.otas) if req.mode == "devices" else []
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ota = (req.ota_version or "").strip() or None
    if req.mode == "ota" and (not ota or not service.OTA_RE.match(ota)):
        raise HTTPException(status_code=422, detail="A valid ota_version is required for mode=ota")
    if req.mode == "devices" and not devices:
        raise HTTPException(status_code=422, detail="At least one device id is required for mode=devices")

    options = {k: v for k, v in {
        "max_devices": req.max_devices, "workers": req.workers,
        "extract_logs": req.extract_logs,
        "otas": otas or None,
    }.items() if v is not None}
    run_id = db.create_run(req.mode, req.environment, ota if req.mode == "ota" else None, devices, start, end,
                           (req.product or "").strip() or None, options, req.requested_by)
    service.start_run(run_id)
    log.info("run %s created (%s, %s, %s → %s) in %.2fs", run_id, req.environment,
             ota if req.mode == "ota" else f"{len(devices)} device(s)" + (f" on {', '.join(otas)}" if otas else ""),
             start, end, time.perf_counter() - t0)
    return Run(**db.get_run(run_id))


@router.get("/runs", response_model=RunList)
def list_runs(limit: int = 50, status: str | None = None, environment: str | None = None) -> RunList:
    _ready()
    _check_environment(environment)
    return RunList(runs=[Run(**r) for r in db.list_runs(min(max(limit, 1), 500), status, environment)])


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int) -> RunDetail:
    _ready()
    run = _run_or_404(run_id)
    reports = []
    for r in db.get_reports(run_id):
        rel = run_subpath(run_id, r["report_path"], "reports")
        reports.append(RunReport(**r, url=f"/log-analyzer/runs/{run_id}/files/{rel.as_posix()}"))
    return RunDetail(run=Run(**run), reports=reports, steps=db.step_summary(run_id),
                     devices=db.device_timings(run_id))


@router.get("/runs/{run_id}/log", response_model=RunLog)
def get_run_log(run_id: int, offset: int = 0) -> RunLog:
    """Incremental run.log: pass the returned offset back to get only new lines."""
    _ready()
    run = _run_or_404(run_id)
    text, new_offset = service.read_log(run, max(offset, 0))
    return RunLog(text=text, offset=new_offset, done=run["status"] not in ("queued", "running"))


@router.post("/runs/{run_id}/cancel", response_model=Run)
def cancel_run(run_id: int) -> Run:
    _ready()
    run = _run_or_404(run_id)
    if run["status"] not in ("queued", "running"):
        raise HTTPException(status_code=409, detail=f"Run {run_id} is already {run['status']}")
    service.cancel_run(run)
    return Run(**db.get_run(run_id))


@router.post("/runs/{run_id}/rerun", response_model=Run, status_code=201)
def rerun(run_id: int) -> Run:
    """Start a new run with the same inputs."""
    _ready()
    old = _run_or_404(run_id)
    new_id = db.create_run(old["mode"], old["environment"], old["ota_version"], old["requested_devices"],
                           old["start_date"], old["end_date"], old["product"], old["options"], old["requested_by"])
    service.start_run(new_id)
    log.info("run %s created as a rerun of %s", new_id, run_id)
    return Run(**db.get_run(new_id))


@router.get("/runs/{run_id}/files/{path:path}")
def run_file(run_id: int, path: str):
    """Serve a run's report files (ota_report.html, devices/<id>.html, ...)."""
    base = (RUNS_DIR / str(run_id) / "reports").resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")


@router.get("/environments", response_model=Environments)
def environments() -> Environments:
    """Staging/production and what each one points at (shown by the UI's environment switch)."""
    return Environments(default=DEFAULT_ENVIRONMENT, environments=[
        Environment(key=key, label=e["label"], bucket=e["bucket"], device_db=e["device_db"],
                    log_root=Path(e["log_root"]).relative_to(REPO_ROOT).as_posix())
        for key, e in ENVIRONMENTS.items()
    ])


@router.get("/otas", response_model=OtaSuggestions)
def ota_suggestions(environment: str = DEFAULT_ENVIRONMENT) -> OtaSuggestions:
    """OTA versions for the form's suggestions: this environment's earlier runs, then ones with a fetched config."""
    _ready()
    _check_environment(environment)
    otas = db.known_otas(environment)
    input_dir = REPO_ROOT / "INPUT"
    if input_dir.is_dir():
        otas += sorted((p.name for p in input_dir.iterdir()
                        if p.is_dir() and (p / "bagheera_config.ini").exists()), reverse=True)
    return OtaSuggestions(otas=list(dict.fromkeys(otas)))
