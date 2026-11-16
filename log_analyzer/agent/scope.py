"""What one agent chat may see: the devices of its run (or of both runs of a comparison), each
on the run's dates plus the day before and after (the days the analysis itself reads, see
lib/ota_report.read_days), and the run's reports. Nothing else is readable."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .. import db
from ..config import ENVIRONMENTS, RUNS_DIR

ARCHIVES = (".7z", ".zip")


def _days(start: str, end: str) -> list[str]:
    first = date.fromisoformat(str(start)[:10]) - timedelta(days=1)
    last = date.fromisoformat(str(end)[:10]) + timedelta(days=1)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def _downloaded(day_dir: Path) -> bool:
    return day_dir.is_dir() and ((day_dir / "logs").is_dir() or any(
        p.is_file() and p.name.endswith(ARCHIVES) for p in day_dir.iterdir()))


def _run_part(run: dict) -> dict:
    env = ENVIRONMENTS[run["environment"]]
    log_root = Path(env["log_root"])
    devices = [str(d) for d in (run.get("devices") or run.get("requested_devices") or [])]
    start, end = str(run["start_date"])[:10], str(run["end_date"])[:10]
    out = []
    for device in devices:
        days = []
        for day in _days(start, end):
            days.append({"date": day, "edge": not (start <= day <= end),
                         "downloaded": _downloaded(log_root / device / day)})
        out.append({"device": device, "environment": run["environment"], "days": days})
    reports = RUNS_DIR / str(run["id"]) / "reports"
    return {"run_id": run["id"], "environment": run["environment"], "mode": run["mode"],
            "ota_version": run.get("ota_version"), "start": start, "end": end,
            "log_root": str(log_root), "devices": out,
            "reports_dir": str(reports) if reports.is_dir() else None}


def build(kind: str, target_id: int) -> dict:
    """{kind, id, title, runs: [...], reports_dirs: [...]} or LookupError."""
    if kind == "run":
        run = db.get_run(target_id)
        if run is None:
            raise LookupError(f"Run {target_id} not found")
        runs = [_run_part(run)]
        title = f"Run #{target_id}"
        extra = []
    elif kind == "comparison":
        c = db.get_comparison(target_id)
        if c is None:
            raise LookupError(f"Comparison {target_id} not found")
        runs = []
        for item in c.get("items") or []:
            run = db.get_run(int(item["run_id"])) if item.get("run_id") else None
            if run is not None:
                runs.append(_run_part(run))
        title = f"Comparison #{target_id}"
        cdir = RUNS_DIR / "compare" / str(target_id)
        extra = [str(cdir)] if cdir.is_dir() else []
    else:
        raise LookupError(f"unknown target {kind!r}")
    if not any(r["devices"] for r in runs):
        raise LookupError(f"{title} has no devices to look at")
    dirs = [r["reports_dir"] for r in runs if r["reports_dir"]] + extra
    return {"kind": kind, "id": target_id, "title": title, "runs": runs, "reports_dirs": dirs}


def summary(scope: dict) -> dict:
    """What the chat header shows."""
    devices = {(d["environment"], d["device"]) for r in scope["runs"] for d in r["devices"]}
    starts = [r["start"] for r in scope["runs"]]
    ends = [r["end"] for r in scope["runs"]]
    first = (date.fromisoformat(min(starts)) - timedelta(days=1)).isoformat()
    last = (date.fromisoformat(max(ends)) + timedelta(days=1)).isoformat()
    return {"title": scope["title"], "devices": len(devices),
            "device_ids": sorted({d for _, d in devices})[:12],
            "environments": sorted({e for e, _ in devices}),
            "from": first, "to": last, "range_start": min(starts), "range_end": max(ends),
            "reports": len(scope["reports_dirs"])}
