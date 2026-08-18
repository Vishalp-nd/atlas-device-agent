#!/usr/bin/env python3
"""Analyze downloaded device logs for each ERROR-severity entry produced by
pipeline/critical_bug_prep.py and write one markdown bug report per entry,
using an Azure OpenAI tool-calling agent that greps service_mon.log and the
relevant process log(s).
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
DEFAULT_LOG_ROOT = REPO_ROOT / "OUTPUT" / "log"
DEFAULT_BUGS_DIR = REPO_ROOT / "OUTPUT" / "critical_bugs"
MAX_ITERATIONS = 10

load_dotenv(str(ENV_PATH), override=False)

SYSTEM_PROMPT = """You are a device-log bug analyst for Netradyne's Atlas platform.

You are given ONE unique critical-info entry (process, code, code_aux, sample description,
occurrence count, and a short list of devices with the date(s) on which that entry was most
recently observed, plus the following calendar day). Device logs for those device/date pairs
have already been downloaded and merged per process under:

    <log_root>/<device_id>/<date>/logs/<process>.log

Investigate using list_process_logs and grep_log:
1. For each device/date pair, call list_process_logs to see what's available.
2. grep service_mon.log for the CODE (and CODE_AUX if present) to find surrounding context -
   service start/stop lines often name a process and hint at what to check next.
3. Follow those hints into the process-specific log (e.g. if PROCESS is GPS, check gps.log)
   and any other log that seems relevant, to build a chronological picture of what happened
   before, during, and after the error.
4. Every claim in your Steps/Flow section must be backed by a quoted log line.

Once you have enough evidence (or you run out of tool budget), respond with prose only
(no further tool calls) containing EXACTLY one markdown bug entry with these sections, in
this exact order and format:

## [<Process name>] <short bug title>

**Description:** <one-line description>

**Impact:** <impact of this bug>

**Recovery:** <how/whether the device recovers>

**Steps/Flow:**
1. <step> - `<quoted log line>`
2. <step> - `<quoted log line>`

**Occurrence Rate:** <occurrence count / rate, referencing the report's occurrence count>

**Device Details:**
- `<device_id>` 
- <dates analyzed> 
-most recent occurrence: <timestamp>
- OTA version

Add a final line starting with `Note:` ONLY if the evidence suggests this is not really a
bug - e.g. the device recovered on its own, or the "error" is expected/benign behavior.
Omit the Note line entirely otherwise. Do not include any text before the `## [` heading or
after the closing section (or Note line, if present).
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_process_logs",
            "description": "List the per-process log filenames available for a given device and date. Use this to discover which logs exist (e.g. service_mon.log, gps.log, apm.log) before grepping them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Device ID, e.g. 105222600004"},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                },
                "required": ["device_id", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_log",
            "description": "Search a specific per-process log file for a device+date for a text/regex pattern and return matching lines with surrounding context. Start with service_mon.log and the CODE/CODE_AUX, then follow hints (process names, keywords) into the process-specific log (e.g. gps.log for GPS) and any other log that seems relevant, to build a chronological picture.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "filename": {"type": "string", "description": "Log filename, e.g. service_mon.log. Must be one of the files returned by list_process_logs."},
                    "pattern": {"type": "string", "description": "Text or regex to search for, e.g. the CODE or a keyword."},
                    "context_lines": {"type": "integer", "description": "Lines of context before/after each match.", "default": 3},
                    "max_matches": {"type": "integer", "description": "Max matches to return (capped at 50).", "default": 20},
                },
                "required": ["device_id", "date", "filename", "pattern"],
            },
        },
    },
]


def list_process_logs(log_root: Path, device_id: str, date: str) -> str:
    log_dir = log_root / device_id / date / "logs"
    if not log_dir.is_dir():
        return f"No log directory found for device {device_id} on {date} (expected {log_dir}). It may not have been downloaded, or no data exists for that day."
    files = sorted(p.name for p in log_dir.iterdir() if p.is_file())
    return json.dumps({"device_id": device_id, "date": date, "log_dir": str(log_dir), "files": files})


def grep_log(log_root: Path, device_id: str, date: str, filename: str, pattern: str,
             context_lines: int = 3, max_matches: int = 20) -> str:
    safe_name = os.path.basename(filename)
    if safe_name != filename or ".." in filename:
        return f"grep_log failed: invalid filename {filename!r}"
    log_path = log_root / device_id / date / "logs" / safe_name
    if not log_path.is_file():
        return f"grep_log failed: {log_path} does not exist. Call list_process_logs first to see available files."
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        regex = re.compile(re.escape(pattern), re.IGNORECASE)
    max_matches = min(max_matches, 50)
    lines = log_path.read_text(encoding="iso8859-15", errors="replace").splitlines()
    blocks, total = [], 0
    for i, line in enumerate(lines):
        if regex.search(line):
            total += 1
            if len(blocks) < max_matches:
                lo, hi = max(0, i - context_lines), min(len(lines), i + context_lines + 1)
                context = [(">>> " if j == i else "    ") + lines[j] for j in range(lo, hi)]
                blocks.append(f"[line {i + 1}]\n" + "\n".join(context))
    if total == 0:
        return f"No matches for {pattern!r} in {log_path}."
    header = f"{total} match(es) for {pattern!r} in {log_path}" + (f" (showing first {len(blocks)})" if total > len(blocks) else "") + ":\n\n"
    return header + "\n\n".join(blocks)


