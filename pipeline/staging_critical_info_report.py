#!/usr/bin/env python3
"""Generate OTA-specific staging critical-info HTML reports for a date range.

The script accepts explicit start/end dates plus optional OTA and device filters.
When OTA or device filters are omitted, it falls back to CINFO_REPORT and
CINFO_DEVICES from the repo-root .env. One HTML report is written per OTA.
When device IDs are provided, they are used only as an additional filter;
devices on the same OTA remain grouped into the same report.
"""

from __future__ import annotations

import argparse
import html
import os
import pickle
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
import pandas as pd

from fetch_device_config import connect_to_snowflake

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
DB_CREDENTIALS_PATH = REPO_ROOT / "db_credentials.ini"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "OUTPUT" / "staging_critical_info_reports"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "svm_type_classifier.pkl"
SNOWFLAKE_SECTION = "SNOWFLAKE_STAG_DB"
SNOWFLAKE_TABLE = "STAGE_IDMS_MAIN_DB.PUBLIC.DEVICE_CRITICAL_EVENT"
DRIVE_MINUTES_TABLE = "IDMS_DAILY_DEVICE_DRIVE_METRICS_BY_OTA_VERSION_VIEW"

NORMALIZE_DYNAMIC_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
NON_ALPHA_TAIL_RE = re.compile(r" *[^A-Z ].*$")


