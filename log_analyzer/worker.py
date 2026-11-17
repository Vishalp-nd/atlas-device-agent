"""worker.py — executes one Log Analyzer run; started by the API as its own process.

    python -m log_analyzer.worker <run_id>      (from the repo root)

Stages, each timed and recorded in log_analyzer.run_steps:
  1. queue    wait for the advisory lock, so only one run does heavy log I/O at a time
  2. resolve  device list (given, or up to OTA_DEVICE_LIMIT devices currently on the OTA; an OTA
              run can bring that list from the UI when this backend can't reach the device db)
  3. filter   only when the run names features: keep the devices with all of them enabled by
              their cloud override config + OTA package defaults (lib/feature_filter.py)
  4. download log archives from s3 per device (kept compressed; lib/log_archive.py reads them)
The run's environment (config.ENVIRONMENTS) picks the s3 bucket, the device db and the
local log/config caches; the caches reach lib/ via ATLAS_LOG_ROOT / ATLAS_CONFIG_JSON_ROOT.
  5. analyse  lib/ota_report.run into OUTPUT/log_analyzer_runs/<id>/reports/
Everything is logged with durations to <run dir>/run.log and logs/log_analyzer.log.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from .config import (ENVIRONMENTS, LIB_DIR, REPO_ROOT, RUN_LOCK_KEY, RUNS_DIR,
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


def resolve_devices(run: dict) -> list[str]:
    if run["mode"] == "devices":
        return list(dict.fromkeys(run["requested_devices"]))
    from .devices import device_limit, ota_devices

    ota = run["ota_version"]
    limit = device_limit(run["options"].get("max_devices"))
    if run["requested_devices"]:
        # Resolved by the UI server (its device db is reachable from there, not from here)
        devices = list(dict.fromkeys(run["requested_devices"]))[:limit]
        found = run["options"].get("ota_devices") or {}
        log.info("OTA %s: %d device(s) resolved by the UI from %s%s", ota, len(devices),
                 found.get("device_db") or "the device db",
                 f" ({found['total']} on the OTA, limit {limit})" if found.get("total") is not None else "")
        return devices
    found = ota_devices(run["environment"], ota, limit)
    log.info("OTA %s: %d device(s) in %s (%s)", ota, found["total"], found["device_db"],
             ", ".join(f"{k} {v}" for k, v in found["states"].items()))
    if found["total"] > limit:
        log.warning("OTA %s: analysing %d of %d devices (limit %d)", ota, limit, found["total"], limit)
    return found["devices"]


def report_otas(run: dict) -> list[str] | None:
    """The OTA versions to report on: the run's OTA, the OTAs given with a device list, or None (every OTA)."""
    if run["mode"] == "ota":
        return [run["ota_version"]]
    return run["options"].get("otas") or None


def filter_by_features(run: dict, devices: list[str]) -> list[str]:
    """The devices with every feature the run names enabled (all of them when it names none).

    Only narrows down the devices to analyse; the reports' feature and config checks still come
    from the logs. The per-device result goes to <run dir>/feature_filter.json and the run log.
    """
    names = run["options"].get("features")
    if not names:
        return devices
    import feature_filter

    db.update_run(run["id"], stage="filter", progress={"stage": "filter", "done": 0, "total": len(devices)})
    result = feature_filter.filter_devices(
        devices, names, run["environment"],
        run_ota=run["ota_version"] if run["mode"] == "ota" else None,
        run_otas=report_otas(run) if run["mode"] == "devices" else None,
        product=run["product"] or None)
    (run_dir(run["id"]) / "feature_filter.json").write_text(json.dumps(result, indent=1))
    for device, reason in result["skipped"].items():
        log.info("feature filter: %s skipped: %s", device, reason)
    return result["kept"]


def download(run: dict, devices: list[str], timings: list[dict]) -> None:
    from downloader import Downloader

    env = ENVIRONMENTS[run["environment"]]
    server = run["environment"]
    extract = bool(run["options"].get("extract_logs"))
    # Also the day before the range (a drive across midnight into the start date is kept whole)
    # and the day after it: the last archive of a day is often uploaded (and so filed) the next
    # day, and a drive's UDID record is written in the next boot (ota_report.read_days)
    first_day = (datetime.strptime(run["start_date"], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    last_day = (datetime.strptime(run["end_date"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    for i, device in enumerate(devices, 1):
        db.update_run(run["id"], progress={"stage": "download", "done": i - 1, "total": len(devices),
                                           "device": device})
        with timed(log, "download", timings, device=device, bucket=env["bucket"]):
            # Day folders that already exist are skipped by the downloader
            Downloader(server, filetype="log", extract_logs=extract,
                       log_root=str(env["log_root"])).download_manager(
                device, first_day, last_day)
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
        otas=report_otas(run),
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
    # IDMS hosts for the cloud override config check (lib/cloud_config.py)
    os.environ["ATLAS_ENV"] = run["environment"]
    target = run["ota_version"] if run["mode"] == "ota" else f"{len(run['requested_devices'])} device(s)"
    if run["mode"] == "devices" and report_otas(run):
        target += f" on OTA {', '.join(report_otas(run))}"
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
        if not devices:
            raise RuntimeError("No devices to analyse (none currently on this OTA)")
        if run["options"].get("features"):
            with timed(log, "feature_filter", timings, devices=len(devices)):
                devices = filter_by_features(run, devices)
            if not devices:
                # Nothing to analyse is the run's answer, not a failure (shown as a warning)
                note = f"No device has all of these features enabled: {', '.join(run['options']['features'])}"
                log.warning("run %s: %s", run_id, note)
                db.update_run(run_id, devices=[], status="succeeded", error=note, stage="done")
                return 0
        log.info("analysing %d device(s): %s", len(devices), ", ".join(devices[:50]) +
                 (" ..." if len(devices) > 50 else ""))
        db.update_run(run_id, devices=devices, stage="download")

        with timed(log, "download_all", timings):
            download(run, devices, timings)

        db.update_run(run_id, stage="analyse", progress={"stage": "analyse", "done": 0, "total": len(devices)})
        with timed(log, "analyse", timings):
            result = analyse(run, devices, timings)
        timings.extend(result["timings"])
        db.save_reports(run_id, result["reports"])

        # No boots on the OTA is a finished run with nothing to report, not a failure;
        # the message is kept as a note on the run (shown as a warning in the UI)
        status, error = "succeeded", None
        if not result["reports"]:
            error = (f"No boots on OTA {run['ota_version']} in the logs" if run["mode"] == "ota"
                     else f"No boots on OTA {', '.join(report_otas(run))} in the logs" if report_otas(run)
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
