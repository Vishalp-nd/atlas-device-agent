"""worker.py — executes one Log Analyzer run; started by the API as its own process.

    python -m log_analyzer.worker <run_id>      (from the repo root)

Stages, each timed and recorded in log_analyzer.run_steps:
  1. queue    wait for the advisory lock, so only one run does heavy log I/O at a time
  2. resolve  device list (given, or up to OTA_DEVICE_LIMIT devices currently on the OTA)
  3. download log archives from s3 per device (kept compressed; lib/log_archive.py reads them)
The run's environment (config.ENVIRONMENTS) picks the s3 bucket, the device db and the
local log/config caches; the caches reach lib/ via ATLAS_LOG_ROOT / ATLAS_CONFIG_JSON_ROOT.
  4. analyse  lib/ota_report.run into OUTPUT/log_analyzer_runs/<id>/reports/
Everything is logged with durations to <run dir>/run.log and logs/log_analyzer.log.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from .config import (ENVIRONMENTS, LIB_DIR, OTA_DEVICE_LIMIT, REPO_ROOT, RUN_LOCK_KEY, RUNS_DIR,
                     SERVICE_LOG, TESTS_DIR, TESTS_SNAPSHOT, WORKERS, runs_relative)

# lib/ modules import each other as top-level modules
sys.path.insert(0, str(LIB_DIR))

import run_logging  # noqa: E402
from run_logging import timed  # noqa: E402

from . import db  # noqa: E402

log = logging.getLogger("log_analyzer.worker")


class Cancelled(Exception):
    pass


def run_dir(run_id: int):
    return RUNS_DIR / str(run_id)


# Devices that are out in the field log the most; list them first when capping
_STATE_ORDER = {"INSTALLED": 0, "SHIPPED_ACTIVE": 1}


def resolve_devices(run: dict) -> list[str]:
    if run["mode"] == "devices":
        return list(dict.fromkeys(run["requested_devices"]))
    from pipeline.fetch_device_list_by_product_line import _fetch_rows_for_ota

    ota = run["ota_version"]
    device_db = ENVIRONMENTS[run["environment"]]["device_db"]
    conn = db.db_connect(device_db)
    try:
        # rows: [device_id, ota, config, tenant_id, tenant_name, tenant_unique_name, state]
        rows = _fetch_rows_for_ota(conn, ota)
    finally:
        conn.close()
    rows.sort(key=lambda r: _STATE_ORDER.get(r[6], 2))
    devices = list(dict.fromkeys(str(r[0]) for r in rows))
    states: dict[str, int] = {}
    for r in rows:
        states[r[6]] = states.get(r[6], 0) + 1
    log.info("OTA %s: %d device(s) in %s (%s)", ota, len(devices), device_db,
             ", ".join(f"{k} {v}" for k, v in sorted(states.items(), key=lambda kv: -kv[1])))
    limit = min(int(run["options"].get("max_devices") or OTA_DEVICE_LIMIT), OTA_DEVICE_LIMIT)
    if len(devices) > limit:
        log.warning("OTA %s: analysing %d of %d devices (limit %d)", ota, limit, len(devices), limit)
        devices = devices[:limit]
    return devices


def download(run: dict, devices: list[str], timings: list[dict]) -> None:
    from downloader import Downloader

    env = ENVIRONMENTS[run["environment"]]
    server = run["environment"]
    extract = bool(run["options"].get("extract_logs"))
    for i, device in enumerate(devices, 1):
        db.update_run(run["id"], progress={"stage": "download", "done": i - 1, "total": len(devices),
                                           "device": device})
        with timed(log, "download", timings, device=device, bucket=env["bucket"]):
            # Day folders that already exist are skipped by the downloader
            Downloader(server, filetype="log", extract_logs=extract,
                       log_root=str(env["log_root"])).download_manager(
                device, run["start_date"], run["end_date"])
    db.update_run(run["id"], progress={"stage": "download", "done": len(devices), "total": len(devices)})


def analyse(run: dict, devices: list[str], timings: list[dict]) -> dict:
    import ota_report

    def on_progress(done, total, device, error):
        db.update_run(run["id"], progress={"stage": "analyse", "done": done, "total": total, "device": device})

    return ota_report.run(
        devices, run["start_date"], run["end_date"], str(TESTS_DIR),
        product=run["product"] or None,
        workers=run["options"].get("workers") or WORKERS,
        report_dir=run_dir(run["id"]) / "reports",
        otas=[run["ota_version"]] if run["mode"] == "ota" else None,
        tests_snapshot=TESTS_SNAPSHOT,
        on_progress=on_progress,
    )


def _on_sigterm(signum, frame):
    raise Cancelled("cancelled")


def main(run_id: int) -> int:
    folder = run_dir(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / "run.log"
    run_logging.configure([log_path, SERVICE_LOG], stream=False)
    signal.signal(signal.SIGTERM, _on_sigterm)
    os.chdir(REPO_ROOT)  # the downloader writes to OUTPUT/log relative to the cwd

    run = db.get_run(run_id)
    if run is None:
        log.error("run %s not found", run_id)
        return 2
    env = ENVIRONMENTS[run["environment"]]
    # Read by lib/ota_report.py and lib/cfg_parser_logs.py at import (also in their worker processes)
    os.environ["ATLAS_LOG_ROOT"] = str(env["log_root"])
    os.environ["ATLAS_CONFIG_JSON_ROOT"] = str(env["config_root"])
    target = run["ota_version"] if run["mode"] == "ota" else f"{len(run['requested_devices'])} device(s)"
    log.info("run %s: %s, %s %s, %s → %s, options %s", run_id, env["label"], run["mode"], target,
             run["start_date"], run["end_date"], run["options"])
    log.info("environment %s: logs s3://%s -> %s, devices from %s", run["environment"], env["bucket"],
             env["log_root"], env["device_db"])
    db.update_run(run_id, pid=os.getpid(), output_dir=runs_relative(folder), log_path=runs_relative(log_path), stage="queued")

    timings: list[dict] = []
    t0 = time.perf_counter()
    lock_conn = None
    try:
        with timed(log, "queue", timings):
            # Session-level advisory lock, held on this connection until the run ends
            lock_conn = db.db_connect(db.DB_SECTION)
            lock_conn.autocommit = True
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (RUN_LOCK_KEY,))
                if not cur.fetchone()[0]:
                    log.info("another run is analysing, waiting for it to finish")
                    cur.execute("SELECT pg_advisory_lock(%s)", (RUN_LOCK_KEY,))
        started = datetime.now(timezone.utc)
        db.update_run(run_id, status="running", stage="resolve", started_at=started)

        with timed(log, "resolve", timings):
            devices = resolve_devices(run)
        log.info("analysing %d device(s): %s", len(devices), ", ".join(devices[:50]) +
                 (" ..." if len(devices) > 50 else ""))
        db.update_run(run_id, devices=devices, stage="download")
        if not devices:
            raise RuntimeError("No devices to analyse (none currently on this OTA)")

        with timed(log, "download_all", timings):
            download(run, devices, timings)

        db.update_run(run_id, stage="analyse", progress={"stage": "analyse", "done": 0, "total": len(devices)})
        with timed(log, "analyse", timings):
            result = analyse(run, devices, timings)
        timings.extend(result["timings"])
        db.save_reports(run_id, result["reports"])

        status, error = "succeeded", None
        if not result["reports"]:
            status = "failed"
            error = (f"No boots on OTA {run['ota_version']} in the logs" if run["mode"] == "ota"
                     else "No device boots with an OTA version found in the logs")
        db.update_run(run_id, status=status, error=error, stage="done", device_errors=result["device_errors"])
        log.info("run %s %s: %d report(s), %d device error(s)", run_id, status,
                 len(result["reports"]), len(result["device_errors"]))
        return 0 if status == "succeeded" else 1
    except Cancelled:
        log.warning("run %s cancelled", run_id)
        db.update_run(run_id, status="cancelled", stage="cancelled")
        return 130
    except Exception as exc:
        log.exception("run %s failed: %s", run_id, exc)
        db.update_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        duration = round(time.perf_counter() - t0, 3)
        timings.append({"step": "run_total", "status": "ok", "duration_s": duration,
                        "started_at": (datetime.now(timezone.utc) - timedelta(seconds=duration)).isoformat()})
        try:
            db.add_steps(run_id, timings)
            db.update_run(run_id, finished_at=datetime.now(timezone.utc), duration_s=duration, pid=None)
        except Exception:
            log.exception("run %s: could not record timings", run_id)
        log.info("run %s took %.2fs", run_id, duration)
        if lock_conn is not None:
            lock_conn.close()


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
