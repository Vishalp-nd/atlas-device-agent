#!/usr/bin/env python3
"""Select devices/dates for each ERROR-severity row in a staging critical-info
report, download their logs via lib/downloader.py, write a JSON map of every
ERROR row (CODE/CODE_AUX/PROCESS/Sample_Description), then hand off to the
bug-report agent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
LIB_ROOT = REPO_ROOT / "lib"
DEFAULT_REPORT_DIR = REPO_ROOT / "OUTPUT" / "staging_critical_info_reports"
DEFAULT_MAP_DIR = REPO_ROOT / "OUTPUT" / "critical_bug_prep"
DEFAULT_AGENT_SCRIPT = REPO_ROOT / "atlas" / "critical_bug_report_agent.py"
DEFAULT_MAILER_SCRIPT = REPO_ROOT / "lib" / "mailer.py"
DEFAULT_BUGS_DIR = REPO_ROOT / "OUTPUT" / "critical_bugs"
MAX_DEVICES_PER_ROW = 2
DEFAULT_AGENT_BATCH_SIZE = 10
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"

if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))
from downloader import Downloader  # noqa: E402


def _numeric_or_str(text: str):
    text = text.strip()
    try:
        return int(float(text))
    except ValueError:
        return text


def _find_latest_report(report_dir: Path) -> Path:
    candidates = sorted(report_dir.glob("staging_critical_info_*.html"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"No staging_critical_info_*.html files found under {report_dir}")
    return candidates[-1]


def _extract_ota_from_filename(report_path: Path) -> str:
    match = re.search(r"staging_critical_info_(.+?)_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}$", report_path.stem)
    if match:
        return match.group(1)
    return ""


def _extract_ota_from_report(report_path: Path) -> str:
    soup = BeautifulSoup(report_path.read_text(encoding="utf-8"), "html.parser")
    meta = soup.find("div", class_="meta")
    if meta is not None:
        for span in meta.find_all("span"):
            text = span.get_text(" ", strip=True)
            match = re.match(r"OTA:\s*(.+)$", text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    ota_from_filename = _extract_ota_from_filename(report_path)
    if ota_from_filename:
        return ota_from_filename
    return os.environ.get("CINFO_REPORT", "").strip()


def _parse_report(report_path: Path) -> list[dict]:
    soup = BeautifulSoup(report_path.read_text(encoding="utf-8"), "html.parser")
    table = soup.find("table", id="mainTable")
    if table is None:
        raise ValueError(f"Could not find the Unique Critical Info table (id=mainTable) in {report_path}")

    rows = []
    for tr in table.find("tbody").find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        devices = []
        for entry in tds[7].find_all("div", class_="device-entry", recursive=False):
            device_id = entry.find("div", class_="device-id").get_text(strip=True)
            timestamps = []
            for li in entry.find("ul", class_="device-time-list").find_all("li"):
                text = li.get_text(strip=True)
                try:
                    datetime.strptime(text, TIMESTAMP_FMT)
                except ValueError:
                    continue
                timestamps.append(text)
            devices.append({"device_id": device_id, "timestamps": timestamps})
        rows.append({
            "process": tr.get("data-proc", ""),
            "code": _numeric_or_str(tr.get("data-code", "")),
            "code_aux": _numeric_or_str(tds[3].get_text(strip=True)),
            "severity": tr.get("data-sev", "").strip().upper(),
            "sample_description": tds[5].get_text(strip=True),
            "occurrences": int(tds[6].get_text(strip=True).replace(",", "") or 0),
            "devices": devices,
        })
    return rows


def _build_entries(error_rows: list[dict], max_devices: int) -> tuple[list[dict], set[tuple[str, str]]]:
    entries: list[dict] = []
    pairs: set[tuple[str, str]] = set()
    for row in error_rows:
        targets = []
        for device in row["devices"][:max_devices]:
            if not device["timestamps"]:
                continue
            most_recent = device["timestamps"][0]
            day = datetime.strptime(most_recent, TIMESTAMP_FMT).date()
            dates = [day.isoformat(), (day + timedelta(days=1)).isoformat()]
            for d in dates:
                pairs.add((device["device_id"], d))
            targets.append({
                "device_id": device["device_id"],
                "reference_timestamp": most_recent,
                "dates": dates,
                "downloaded": False,
            })
        entries.append({
            "process": row["process"],
            "code": row["code"],
            "code_aux": row["code_aux"],
            "severity": row["severity"],
            "sample_description": row["sample_description"],
            "occurrences": row["occurrences"],
            "devices_in_report": [d["device_id"] for d in row["devices"]],
            "download_targets": targets,
        })
    return entries, pairs


def _download_pairs(pairs: set[tuple[str, str]], count_only: bool) -> dict[tuple[str, str], bool]:
    downloader = Downloader(server="stag", filetype="log", dd=False, count=count_only)
    results: dict[tuple[str, str], bool] = {}
    for device_id, date_str in sorted(pairs):
        downloader.bucket = downloader.bt3_client.Bucket(downloader.server)
        try:
            downloader.download_content(device_id, date_str)
            results[(device_id, date_str)] = True
        except Exception as exc:
            print(f"[-] download failed for {device_id}/{date_str}: {exc}")
            results[(device_id, date_str)] = False
        finally:
            downloader.bucket = None
    return results


def _apply_download_results(entries: list[dict], results: dict[tuple[str, str], bool]) -> None:
    for entry in entries:
        for target in entry["download_targets"]:
            target["downloaded"] = all(
                results.get((target["device_id"], d), False) for d in target["dates"]
            )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=None, help="Path to a staging_critical_info_*.html report. Defaults to the newest one under --report-dir.")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Directory to search for the newest report when --report is omitted.")
    parser.add_argument("--map-dir", default=str(DEFAULT_MAP_DIR), help="Directory to write the JSON map into.")
    parser.add_argument("--max-devices", type=int, default=MAX_DEVICES_PER_ROW, help="Max devices to select per ERROR row.")
    parser.add_argument("--no-download", action="store_true", help="Parse/select/write the map only; skip S3 entirely.")
    parser.add_argument("--count", action="store_true", help="List matching S3 objects without downloading (real S3 dry-run).")
    parser.add_argument("--skip-agent", action="store_true", help="Build the map (and download, unless --no-download/--count) but don't invoke the bug-report agent.")
    parser.add_argument("--agent-script", default=str(DEFAULT_AGENT_SCRIPT), help="Path to the bug-report agent script to invoke once downloads finish.")
    parser.add_argument("--agent-batch-size", type=int, default=DEFAULT_AGENT_BATCH_SIZE, help="Number of ERROR entries to analyze per bug-agent run.")
    parser.add_argument("--skip-email", action="store_true", help="Don't email the generated bug report once the agent finishes.")
    parser.add_argument("--mailer-script", default=str(DEFAULT_MAILER_SCRIPT), help="Path to the mailer script to invoke once the bug report is generated.")
    return parser.parse_args()


def _resolve_output_path(stdout: str, fallback: Path) -> Path:
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if candidate.endswith(".md"):
            return Path(candidate)
    return fallback


def _count_error_entries(map_path: Path) -> int:
    data = json.loads(map_path.read_text(encoding="utf-8"))
    return sum(1 for entry in data.get("entries", []) if entry.get("severity") == "ERROR")


def main() -> int:
    args = _parse_args()
    load_dotenv(str(ENV_PATH), override=False)
    os.chdir(REPO_ROOT)

    report_path = Path(args.report) if args.report else _find_latest_report(Path(args.report_dir))
    rows = _parse_report(report_path)
    error_rows = [r for r in rows if r["severity"] == "ERROR"]
    entries, pairs = _build_entries(error_rows, max_devices=args.max_devices)

    dry_run = args.no_download or args.count
    if dry_run:
        results = _download_pairs(pairs, count_only=True) if args.count else {}
    else:
        results = _download_pairs(pairs, count_only=False)
    _apply_download_results(entries, results)

    map_dir = Path(args.map_dir)
    map_dir.mkdir(parents=True, exist_ok=True)
    map_path = map_dir / f"{report_path.stem}_map.json"
    map_path.write_text(json.dumps({
        "report": report_path.name,
        "ota_version": _extract_ota_from_report(report_path),
        "generated_at": datetime.now().strftime(TIMESTAMP_FMT),
        "entries": entries,
    }, indent=2), encoding="utf-8")
    print(map_path)

    if args.skip_agent or dry_run:
        return 0

    bugs_path = DEFAULT_BUGS_DIR / f"{report_path.stem}_bugs.md"
    total_entries = _count_error_entries(map_path)
    batch_size = max(1, args.agent_batch_size)
    for start_index in range(0, total_entries, batch_size):
        agent_command = [
            sys.executable, str(Path(args.agent_script)),
            "--map", str(map_path),
            "--output", str(bugs_path),
            "--start-index", str(start_index),
            "--limit", str(batch_size),
        ]
        completed = subprocess.run(agent_command, cwd=REPO_ROOT, capture_output=True, text=True)
        if completed.stdout:
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="" if completed.stderr.endswith("\n") else "\n")
        if completed.returncode != 0:
            return completed.returncode
        bugs_path = _resolve_output_path(completed.stdout, bugs_path)

    index_command = [
        sys.executable, str(Path(args.agent_script)),
        "--map", str(map_path),
        "--output", str(bugs_path),
        "--jira-index-only",
    ]
    completed = subprocess.run(index_command, cwd=REPO_ROOT, capture_output=True, text=True)
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    if completed.returncode != 0:
        return completed.returncode
    bugs_path = _resolve_output_path(completed.stdout, bugs_path)

    if not bugs_path.is_file():
        print(
            f"Bug report agent completed without creating markdown output. Expected {bugs_path}",
            file=sys.stderr,
        )
        return 1

    if args.skip_email:
        return 0

    mailer_command = [
        sys.executable, str(Path(args.mailer_script)),
        "--attachment", str(bugs_path), str(report_path),
        "--subject", f"Critical Bug Report: {report_path.stem}",
    ]
    completed = subprocess.run(mailer_command, cwd=REPO_ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
