"""compare_worker.py — builds one report comparison; started by the API as its own process.

    python -m log_analyzer.compare_worker <comparison_id>      (from the repo root)

A comparison of two OTAs or two devices waits for the two normal runs it triggered (they
queue like any run), then picks each side's report: the run's report of that OTA, or for a
device the report of its latest OTA (the OTA of its last boot, when it was upgraded in the
range). A comparison of two existing reports starts right away. lib/compare_report.py then
writes <RUNS_DIR>/compare/<id>/compare_report.{json,html}.

Waiting is idempotent, so the API restarts the worker of an unfinished comparison
(service.reconcile).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

from .config import ENVIRONMENTS, LIB_DIR, REPO_ROOT, RUNS_DIR, SERVICE_LOG, run_subpath

sys.path.insert(0, str(LIB_DIR))

import run_logging  # noqa: E402

from . import db  # noqa: E402

log = logging.getLogger("log_analyzer.compare_worker")

POLL_S = 10
DONE = ("succeeded", "failed", "cancelled")


class Cancelled(Exception):
    pass


def compare_dir(comparison_id: int):
    return RUNS_DIR / "compare" / str(comparison_id)


def _report_file(run_id: int, report: dict):
    """The report's ota_report.json (report_path is its .html)."""
    html = RUNS_DIR / str(run_id) / "reports" / run_subpath(run_id, report["report_path"], "reports")
    return html.with_name("ota_report.json")


def _latest_boot(report_json: dict, device: str) -> str:
    """The device's last boot start in a report ("YYYY-MM-DD HH:MM:SS.mmm" sorts as text)."""
    return max((b.get("start") or "" for b in (report_json.get("devices") or {}).get(device) or []), default="")


def pick_report(item: dict, kind: str) -> tuple[dict, dict]:
    """(run_reports row, ota_report.json) of one side."""
    run_id = item["run_id"]
    reports = db.get_reports(run_id)
    if not reports:
        run = db.get_run(run_id) or {}
        raise RuntimeError(f"Run #{run_id} ({item.get('target')}) produced no report"
                           + (f": {run['error']}" if run.get("error") else ""))
    if item.get("ota_version") or kind == "ota":
        ota = item.get("ota_version") or item["target"]
        row = next((r for r in reports if r["ota_version"] == ota), None)
        if row is None:
            raise RuntimeError(f"Run #{run_id} has no report for OTA {ota}")
        return row, json.loads(_report_file(run_id, row).read_text())
    # A device: the report of its latest OTA (the one its last boot is on)
    best = None
    for row in reports:
        doc = json.loads(_report_file(run_id, row).read_text())
        last = _latest_boot(doc, item["target"])
        if best is None or last > best[0]:
            best = (last, row, doc)
    if len(reports) > 1:
        log.info("device %s: %d OTA reports, latest OTA %s", item["target"], len(reports), best[1]["ota_version"])
    return best[1], best[2]


def _labels(kind: str, items: list[dict], rows: list[dict]) -> list[str]:
    if kind == "devices":
        labels = [f"{it['target']} · {row['ota_version']}" for it, row in zip(items, rows)]
    else:
        labels = [row["ota_version"] for row in rows]
    if labels[0] == labels[1]:
        # Same OTA on both sides: tell them apart by environment, else by run
        envs = [it.get("environment") for it in items]
        extra = ([ENVIRONMENTS[e]["label"] for e in envs] if envs[0] != envs[1]
                 else [f"run #{it['run_id']}" for it in items])
        labels = [f"{label} ({x})" for label, x in zip(labels, extra)]
    return labels


def _side(item: dict, row: dict) -> dict:
    run = db.get_run(item["run_id"]) or {}
    env = ENVIRONMENTS.get(run.get("environment") or item.get("environment") or "stag", {}).get("label", "")
    span = run.get("start_date", "")
    if run.get("end_date") and run["end_date"] != span:
        span += f" → {run['end_date']}"
    rel = run_subpath(item["run_id"], row["report_path"], "reports").as_posix()
    return {"sub": f"{env} · run #{item['run_id']} · {span}",
            "href": f"/log-analyzer/runs/{item['run_id']}/files/{rel}"}


def wait_for_runs(comparison: dict) -> None:
    ids = [it["run_id"] for it in comparison["items"]]
    while True:
        runs = [db.get_run(i) for i in ids]
        bad = [r for r in runs if r is None or r["status"] in ("failed", "cancelled")]
        if bad:
            r = bad[0]
            raise RuntimeError(f"Run #{r['id'] if r else '?'} {r['status'] if r else 'is missing'}"
                               + (f": {r['error']}" if r and r.get("error") else ""))
        if all(r["status"] == "succeeded" for r in runs):
            return
        if (db.get_comparison(comparison["id"]) or {}).get("status") == "cancelled":
            raise Cancelled()
        time.sleep(POLL_S)


def _on_sigterm(signum, frame):
    raise Cancelled()


def main(comparison_id: int) -> int:
    folder = compare_dir(comparison_id)
    folder.mkdir(parents=True, exist_ok=True)
    run_logging.configure([folder / "compare.log", SERVICE_LOG], stream=False)
    signal.signal(signal.SIGTERM, _on_sigterm)
    os.chdir(REPO_ROOT)
    comparison = db.get_comparison(comparison_id)
    if comparison is None:
        log.error("comparison %s not found", comparison_id)
        return 2
    db.update_comparison(comparison_id, pid=os.getpid(), status="running",
                         stage="waiting" if comparison["kind"] != "reports" else "building")
    t0 = time.perf_counter()
    try:
        if comparison["kind"] != "reports":
            log.info("comparison %s: waiting for runs %s", comparison_id, [it["run_id"] for it in comparison["items"]])
            wait_for_runs(comparison)
            db.update_comparison(comparison_id, stage="building")
        import compare_report

        picked = [pick_report(it, comparison["kind"]) for it in comparison["items"]]
        rows = [p[0] for p in picked]
        labels = _labels(comparison["kind"], comparison["items"], rows)
        items = [dict(it, ota_version=row["ota_version"], label=label)
                 for it, row, label in zip(comparison["items"], rows, labels)]
        db.update_comparison(comparison_id, items=items)
        doc = compare_report.build(picked[0][1], picked[1][1], labels, folder,
                                   sides=[_side(it, row) for it, row in zip(items, rows)])
        db.update_comparison(comparison_id, status="succeeded", stage="done", error=None,
                             report_path="compare_report.html", finished_at=datetime.now(timezone.utc), pid=None)
        log.info("comparison %s: %s vs %s built in %.1fs (%d API, %d KPI callouts)", comparison_id, labels[0], labels[1],
                 time.perf_counter() - t0, len(doc["callouts"]["callouts"]), len(doc["kpi_callouts"]["callouts"]))
        return 0
    except Cancelled:
        db.update_comparison(comparison_id, status="cancelled", stage="cancelled",
                             finished_at=datetime.now(timezone.utc), pid=None)
        return 130
    except Exception as exc:
        log.exception("comparison %s failed: %s", comparison_id, exc)
        db.update_comparison(comparison_id, status="failed", stage="failed", error=str(exc),
                             finished_at=datetime.now(timezone.utc), pid=None)
        return 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
