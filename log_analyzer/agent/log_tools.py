"""MCP server (stdio) with the log tools of one agent chat.

    LA_SCOPE=<chat>/scope.json LA_CACHE=<cache dir> python log_tools.py

Started by Claude Code for each turn of a chat (log_analyzer/agent/turn.py). Every tool takes a
device and a date and works on that device-day's logs, rebuilt from its downloaded archives
exactly as the analysis does (lib/log_archive.merge_archives: one <service>.log per service,
syslogs/ apart). A rebuilt day is kept in the chat's cache folder while the chat uses it (at
most LA_AGENT_CACHE_DAYS days, least recently used dropped first) and removed with the chat's
cache. Only the devices and dates in the chat's scope are accepted.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import log_archive  # noqa: E402  (lib/)

PROTOCOL = "2025-06-18"
SCOPE = json.loads(Path(os.environ["LA_SCOPE"]).read_text())
CACHE = Path(os.environ["LA_CACHE"])
CACHE_DAYS = max(1, int(os.environ.get("LA_AGENT_CACHE_DAYS", "4")))
MAX_LINE = 1200
MAX_READ_LINES = 600
MAX_MATCHES = 400
GREP_SECONDS = 120


class ToolError(Exception):
    pass


# ── scope ──────────────────────────────────────────────────────────────────

def _entries():
    for run in SCOPE["runs"]:
        for d in run["devices"]:
            yield run, d


def _pick(device: str, environment: str = "") -> tuple[dict, dict]:
    device = str(device or "").strip()
    found = [(r, d) for r, d in _entries() if d["device"] == device
             and (not environment or d["environment"] == environment)]
    if not found:
        known = sorted({d["device"] for _, d in _entries()})
        raise ToolError(f"Device {device!r} is not in this {SCOPE['kind']}. Devices: "
                        + ", ".join(known[:50]) + (" …" if len(known) > 50 else ""))
    envs = {d["environment"] for _, d in found}
    if len(envs) > 1:
        raise ToolError(f"Device {device} is in more than one environment here "
                        f"({', '.join(sorted(envs))}); pass environment.")
    return found[0]


def _day(device: str, date: str, environment: str = "") -> tuple[dict, dict, str]:
    run, d = _pick(device, environment)
    date = str(date or "").strip()[:10]
    days = {x["date"]: x for x in d["days"]}
    if date not in days:
        raise ToolError(f"{date or 'That date'} is outside this {SCOPE['kind']} for device "
                        f"{d['device']}. Allowed dates: {', '.join(days)} (the run's range "
                        "plus one day either side).")
    if not days[date]["downloaded"]:
        have = [x["date"] for x in d["days"] if x["downloaded"]]
        raise ToolError(f"No logs were downloaded for device {d['device']} on {date}. "
                        f"Days with logs: {', '.join(have) or 'none'}.")
    return run, d, date


# ── rebuilt days ───────────────────────────────────────────────────────────

def _evict(keep: Path) -> None:
    ready = sorted((p.parent for p in CACHE.glob("*/*/*/.used")), key=lambda p: (p / ".used").stat().st_mtime)
    while len(ready) > CACHE_DAYS:
        old = ready.pop(0)
        if old != keep:
            shutil.rmtree(old, ignore_errors=True)


def _logs_dir(run: dict, d: dict, date: str) -> tuple[Path, dict]:
    """The device-day's logs/ folder, rebuilt into the cache on first use."""
    day_dir = Path(run["log_root"]) / d["device"] / date
    if (day_dir / "logs").is_dir() and not log_archive.archives(day_dir):
        return day_dir / "logs", {"rebuilt": False}
    slot = CACHE / d["environment"] / d["device"] / date
    logs = slot / "logs"
    slot.mkdir(parents=True, exist_ok=True)
    with (slot / ".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        info = {"rebuilt": False}
        if not (slot / ".ready").is_file():
            archives = log_archive.archives(day_dir)
            need = sum(log_archive.unpacked_bytes(a) or 17 * a.stat().st_size for a in archives)
            free = shutil.disk_usage(slot).free
            if need * 1.1 > free:
                raise ToolError(f"Not enough temp space to open {d['device']} {date} "
                                f"(needs ~{need / 1e9:.1f} GB, {free / 1e9:.1f} GB free).")
            t0 = time.time()
            shutil.rmtree(logs, ignore_errors=True)
            work = tempfile.mkdtemp(prefix="work_", dir=slot)
            try:
                log_archive.merge_archives(archives, str(logs), work)
            finally:
                shutil.rmtree(work, ignore_errors=True)
            (slot / ".ready").write_text(str(time.time()))
            info = {"rebuilt": True, "rebuild_seconds": round(time.time() - t0, 1),
                    "archives": len(archives)}
        (slot / ".used").touch()
    _evict(slot)
    return logs, info


def _files(logs: Path) -> list[Path]:
    return sorted(p for p in logs.rglob("*") if p.is_file())


def _file(logs: Path, name: str) -> Path:
    name = str(name or "").strip().lstrip("/")
    target = (logs / name).resolve()
    if logs.resolve() not in target.parents or not target.is_file():
        names = [p.relative_to(logs).as_posix() for p in _files(logs)]
        stem = Path(name).name.split(".")[0].lower()
        close = [n for n in names if len(stem) >= 2 and stem in n.lower()][:10]
        raise ToolError(f"No log file {name!r} that day."
                        + (f" Did you mean: {', '.join(close)}?" if close else
                           " Call list_log_files for the names."))
    return target


def _clip(text: str) -> str:
    text = text.rstrip("\n")
    return text if len(text) <= MAX_LINE else text[:MAX_LINE] + f" …[{len(text) - MAX_LINE} more chars]"


# ── tools ──────────────────────────────────────────────────────────────────

def t_scope(_: dict) -> dict:
    runs = []
    for run in SCOPE["runs"]:
        runs.append({
            "run_id": run["run_id"], "environment": run["environment"], "mode": run["mode"],
            "ota_version": run.get("ota_version"), "range": f"{run['start']} → {run['end']}",
            "devices": [{"device": d["device"],
                         "dates_with_logs": [x["date"] for x in d["days"] if x["downloaded"]],
                         "dates_without_logs": [x["date"] for x in d["days"] if not x["downloaded"]]}
                        for d in run["devices"]]})
    reports = []
    for base in SCOPE["reports_dirs"]:
        for p in sorted(Path(base).rglob("*")):
            if p.is_file() and len(reports) < 400:
                reports.append({"path": str(p), "bytes": p.stat().st_size})
    return {"kind": SCOPE["kind"], "id": SCOPE["id"], "runs": runs, "report_files": reports,
            "note": "Dates either side of a run's range are only there to complete boots that "
                    "cross midnight or were uploaded late; the run's own range is what it reports."}


def t_list_log_files(a: dict) -> dict:
    run, d, date = _day(a.get("device"), a.get("date"), a.get("environment", ""))
    logs, info = _logs_dir(run, d, date)
    files = [{"file": p.relative_to(logs).as_posix(), "bytes": p.stat().st_size} for p in _files(logs)]
    return {"device": d["device"], "date": date, **info, "files": files}


def t_search_logs(a: dict) -> dict:
    pattern = str(a.get("pattern") or "")
    if not pattern:
        raise ToolError("pattern is required")
    run, d = _pick(a.get("device"), a.get("environment", ""))
    dates = a.get("dates") or ([a["date"]] if a.get("date") else [])
    if isinstance(dates, str):
        dates = [x.strip() for x in dates.split(",") if x.strip()]
    if not dates or dates == ["all"]:
        dates = [x["date"] for x in d["days"] if x["downloaded"]]
    limit = min(max(int(a.get("max_matches") or 100), 1), MAX_MATCHES)
    context = min(max(int(a.get("context") or 0), 0), 10)
    count_only = bool(a.get("count_only"))
    flags = ["-a", "-n", "-H", "-F" if a.get("fixed_string") else "-E"]
    if a.get("ignore_case", True):
        flags.append("-i")
    if context and not count_only:
        flags += ["-C", str(context)]
    if count_only:
        flags.append("-c")
    out = {"device": d["device"], "pattern": pattern, "days": []}
    total = 0
    line_re = re.compile(r"^(.+?)([:-])(\d+)\2(.*)$")
    for date in dates:
        _, _, date = _day(d["device"], date, d["environment"])
        logs, info = _logs_dir(run, d, date)
        if a.get("file"):
            targets = [_file(logs, a["file"]).relative_to(logs).as_posix()]
        else:
            glob = str(a.get("files") or "")
            targets = [p.relative_to(logs).as_posix() for p in _files(logs)
                       if not glob or Path(p.name).match(glob) or p.relative_to(logs).match(glob)]
        day = {"date": date, **({"rebuilt_seconds": info["rebuild_seconds"]} if info.get("rebuilt") else {})}
        if not targets:
            day["matches"] = []
            out["days"].append(day)
            continue
        proc = subprocess.Popen(["grep", *flags, "--", pattern, *targets], cwd=logs,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                errors="replace")
        deadline = time.time() + GREP_SECONDS
        if count_only:
            counts = {}
            for raw in proc.stdout:
                name, _, n = raw.rstrip("\n").rpartition(":")
                if n.isdigit() and int(n):
                    counts[name] = int(n)
            proc.wait()
            day["counts"] = counts
            total += sum(counts.values())
        else:
            matches, hit = [], 0
            for raw in proc.stdout:
                if raw.startswith("--"):
                    continue
                m = line_re.match(raw.rstrip("\n"))
                if not m:
                    continue
                is_hit = m.group(2) == ":"
                if is_hit:
                    if total + hit >= limit:
                        day["truncated"] = True
                        break
                    hit += 1
                matches.append({"file": m.group(1), "line": int(m.group(3)), "text": _clip(m.group(4)),
                                **({} if is_hit or not context else {"context": True})})
                if time.time() > deadline:
                    day["truncated"] = True
                    break
            proc.kill()
            proc.wait()
            day["matches"] = matches
            total += hit
        err = proc.stderr.read().strip() if proc.stderr else ""
        if err and "Binary" not in err:
            day["grep_error"] = err[:300]
        out["days"].append(day)
        if day.get("truncated"):
            break
    out["total"] = total
    if any(x.get("truncated") for x in out["days"]):
        out["note"] = (f"Stopped at {limit} matches. Narrow the pattern, pick a file, or use "
                       "count_only to see how many there are.")
    return out


def t_read_log(a: dict) -> dict:
    run, d, date = _day(a.get("device"), a.get("date"), a.get("environment", ""))
    logs, _ = _logs_dir(run, d, date)
    path = _file(logs, a.get("file"))
    start = max(int(a.get("start_line") or 1), 1)
    count = min(max(int(a.get("num_lines") or 200), 1), MAX_READ_LINES)
    lines, n, eof = [], 0, True
    with path.open("r", errors="replace") as fh:
        for n, raw in enumerate(fh, 1):
            if n < start:
                continue
            if len(lines) >= count:
                eof = False
                break
            lines.append(f"{n}\t{_clip(raw)}")
    return {"device": d["device"], "date": date, "file": path.relative_to(logs).as_posix(),
            "start_line": start, "lines": "\n".join(lines), "end_of_file": eof,
            **({"note": f"The file has only {n} lines."} if not lines else {})}


def _epoch_ms(value) -> int:
    raw = str(value or "").strip()
    if not raw:
        raise ToolError("start and end are required (epoch ms, epoch s or ISO time, UTC)")
    if raw.isdigit():
        n = int(raw)
        return n if n > 10 ** 11 else n * 1000
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(f"Can't read the time {raw!r}: use epoch ms, epoch s or ISO (UTC)") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def t_read_log_by_time(a: dict) -> dict:
    run, d, date = _day(a.get("device"), a.get("date"), a.get("environment", ""))
    logs, _ = _logs_dir(run, d, date)
    path = _file(logs, a.get("file"))
    lo, hi = _epoch_ms(a.get("start")), _epoch_ms(a.get("end"))
    if hi < lo:
        lo, hi = hi, lo
    count = min(max(int(a.get("max_lines") or 300), 1), MAX_READ_LINES)
    stamp = re.compile(r"^(\d{12,13})\b")
    lines, inside, more = [], False, False
    with path.open("r", errors="replace") as fh:
        for n, raw in enumerate(fh, 1):
            m = stamp.match(raw)
            if m:
                inside = lo <= int(m.group(1)) <= hi
            if inside:
                if len(lines) >= count:
                    more = True
                    break
                lines.append(f"{n}\t{_clip(raw)}")
    iso = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds")
    return {"device": d["device"], "date": date, "file": path.relative_to(logs).as_posix(),
            "window_utc": [iso(lo), iso(hi)], "lines": "\n".join(lines), "more": more,
            **({"note": "No lines in that window (lines are matched on their leading epoch-ms "
                        "stamp)."} if not lines else {})}


DEVICE = {"type": "string", "description": "Device ID, e.g. 103182502310"}
DATE = {"type": "string", "description": "Day folder, YYYY-MM-DD (the date the logs were uploaded)"}
ENV = {"type": "string", "description": "stag or prod; only needed if the device is in both"}
FILE = {"type": "string", "description": "Log file name from list_log_files, e.g. bagheera.log or syslogs/x.log"}

TOOLS = {
    "scope": (t_scope, "The devices, dates and report files this chat may look at. Call it first.",
              {"type": "object", "properties": {}}),
    "list_log_files": (t_list_log_files,
                       "The log files of one device on one day, with sizes. The day is rebuilt from its "
                       "archives the first time (can take ~30 s), then reused.",
                       {"type": "object", "required": ["device", "date"],
                        "properties": {"device": DEVICE, "date": DATE, "environment": ENV}}),
    "search_logs": (t_search_logs,
                    "grep a device's logs: one day (date), several (dates), or every day with logs "
                    "(omit both). Extended regex by default (fixed_string for plain text), case "
                    "insensitive by default. Returns file, line number and text of each match.",
                    {"type": "object", "required": ["device", "pattern"],
                     "properties": {"device": DEVICE, "pattern": {"type": "string"},
                                    "date": DATE,
                                    "dates": {"type": "array", "items": {"type": "string"}},
                                    "file": FILE,
                                    "files": {"type": "string", "description": "glob over file names, e.g. 'syslogs/*' or '*camera*'"},
                                    "fixed_string": {"type": "boolean"},
                                    "ignore_case": {"type": "boolean", "default": True},
                                    "context": {"type": "integer", "description": "lines before/after each match (0-10)"},
                                    "max_matches": {"type": "integer", "description": f"default 100, at most {MAX_MATCHES}"},
                                    "count_only": {"type": "boolean", "description": "only the number of matches per file"},
                                    "environment": ENV}}),
    "read_log": (t_read_log,
                 f"Read lines of one log file of a device-day, numbered, from start_line (at most {MAX_READ_LINES}).",
                 {"type": "object", "required": ["device", "date", "file"],
                  "properties": {"device": DEVICE, "date": DATE, "file": FILE,
                                 "start_line": {"type": "integer"}, "num_lines": {"type": "integer"},
                                 "environment": ENV}}),
    "read_log_by_time": (t_read_log_by_time,
                         "Read the lines of one log file between two times (matched on each line's leading "
                         "epoch-ms stamp; continuation lines follow their line). Times: epoch ms, epoch s or ISO, UTC.",
                         {"type": "object", "required": ["device", "date", "file", "start", "end"],
                          "properties": {"device": DEVICE, "date": DATE, "file": FILE,
                                         "start": {"type": "string"}, "end": {"type": "string"},
                                         "max_lines": {"type": "integer"}, "environment": ENV}}),
}


def _reply(msg_id, result=None, error=None) -> None:
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if method == "initialize":
            _reply(msg_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                            "serverInfo": {"name": "logs", "version": "1"}})
        elif method == "tools/list":
            _reply(msg_id, {"tools": [{"name": n, "description": desc, "inputSchema": schema}
                                      for n, (_, desc, schema) in TOOLS.items()]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            tool = TOOLS.get(params.get("name"))
            if tool is None:
                _reply(msg_id, error={"code": -32601, "message": f"no tool {params.get('name')!r}"})
                continue
            try:
                payload = tool[0](params.get("arguments") or {})
                _reply(msg_id, {"content": [{"type": "text", "text": json.dumps(payload, indent=1)}]})
            except ToolError as exc:
                _reply(msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            except Exception as exc:  # noqa: BLE001 — a tool failure is the model's to see
                _reply(msg_id, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                                "isError": True})
        elif msg_id is not None:
            _reply(msg_id, error={"code": -32601, "message": f"no method {method!r}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
