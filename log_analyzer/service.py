"""service.py — start, cancel and reconcile Log Analyzer runs (worker processes)."""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone

from . import db
from .config import REPO_ROOT, RUNS_DIR, SERVICE_LOG, runs_relative

log = logging.getLogger("log_analyzer.service")

DEVICE_RE = re.compile(r"^\d{6,14}$")
OTA_RE = re.compile(r"^[\w.\-]+$")
MAX_RANGE_DAYS = 31

_logging_ready = False


def setup_logging() -> None:
    """API-side log_analyzer.* records go to logs/log_analyzer.log (the worker adds its own)."""
    global _logging_ready
    if _logging_ready:
        return
    logger = logging.getLogger("log_analyzer")
    logger.setLevel(logging.INFO)
    SERVICE_LOG.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(SERVICE_LOG, encoding="utf-8")
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    from run_logging import LOG_FORMAT
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    _logging_ready = True


def parse_devices(values: list[str]) -> list[str]:
    """Accept ids separated by commas/whitespace/newlines, possibly in several list items."""
    devices = []
    for value in values:
        devices.extend(part for part in re.split(r"[\s,;]+", value) if part)
    bad = [d for d in devices if not DEVICE_RE.match(d)]
    if bad:
        raise ValueError(f"Invalid device id(s): {', '.join(bad[:10])}")
    return list(dict.fromkeys(devices))


def parse_otas(values: list[str]) -> list[str]:
    """Accept OTA versions separated by commas/whitespace, possibly in several list items."""
    otas = []
    for value in values:
        otas.extend(part for part in re.split(r"[\s,;]+", value) if part)
    bad = [o for o in otas if not OTA_RE.match(o)]
    if bad:
        raise ValueError(f"Invalid OTA version(s): {', '.join(bad[:10])}")
    return list(dict.fromkeys(otas))


def validate_range(start: date, end: date) -> None:
    if start > end:
        raise ValueError("start must be on or before end")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"Date range is limited to {MAX_RANGE_DAYS} days")


def start_run(run_id: int) -> int:
    """Launch the worker for a run in its own session (so cancel can kill its whole group)."""
    folder = RUNS_DIR / str(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    out = open(folder / "worker.out", "ab")
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "log_analyzer.worker", str(run_id)],
        cwd=REPO_ROOT, stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True,
    )
    out.close()
    db.update_run(run_id, pid=proc.pid, output_dir=runs_relative(folder), log_path=runs_relative(folder / "run.log"))
    # Reap the child when it exits so it doesn't linger as a zombie in the API process
    threading.Thread(target=proc.wait, daemon=True).start()
    log.info("run %s: worker started (pid %s)", run_id, proc.pid)
    return proc.pid


def start_compare(comparison_id: int) -> int:
    """Launch the worker of a report comparison (log_analyzer/compare_worker.py)."""
    folder = RUNS_DIR / "compare" / str(comparison_id)
    folder.mkdir(parents=True, exist_ok=True)
    out = open(folder / "worker.out", "ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "log_analyzer.compare_worker", str(comparison_id)],
        cwd=REPO_ROOT, stdout=out, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONUNBUFFERED="1"),
        start_new_session=True,
    )
    out.close()
    db.update_comparison(comparison_id, pid=proc.pid)
    threading.Thread(target=proc.wait, daemon=True).start()
    log.info("comparison %s: worker started (pid %s)", comparison_id, proc.pid)
    return proc.pid


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cancel_run(run: dict) -> None:
    pid = run.get("pid")
    db.update_run(run["id"], status="cancelled", stage="cancelled",
                  finished_at=datetime.now(timezone.utc))
    if _alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        def _force_kill():
            time.sleep(15)
            if _alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        threading.Thread(target=_force_kill, daemon=True).start()
    log.info("run %s: cancelled (pid %s)", run["id"], pid)


def reconcile() -> None:
    """Runs left queued/running by a restarted API (their worker died with it) are marked failed."""
    for run in db.runs_with_status(("queued", "running")):
        if not _alive(run.get("pid")):
            db.update_run(run["id"], status="failed", error="Interrupted: the worker process is gone "
                          "(API restarted?)", finished_at=datetime.now(timezone.utc), pid=None)
            log.warning("run %s: worker gone, marked failed", run["id"])
    # A comparison only waits and then builds: restart its worker
    for comparison in db.list_comparisons(200, ("queued", "running")):
        if not _alive(comparison.get("pid")):
            log.warning("comparison %s: worker gone, restarting it", comparison["id"])
            start_compare(comparison["id"])


def read_log(run: dict, offset: int, max_bytes: int = 256 * 1024) -> tuple[str, int]:
    # Always this backend's own RUNS_DIR: the stored log_path may be from another mount
    path = RUNS_DIR / str(run["id"]) / "run.log"
    if not path.exists():
        return "", offset
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if offset > size:
            offset = 0
        if offset == 0 and size > max_bytes:
            offset = size - max_bytes  # first fetch: just the tail
        f.seek(offset)
        data = f.read(max_bytes)
    return data.decode("utf-8", errors="replace"), offset + len(data)