def _dispatch_tool(call, log_root: Path) -> str:
    try:
        args = json.loads(call.function.arguments)
        if call.function.name == "list_process_logs":
            return list_process_logs(log_root, args["device_id"], args["date"])
        if call.function.name == "grep_log":
            return grep_log(
                log_root, args["device_id"], args["date"], args["filename"], args["pattern"],
                context_lines=args.get("context_lines", 3), max_matches=args.get("max_matches", 20),
            )
        return f"Unknown tool {call.function.name!r}"
    except Exception as exc:
        return f"{call.function.name} failed: {exc}"


def _build_user_prompt(entry: dict) -> str:
    targets = "\n".join(
        f"  - device {t['device_id']}: dates {t['dates']}, most recent occurrence {t['reference_timestamp']}, downloaded={t['downloaded']}"
        for t in entry["download_targets"]
    ) or "  (no download targets were available for this entry)"
    return (
        f"Process: {entry['process']}\n"
        f"Code: {entry['code']}\n"
        f"Code Aux: {entry['code_aux']}\n"
        f"Severity: {entry['severity']}\n"
        f"Sample description: {entry['sample_description']}\n"
        f"Total occurrences (report window): {entry['occurrences']}\n"
        f"Devices in report: {', '.join(entry['devices_in_report'])}\n"
        f"Download targets to investigate:\n{targets}"
    )


def _get_client() -> tuple[AzureOpenAI, str]:
    client = AzureOpenAI(
        api_key=os.environ.get("AZURE_OAI", "").strip(),
        azure_endpoint=os.environ.get("OAI_ENDPOINT", "").strip(),
        api_version=os.environ.get("OAI_API_VERSION", "2024-08-01-preview").strip(),
    )
    deployment = os.environ.get("OAI_MODEL", "").strip()
    return client, deployment


def analyze_entry(client: AzureOpenAI, deployment: str, entry: dict, log_root: Path,
                   max_iterations: int = MAX_ITERATIONS) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(entry)},
    ]
    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=deployment, messages=messages, tools=TOOLS, tool_choice="auto", temperature=0.2,
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        if not message.tool_calls:
            return message.content or ""
        for call in message.tool_calls:
            result = _dispatch_tool(call, log_root)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    messages.append({
        "role": "user",
        "content": "Iteration limit reached. Produce your best bug report now, in the exact markdown format requested, using only the evidence already gathered.",
    })
    final = client.chat.completions.create(model=deployment, messages=messages, temperature=0.2)
    return final.choices[0].message.content or ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True, help="Path to the JSON map produced by pipeline/critical_bug_prep.py.")
    parser.add_argument("--output", default=None, help="Output markdown path. Defaults to OUTPUT/critical_bugs/<report_stem>_bugs.md.")
    parser.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT), help="Root directory containing <device_id>/<date>/logs/ dirs.")
    parser.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS, help="Max tool-calling iterations per entry.")
    parser.add_argument("--only", default=None, help="Limit analysis to entries whose code matches this value (for prompt iteration).")
    return parser.parse_args()


def _section_heading(section: str) -> str:
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("## "):
            return line[3:].strip()
    return "(untitled)"


def _classify_section(section: str) -> str:
    if "ANALYSIS FAILED" in section:
        return "Needs Review"
    for line in section.splitlines():
        if line.strip().startswith("Note:"):
            return "Needs Review"
    return "Bug"


def _build_index(sections: list[str]) -> str:
    rows = "\n".join(
        f"| {i} | {_section_heading(section)} | {_classify_section(section)} |"
        for i, section in enumerate(sections, start=1)
    )
    return f"## Index\n\n| Sl No | Bug | Classification |\n|---|---|---|\n{rows}\n"


def main() -> int:
    args = _parse_args()
    data = json.loads(Path(args.map).read_text(encoding="utf-8"))
    entries = [e for e in data["entries"] if e["severity"] == "ERROR"]
    if args.only:
        entries = [e for e in entries if str(e["code"]) == args.only]

    log_root = Path(args.log_root)
    output_path = Path(args.output) if args.output else DEFAULT_BUGS_DIR / f"{Path(data['report']).stem}_bugs.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client, deployment = _get_client()

    sections = []
    for entry in entries:
        try:
            sections.append(analyze_entry(client, deployment, entry, log_root, args.max_iterations).strip())
        except Exception as exc:
            sections.append(f"## [{entry['process']}] ANALYSIS FAILED\n\nAgent error: {exc}")

    header = f"# Critical Bug Report - {data['report']}\nGenerated: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
    index = _build_index(sections)
    body = "\n\n---\n\n".join(sections)
    output_path.write_text(f"{header}\n{index}\n---\n\n{body}\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