def _parse_csv_env(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_filters() -> tuple[list[str], list[str]]:
    load_dotenv(str(ENV_PATH), override=False)

    ota_versions = _parse_csv_env(os.environ.get("CINFO_REPORT", ""))
    if not ota_versions:
        raise RuntimeError(
            "CINFO_REPORT is empty. Configure one or more comma-separated OTA versions in .env."
        )

    device_ids = _parse_csv_env(os.environ.get("CINFO_DEVICES", ""))
    return ota_versions, device_ids


def _parse_date_arg(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}: {value!r}. Expected YYYY-MM-DD.") from exc


def _normalize_description(description: object) -> str:
    text = str(description or "").upper().strip()
    text = NORMALIZE_DYNAMIC_RE.sub("%", text)
    text = NON_ALPHA_TAIL_RE.sub("%", text)
    text = re.sub(r"%+", "%", text)
    return text


def _format_timestamp(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return slug.strip("_") or "report"


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _connect_staging_snowflake(aws_profile: str | None):
    conn = connect_to_snowflake(str(DB_CREDENTIALS_PATH), SNOWFLAKE_SECTION, aws_profile=aws_profile)
    if conn is None:
        raise ConnectionError(
            f"Failed to connect to Snowflake using section {SNOWFLAKE_SECTION}"
        )
    return conn


def _load_classifier(model_path: Path = DEFAULT_MODEL_PATH):
    if not model_path.exists():
        raise FileNotFoundError(f"SVM model file not found: {model_path}")
    with model_path.open("rb") as handle:
        return pickle.load(handle)


def _fetch_rows(
    conn,
    ota_version: str,
    device_ids: list[str],
    start_ts: str,
    end_ts: str,
) -> list[dict[str, object]]:
    filters = [
        f"TIMESTAMP >= '{_sql_literal(start_ts)}'",
        f"TIMESTAMP < '{_sql_literal(end_ts)}'",
        f"DEVICE_VERSION = '{_sql_literal(ota_version)}'",
    ]
    if device_ids:
        device_list = ", ".join(f"'{_sql_literal(device_id)}'" for device_id in device_ids)
        filters.append(f"DEVICE_ID IN ({device_list})")

    query = f"""
        SELECT
            DEVICE_ID,
            TIMESTAMP,
            PROCESS_NAME,
            CODE,
            CODE_AUX,
            COUNT,
            DESCRIPTION,
            DEVICE_VERSION
        FROM {SNOWFLAKE_TABLE}
        WHERE {' AND '.join(filters)}
        ORDER BY PROCESS_NAME, CODE, TIMESTAMP DESC
    """

    cursor = conn.cursor()
    try:
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def _fetch_drive_minutes(
    conn,
    device_ids: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, float]:
    if not device_ids:
        return {}

    device_list = ", ".join(f"'{_sql_literal(device_id)}'" for device_id in device_ids)
    query = f"""
        SELECT
            DEVICE_ID,
            SUM(VALID_DRIVE_TIME_IN_MINUTES) AS TOTAL_DRIVE_MINUTES
        FROM {DRIVE_MINUTES_TABLE}
        WHERE DEVICE_ID IN ({device_list})
          AND RECORD_DATE BETWEEN '{_sql_literal(start_date)}' AND '{_sql_literal(end_date)}'
        GROUP BY DEVICE_ID
    """

    cursor = conn.cursor()
    try:
        cursor.execute(query)
        return {
            str(device_id): float(total_drive_minutes or 0)
            for device_id, total_drive_minutes in cursor.fetchall()
        }
    finally:
        cursor.close()


def _classify_severity(predicted_type: object) -> str:
    label = str(predicted_type or "").strip().upper()
    if label == "ERROR":
        return "error"
    if label == "INFO":
        return "info"
    return "info"


def _predict_types(model, rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return []
    features = pd.Series([str(row.get("DESCRIPTION") or "") for row in rows], dtype="string")
    try:
        predicted = model.predict(features)
    except Exception as exc:
        raise RuntimeError(
            "Model prediction failed. The loaded model is likely incompatible with the current "
            "description-only pipeline. Retrain the model with pipeline/svm_type_classifier.py "
            "and rerun the report."
        ) from exc
    if len(predicted) != len(rows):
        raise RuntimeError(
            f"Model returned {len(predicted)} predictions for {len(rows)} rows. "
            "This usually means an incompatible model file is being used."
        )
    return [str(item) for item in predicted]


def _build_report_rows(
    rows: list[dict[str, object]],
    predicted_types: list[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], int, int, int]:
    grouped: dict[tuple[str, str, str, str], dict[str, object]] = {}
    device_versions: dict[str, str] = {}
    devices: set[str] = set()
    error_count = 0
    info_count = 0

    for row, predicted_type in zip(rows, predicted_types, strict=True):
        process_name = str(row.get("PROCESS_NAME") or "")
        code = row.get("CODE")
        code_aux = row.get("CODE_AUX")
        description = str(row.get("DESCRIPTION") or "")
        normalized = _normalize_description(description)
        severity = _classify_severity(predicted_type)
        device_id = str(row.get("DEVICE_ID") or "")
        if device_id:
            devices.add(device_id)
            device_versions.setdefault(device_id, str(row.get("DEVICE_VERSION") or ""))
        if severity == "error":
            error_count += 1
        else:
            info_count += 1

        key = (process_name, str(code), str(code_aux), normalized)
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "process": process_name,
                "code": str(code),
                "code_aux": "" if code_aux is None else str(code_aux),
                "severity": severity,
                "predicted_type": str(predicted_type).strip().upper() or "INFO",
                "normalized_description": normalized,
                "sample_description": description,
                "occurrences": 0,
                "devices": set(),
                "device_timestamps": defaultdict(list),
            }
            grouped[key] = entry
        entry["occurrences"] += int(row.get("COUNT") or 1)
        if device_id:
            entry["devices"].add(device_id)
            timestamp = _format_timestamp(row.get("TIMESTAMP"))
            if timestamp:
                entry["device_timestamps"][device_id].append(timestamp)

    process_summary_map: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "process": "",
            "unique_descriptions": 0,
            "errors": 0,
            "info": 0,
            "total_occurrences": 0,
        }
    )

    detail_rows: list[dict[str, object]] = []
    for entry in grouped.values():
        process_summary = process_summary_map[entry["process"]]
        process_summary["process"] = entry["process"]
        process_summary["unique_descriptions"] += 1
        process_summary[entry["severity"] + "s" if entry["severity"] == "error" else "info"] += 1
        process_summary["total_occurrences"] += entry["occurrences"]
        detail_rows.append(
            {
                **entry,
                "devices": sorted(entry["devices"]),
                "device_timestamps": {
                    device_id: sorted(timestamps, reverse=True)
                    for device_id, timestamps in sorted(entry["device_timestamps"].items())
                },
            }
        )

    process_rows = sorted(
        process_summary_map.values(),
        key=lambda item: (-int(item["errors"]), -int(item["unique_descriptions"]), str(item["process"])),
    )
    detail_rows.sort(
        key=lambda item: (
            item["process"],
            item["code"],
            item["normalized_description"],
        )
    )
    device_rows = [
        {
            "device_id": device_id,
            "ota_version": device_versions.get(device_id, ""),
        }
        for device_id in sorted(devices)
    ]
    return process_rows, detail_rows, device_rows, len(devices), error_count, info_count


def _render_html(
    ota_version: str,
    start_date: str,
    end_date: str,
    generated_at: str,
    total_rows: int,
    process_rows: list[dict[str, object]],
    detail_rows: list[dict[str, object]],
    device_rows: list[dict[str, object]],
    device_count: int,
    error_count: int,
    info_count: int,
    total_drive_minutes: float,
) -> str:
    device_rows_html = []
    for index, row in enumerate(device_rows, start=1):
        device_rows_html.append(
            f"<tr><td class=\"num\">{index}</td>"
            f"<td class=\"mono\">{html.escape(str(row['device_id']))}</td>"
            f"<td>{html.escape(str(row['ota_version']))}</td>"
            f"<td class=\"num mono\">{float(row['drive_minutes']):,.0f}</td></tr>"
        )

    process_rows_html = []
    for index, row in enumerate(process_rows, start=1):
        total = int(row["errors"]) + int(row["info"])
        error_width = round((int(row["errors"]) / total) * 100) if total else 0
        info_width = 100 - error_width if total else 0
        ratio_label = f"{error_width}% err" if total else "0% err"
        process_rows_html.append(
            f"<tr><td class=\"num\">{index}</td>"
            f"<td><span class=\"badge badge-proc\">{html.escape(str(row['process']))}</span></td>"
            f"<td class=\"num mono\">{row['unique_descriptions']}</td>"
            f"<td class=\"num sev-error\">{row['errors']}</td>"
            f"<td class=\"num sev-info\">{row['info']}</td>"
            f"<td class=\"num mono\">{row['total_occurrences']:,}</td>"
            f"<td><div class=\"bar-wrap\"><span class=\"bar bar-error\" style=\"width:{error_width}px\"></span>"
            f"<span class=\"bar bar-info\" style=\"width:{info_width}px\"></span>"
            f"<span class=\"bar-label\">{ratio_label}</span></div></td></tr>"
        )

    detail_rows_html = []
    for index, row in enumerate(detail_rows, start=1):
        device_lists = []
        for device_id in row["devices"]:
            timestamps = row["device_timestamps"].get(device_id, [])
            timestamp_items = "".join(
                f"<li class=\"device-time\">{html.escape(timestamp)}</li>" for timestamp in timestamps
            ) or '<li class="device-time muted">No timestamp</li>'
            device_lists.append(
                f"<div class=\"device-entry\">"
                f"<div class=\"device-id mono\">{html.escape(device_id)}</div>"
                f"<ul class=\"device-time-list\">{timestamp_items}</ul>"
                f"</div>"
            )
        device_tags = "".join(device_lists)
        detail_rows_html.append(
            f"<tr data-proc=\"{html.escape(str(row['process']))}\" data-code=\"{html.escape(str(row['code']))}\" "
            f"data-sev=\"{html.escape(str(row['severity']))}\">"
            f"<td class=\"num\">{index}</td>"
            f"<td><span class=\"badge badge-proc\">{html.escape(str(row['process']))}</span></td>"
            f"<td class=\"mono\">{html.escape(str(row['code']))}</td>"
            f"<td>{html.escape(str(row['code_aux']))}</td>"
            f"<td><span class=\"badge {'badge-error' if row['severity'] == 'error' else 'badge-info'}\">{html.escape(str(row['severity']).upper())}</span></td>"
            f"<td class=\"desc-cell muted\">{html.escape(str(row['sample_description']))}</td>"
            f"<td class=\"num mono\">{int(row['occurrences']):,}</td>"
            f"<td class=\"device-tags\">{device_tags}</td></tr>"
        )

    process_options = "".join(
        f"<option value=\"{html.escape(str(row['process']))}\">{html.escape(str(row['process']))}</option>"
        for row in process_rows
    )
    code_options = "".join(
        f"<option value=\"{html.escape(str(code))}\">{html.escape(str(code))}</option>"
        for code in sorted({row['code'] for row in detail_rows}, key=str)
    )

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1.0\">
<title>Unique Critical Info Report</title>
<style>
:root {{
    --bg:#eef1f6;--panel:#ffffff;--ink:#1e293b;--muted:#64748b;--line:#e2e8f0;
    --accent:#2563eb;--pass:#16a34a;--fail:#dc2626;--warn:#d97706;--soft:#f8fafc;
}}
* {{ margin:0; padding:0; box-sizing:border-box }}
body {{ font-family:'Segoe UI',system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--ink); font-size:14px; line-height:1.5 }}
.top {{ background:linear-gradient(120deg,#0f172a,#1e3a5f 60%,#2563eb); color:#fff; padding:26px 0 0 }}
.top-inner {{ max-width:1400px; margin:0 auto; padding:0 24px }}
.top h1 {{ font-size:22px; font-weight:700 }}
.top .meta {{ font-size:12.5px; opacity:.85; margin-top:6px; display:flex; flex-wrap:wrap; gap:16px }}
.top .meta b {{ opacity:.7; font-weight:600 }}
.tabs {{ display:flex; gap:4px; margin-top:20px }}
.tab-btn {{ padding:11px 22px; font-size:13.5px; font-weight:600; color:rgba(255,255,255,.65); cursor:pointer; border:none; background:transparent; border-bottom:3px solid transparent; border-radius:8px 8px 0 0 }}
.tab-btn:hover {{ color:#fff; background:rgba(255,255,255,.08) }}
.tab-btn.active {{ color:#0f172a; background:var(--bg); border-bottom-color:transparent }}
.tab-btn .cnt {{ display:inline-block; margin-left:6px; font-size:11px; padding:1px 7px; border-radius:10px; background:rgba(255,255,255,.2) }}
.tab-btn.active .cnt {{ background:var(--line); color:var(--ink) }}
.wrap {{ max-width:1400px; margin:0 auto; padding:20px 24px 60px }}
.tab-content {{ display:none }} .tab-content.active {{ display:block }}
.kpis {{ display:grid; grid-template-columns:repeat(5,1fr); gap:14px; margin-bottom:22px }}
.kpi {{ background:var(--panel); border-radius:12px; padding:16px 18px; box-shadow:0 1px 6px rgba(0,0,0,.06); border-top:3px solid var(--accent) }}
.kpi-label {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.8px; color:var(--muted); font-weight:700 }}
.kpi-value {{ font-size:26px; font-weight:800; margin-top:6px; color:var(--ink) }}
.kpi-sub {{ font-size:11px; color:var(--muted); margin-top:3px }}
.k-total {{ border-top-color:var(--accent) }}
.k-unique {{ border-top-color:var(--pass) }}
.k-error {{ border-top-color:var(--fail) }}
.k-info {{ border-top-color:var(--pass) }}
.k-dev {{ border-top-color:var(--warn) }}
.card {{ background:var(--panel); border-radius:12px; box-shadow:0 1px 6px rgba(0,0,0,.06); overflow:hidden; margin-bottom:20px }}
.card-h {{ padding:14px 18px; font-weight:700; font-size:14px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center }}
.scroll-table {{ max-height:700px; overflow:auto; position:relative }}
table {{ width:100%; border-collapse:collapse; font-size:12.5px }}
th {{ text-align:left; padding:9px 12px; background:var(--soft); color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.5px; border-bottom:1px solid var(--line); position:sticky; top:0; z-index:2 }}
td {{ padding:9px 12px; border-bottom:1px solid #f1f5f9; vertical-align:middle }}
tr:hover > td {{ background:#fafcff }}
.num {{ text-align:right; font-variant-numeric:tabular-nums }}
.mono {{ font-family:ui-monospace,'SF Mono',Menlo,monospace; font-size:11.5px; font-weight:600 }}
.muted {{ color:var(--muted) }}
.badge {{ display:inline-block; font-size:10px; padding:2px 8px; border-radius:6px; font-weight:700 }}
.badge-proc {{ background:#dbeafe; color:#1e40af }}
.badge-error {{ background:#fef2f2; color:#dc2626 }}
.badge-info {{ background:#f0fdf4; color:#16a34a }}
.sev-error {{ color:var(--fail); font-weight:700 }}
.sev-info {{ color:var(--pass); font-weight:700 }}
.desc-cell {{ max-width:420px; word-break:break-word }}
.device-tags {{ min-width:280px; max-width:360px }}
.device-entry {{ background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:8px; margin-bottom:8px }}
.device-entry:last-child {{ margin-bottom:0 }}
.device-id {{ color:#166534; margin-bottom:6px }}
.device-time-list {{ list-style:none; margin:0; padding:0; max-height:96px; overflow:auto; border-top:1px solid #e2e8f0 }}
.device-time {{ font-size:11px; padding:4px 0; border-bottom:1px solid #eef2f7 }}
.device-time:last-child {{ border-bottom:none }}
.filter-bar {{ padding:12px 18px; display:flex; gap:12px; align-items:center; border-bottom:1px solid var(--line); flex-wrap:wrap }}
.filter-bar input, .filter-bar select {{ padding:6px 10px; border:1px solid var(--line); border-radius:6px; font-size:12px }}
.filter-bar input {{ width:260px }}
.filter-bar select {{ min-width:140px }}
.bar-wrap {{ display:flex; gap:2px; align-items:center }}
.bar {{ height:8px; border-radius:4px; display:inline-block }}
.bar-error {{ background:var(--fail) }}
.bar-info {{ background:var(--pass) }}
.bar-label {{ font-size:10px; color:var(--muted); margin-left:6px }}
.empty-state {{ padding:24px 18px; color:var(--muted); font-size:13px }}
</style>
</head>
<body>
<div class=\"top\">
 <div class=\"top-inner\">
  <h1>Unique Critical Info Report</h1>
  <div class=\"meta\">
   <span><b>Source:</b> Snowflake staging critical info</span>
   <span><b>Generated:</b> {html.escape(generated_at)}</span>
   <span><b>Date Range:</b> {html.escape(start_date)} to {html.escape(end_date)}</span>
   <span><b>OTA:</b> {html.escape(ota_version)}</span>
  </div>
  <div class=\"tabs\">
    <button class=\"tab-btn active\" onclick=\"switchTab(event, 'devices')\">Devices & Drive Minutes<span class=\"cnt\">{len(device_rows)}</span></button>
    <button class=\"tab-btn\" onclick=\"switchTab(event, 'summary')\">Process Summary<span class=\"cnt\">{len(process_rows)}</span></button>
   <button class=\"tab-btn\" onclick=\"switchTab(event, 'detail')\">Unique Critical Info<span class=\"cnt\">{len(detail_rows)}</span></button>
  </div>
 </div>
</div>
<div class=\"wrap\">
 <div class=\"kpis\">
  <div class=\"kpi k-total\"><div class=\"kpi-label\">Total Rows</div><div class=\"kpi-value\">{total_rows:,}</div></div>
  <div class=\"kpi k-unique\"><div class=\"kpi-label\">Unique Entries</div><div class=\"kpi-value\">{len(detail_rows)}</div><div class=\"kpi-sub\">After normalisation</div></div>
  <div class=\"kpi k-error\"><div class=\"kpi-label\">Errors</div><div class=\"kpi-value\">{error_count:,}</div></div>
  <div class=\"kpi k-info\"><div class=\"kpi-label\">Info</div><div class=\"kpi-value\">{info_count:,}</div></div>
  <div class=\"kpi k-dev\"><div class=\"kpi-label\">Devices</div><div class=\"kpi-value\">{device_count}</div></div>
 </div>
 <div id=\"tab-devices\" class=\"tab-content active\">
  <div class=\"card\">
   <div class=\"card-h\"><span>Device OTA and Drive Minutes</span><span class=\"muted\" style=\"font-weight:400;font-size:12px\">{len(device_rows)} devices | {total_drive_minutes:,.0f} total drive minutes</span></div>
   <div class=\"scroll-table\">
    <table>
     <thead><tr><th>#</th><th>Device ID</th><th>OTA</th><th class=\"num\">Drive Minutes</th></tr></thead>
     <tbody>
     {''.join(device_rows_html) if device_rows_html else '<tr><td colspan="4" class="empty-state">No device rows found for the selected OTA/date range.</td></tr>'}
     </tbody>
    </table>
   </div>
  </div>
 </div>
 <div id=\"tab-summary\" class=\"tab-content\">
  <div class=\"card\">
   <div class=\"card-h\"><span>Process Summary</span><span class=\"muted\" style=\"font-weight:400;font-size:12px\">{len(process_rows)} processes</span></div>
   <div class=\"scroll-table\">
    <table>
     <thead><tr><th>#</th><th>Process</th><th class=\"num\">Unique Descriptions</th><th class=\"num\">Errors</th><th class=\"num\">Info</th><th class=\"num\">Total Occurrences</th><th>Error / Info Ratio</th></tr></thead>
     <tbody>
     {''.join(process_rows_html)}
     </tbody>
    </table>
   </div>
  </div>
 </div>
 <div id=\"tab-detail\" class=\"tab-content\">
  <div class=\"card\">
   <div class=\"card-h\">
    <span>Unique Critical Info (grouped by Process + Code + Normalised Description)</span>
    <span class=\"muted\" style=\"font-weight:400;font-size:12px\">{len(detail_rows)} entries</span>
   </div>
   <div class=\"filter-bar\">
    <input type=\"text\" id=\"searchBox\" placeholder=\"Filter descriptions…\" oninput=\"filterTable()\">
    <select id=\"procFilter\" onchange=\"filterTable()\"><option value=\"\">All Processes</option>{process_options}</select>
    <select id=\"codeFilter\" onchange=\"filterTable()\"><option value=\"\">All Codes</option>{code_options}</select>
    <select id=\"sevFilter\" onchange=\"filterTable()\"><option value=\"\">All Severity</option><option value=\"error\">Error</option><option value=\"info\">Info</option></select>
   </div>
   <div class=\"scroll-table\">
    <table id=\"mainTable\">
    <thead><tr><th>#</th><th>Process</th><th>Code</th><th>Code Aux</th><th>Severity</th><th>Sample Description</th><th class=\"num\">Occurrences</th><th>Devices</th></tr></thead>
     <tbody>
     {''.join(detail_rows_html)}
     </tbody>
    </table>
   </div>
  </div>
 </div>
</div>
<script>
function switchTab(evt, name) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  evt.currentTarget.classList.add('active');
}}
function filterTable() {{
  const search = document.getElementById('searchBox').value.toLowerCase();
  const proc = document.getElementById('procFilter').value;
  const code = document.getElementById('codeFilter').value;
  const sev = document.getElementById('sevFilter').value;
  const rows = document.querySelectorAll('#mainTable tbody tr');
  let idx = 0;
  rows.forEach(row => {{
    const pv = row.dataset.proc || '';
    const cv = row.dataset.code || '';
    const sv = row.dataset.sev || '';
    const text = row.textContent.toLowerCase();
    const show = (!search || text.includes(search)) && (!proc || pv === proc) && (!code || cv === code) && (!sev || sv === sev);
    row.style.display = show ? '' : 'none';
    if (show) {{ idx++; row.cells[0].textContent = idx; }}
  }});
}}
</script>
</body>
</html>
"""


def generate_reports(
    output_dir: Path,
    start_date: date,
    end_date: date,
    aws_profile: str | None = None,
    ota_versions: list[str] | None = None,
    device_ids: list[str] | None = None,
) -> list[Path]:
    env_ota_versions, env_device_ids = _load_filters()
    ota_versions = ota_versions if ota_versions is not None else env_ota_versions
    device_ids = device_ids if device_ids is not None else env_device_ids
    output_dir.mkdir(parents=True, exist_ok=True)

    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    start_ts = f"{start_date.isoformat()} 00:00:00"
    end_ts = f"{(end_date + timedelta(days=1)).isoformat()} 00:00:00"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    model = _load_classifier()

    conn = _connect_staging_snowflake(aws_profile)
    try:
        written_files: list[Path] = []
        for ota_version in ota_versions:
            rows = _fetch_rows(conn, ota_version, device_ids, start_ts, end_ts)
            predicted_types = _predict_types(model, rows)
            process_rows, detail_rows, device_rows, device_count, error_count, info_count = _build_report_rows(
                rows,
                predicted_types,
            )
            drive_minutes_by_device = _fetch_drive_minutes(
                conn,
                [row["device_id"] for row in device_rows],
                start_date.isoformat(),
                end_date.isoformat(),
            )
            for row in device_rows:
                row["drive_minutes"] = drive_minutes_by_device.get(str(row["device_id"]), 0.0)
            device_rows.sort(
                key=lambda item: (-float(item["drive_minutes"]), str(item["device_id"]))
            )
            report_html = _render_html(
                ota_version=ota_version,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                generated_at=generated_at,
                total_rows=len(rows),
                process_rows=process_rows,
                detail_rows=detail_rows,
                device_rows=device_rows,
                device_count=device_count,
                error_count=error_count,
                info_count=info_count,
                total_drive_minutes=sum(float(row["drive_minutes"]) for row in device_rows),
            )
            output_path = output_dir / (
                f"staging_critical_info_{_slugify(ota_version)}_"
                f"{start_date.isoformat()}_{end_date.isoformat()}.html"
            )
            output_path.write_text(report_html, encoding="utf-8")
            written_files.append(output_path)
        return written_files
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        default=None,
        help="Inclusive start date in YYYY-MM-DD format. Defaults to yesterday.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Inclusive end date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--deviceid",
        default=None,
        help="Optional comma-separated device IDs. Falls back to CINFO_DEVICES in .env.",
    )
    parser.add_argument(
        "--ota",
        default=None,
        help="Optional comma-separated OTA versions. Falls back to CINFO_REPORT in .env.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where OTA-specific HTML reports will be written.",
    )
    parser.add_argument(
        "--aws-profile",
        default=None,
        help="Optional AWS profile for Snowflake private-key lookup.",
    )
    args = parser.parse_args()

    today = date.today()
    default_start = today - timedelta(days=1)
    start_date = _parse_date_arg(args.start_date, "start-date") if args.start_date else default_start
    end_date = _parse_date_arg(args.end_date, "end-date") if args.end_date else today
    device_ids = _parse_csv_env(args.deviceid) if args.deviceid else None
    ota_versions = _parse_csv_env(args.ota) if args.ota else None

    written_files = generate_reports(
        Path(args.output_dir),
        start_date=start_date,
        end_date=end_date,
        aws_profile=args.aws_profile,
        ota_versions=ota_versions,
        device_ids=device_ids,
    )
    for path in written_files:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())