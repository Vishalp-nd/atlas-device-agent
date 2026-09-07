#!/usr/bin/env python3
"""Generate GPS Performance Excel reports for installed OCTA devices from ClickHouse.

Emits four workbooks per run:

  1. GPS Observation Report - Condition 1  (ignition ON and uptime > 300 s)
  2. GPS Observation Report - Condition 2  (ignition ON and max speed > 5 mph)
  3. GPS Observation Report - Condition 3  (ignition ON)
  4. GPS Summary Report                    (one row per video file)

Reports 1-3 are aggregated per device for the requested date range. Report 4
is one row per observation/video file, with the GPS
samples inside each file collapsed into counts, joined value lists, a mean and
the first valid fix.

Data comes from the ClickHouse `observation_data` and `video_metadata` tables
(see clickhouse_schema.sql), joined on file_name. Device scope and tenant
display names come from OUTPUT/octo/<ota>/polling/<date>/device_data_<ota>.csv,
produced by pipeline/data_polling.py.

Note: --end is EXCLUSIVE. A 7-day week is --start 2026-06-15 --end 2026-06-22.

Usage:
    python scripts/gps_performance_report.py --start 2026-06-15 --end 2026-06-22
    python scripts/gps_performance_report.py --start 2026-06-15 --end 2026-06-16 \
        --devices 125072600091 --filename QA_Smoke.xlsx
"""

from __future__ import annotations

import argparse
import csv
import glob
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib.logger import Logger

logger = Logger("gps_performance_report")

DEFAULT_CLICKHOUSE_SECTION = "CLICKHOUSE_DB"
DEFAULT_DEVICE_LIST_ROOT = REPO_ROOT / "OUTPUT" / "octo"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "OUTPUT" / "gps_performance_reports"
INSTALLED_STATE = "INSTALLED"
DEVICE_CHUNK = 200

# A device that never acquired a fix writes impossible lat/long (91 / 181) into
# its filename. Established marker; see atlas/gps_oh_summary_generator.py:469.
NO_GPS_SENTINEL = "_91.0000_181.0000_"

NO_GPS_LABEL = "No GPS"

OBS_COLUMNS = [
    "Given Date Range",
    "Device ID",
    "Tenant Display Name",
    "OTA Version",
    "ObsCount",
    "0-2 m",
    "0-3.5 m",
    "0-6 m",
    "0-10 m",
    "Above 10 m",
    "GPS Loss %",
    "No GPS %",
    "Expected Accuracy Count",
    "Actual Accuracy Count",
    "Invalid Accuracy Count",
    "Average Accuracy",
    "Maximum Accuracy",
    "Minimum Accuracy",
    "IGN High Count",
    "IGN Low Count",
]

SUMMARY_COLUMNS = [
    "Device ID",
    "File Name",
    "Videometadata_Accuracy_Count",
    "Videometadata_Invalid_Accuracy_Count",
    "Start Time",
    "Duration",
    "Uptime",
    "Ignition_Status",
    "Nw_Recordedtime_Epoch",
    "Nw_Recordedtime",
    "Nwsource",
    "Rssi",
    "Sinr",
    "Videometadata_Accuracy",
    "Videometadata_Invalidaccuracy",
    "App_Ver",
    "Min Speed",
    "Max Speed",
    "Metadatastatus",
    "Udid",
    "avg_accuracy",
    "Latitude",
    "Longitude",
    "Timestamp",
]


# ---------------------------------------------------------------------------
# Small local helpers
#
# Deliberately re-implemented rather than imported from
# atlas/gps_oh_summary_generator.py: that module pulls sqlalchemy, python-dotenv
# and the Postgres device-config code at import time, none of which this
# ClickHouse-only script needs.
# ---------------------------------------------------------------------------

def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _isum(series: pd.Series) -> int:
    return int(_num(series).fillna(0).sum())


def _epoch_to_gmt(epoch_ms: Any) -> str:
    """Epoch milliseconds -> 'YYYY-MM-DD HH:MM:SS' GMT, '' when absent."""
    try:
        if epoch_ms is None or str(epoch_ms) in ("None", "nan", "NaT", ""):
            return ""
        value = float(epoch_ms)
        if value == 0:
            return ""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(value / 1000))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _date_range_slug(start: str, end: str) -> str:
    start_slug, end_slug = start.split(" ")[0], end.split(" ")[0]
    return start_slug if start_slug == end_slug else f"{start_slug}_to_{end_slug}"


def _sanitize_sheet_name(name: Any, fallback: str = "Sheet") -> str:
    text = str(name).strip() if name is not None else ""
    if not text:
        text = fallback
    cleaned = "".join("_" if ch in set("[]:*?/\\") else ch for ch in text)
    return cleaned.strip("'")[:31] or fallback


def _parse_dt(value: str) -> datetime:
    """Accept 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'."""
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised datetime {value!r}; use YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS'")


# ---------------------------------------------------------------------------
# Step 1 - device scope and tenant map
# ---------------------------------------------------------------------------

def load_installed_devices(device_list_root: Path) -> dict[str, str]:
    """device_id -> Tenant_Display_Name for every INSTALLED device in the CSVs.

    Reads OUTPUT/octo/<ota>/polling/<date>/device_data_<ota>.csv as written by
    pipeline/data_polling.py.
    """
    pattern = str(device_list_root / "*" / "polling" / "*" / "device_data_*.csv")
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise FileNotFoundError(
            f"No device_data_*.csv found under {device_list_root}.\n"
            "Generate it first:\n"
            "  python pipeline/data_polling.py obs --start-dt '2026-08-31 00:00:00' --end-dt '2026-09-01 00:00:00'"
        )

    tenants: dict[str, str] = {}
    for path in paths:
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if (row.get("Device_State") or "").strip().upper() != INSTALLED_STATE:
                    continue
                device_id = (row.get("Device_ID") or "").strip()
                if not device_id:
                    continue
                # Later OTA directories win; a device only has one live tenant.
                tenants[device_id] = (row.get("Tenant_Display_Name") or "").strip()

    logger.log_info(
        f"Loaded {len(tenants)} {INSTALLED_STATE} devices from {len(paths)} polling device_data CSV file(s)"
    )
    return tenants


# ---------------------------------------------------------------------------
# Step 2 - one ClickHouse query -> per-file frame
#
# video_metadata is aggregated FIRST, then LEFT JOINed onto observation_data.
# That order matters twice over: video_metadata.start_time is denormalised so
# PARTITION BY toYYYYMM(start_time) prunes on both sides, and it sidesteps
# ClickHouse's join_use_nulls=0 default, which would otherwise fill unmatched
# columns with 0/'' instead of NULL and silently inflate the counts.
# ---------------------------------------------------------------------------

PER_FILE_SQL = """
WITH vm AS (
    SELECT
        device_id,
        file_name,
        countIf(accuracy IS NOT NULL AND ifNull(valid, 0) = 1)                AS acc_count,
        countIf(accuracy IS NOT NULL AND ifNull(valid, 0) <> 1)               AS invalid_acc_count,
        countIf(accuracy IS NOT NULL AND ifNull(valid, 0) = 1 AND accuracy <= 2)    AS acc_le_2,
        countIf(accuracy IS NOT NULL AND ifNull(valid, 0) = 1 AND accuracy <= 3.5)  AS acc_le_3_5,
        countIf(accuracy IS NOT NULL AND ifNull(valid, 0) = 1 AND accuracy <= 6)    AS acc_le_6,
        countIf(accuracy IS NOT NULL AND ifNull(valid, 0) = 1 AND accuracy <= 10)   AS acc_le_10,
        countIf(accuracy IS NOT NULL AND ifNull(valid, 0) = 1 AND accuracy >  10)   AS acc_gt_10,
        avgIf(accuracy, ifNull(valid, 0) = 1)                                 AS avg_accuracy,
        maxIf(accuracy, ifNull(valid, 0) = 1)                                 AS max_accuracy,
        minIf(accuracy, ifNull(valid, 0) = 1 AND accuracy > 0)                AS min_accuracy,
        arrayStringConcat(
            groupArrayIf(toString(accuracy), accuracy IS NOT NULL AND ifNull(valid, 0) = 1), ' - '
        )                                                                     AS acc_list,
        arrayStringConcat(
            groupArrayIf(toString(accuracy), accuracy IS NOT NULL AND ifNull(valid, 0) <> 1), ' - '
        )                                                                     AS inv_acc_list,
        argMinIf(lat,       seq_no, ifNull(valid, 0) = 1)                     AS first_lat,
        argMinIf(long,      seq_no, ifNull(valid, 0) = 1)                     AS first_long,
        argMinIf(timestamp, seq_no, ifNull(valid, 0) = 1)                     AS first_ts
    FROM video_metadata
    WHERE start_time >= {start:DateTime64(3)}
      AND start_time <  {end:DateTime64(3)}
      {vm_device_filter}
    GROUP BY device_id, file_name
)
SELECT
    o.device_id                                            AS device_id,
    o.file_name                                            AS file_name,
    o.udid                                                 AS udid,
    o.ota                                                  AS ota,
    o.start_time                                           AS start_time,
    o.end_time                                             AS end_time,
    o.ignition_status                                      AS ignition_status,
    o.uptime                                               AS uptime,
    o.min_speed                                            AS min_speed,
    o.max_speed                                            AS max_speed,
    o.metadatastatus                                       AS metadatastatus,
    o.rssi                                                 AS rssi,
    o.sinr                                                 AS sinr,
    o.nw_source                                            AS nw_source,
    o.nw_recorded_time                                     AS nw_recorded_time,
    greatest(dateDiff('second', o.start_time, o.end_time), 0)      AS duration_sec,
    greatest(toUnixTimestamp64Milli(o.end_time) - toUnixTimestamp64Milli(o.start_time), 0) AS duration_ms,
    vm.acc_count                                           AS acc_count,
    vm.invalid_acc_count                                   AS invalid_acc_count,
    vm.acc_le_2                                            AS acc_le_2,
    vm.acc_le_3_5                                          AS acc_le_3_5,
    vm.acc_le_6                                            AS acc_le_6,
    vm.acc_le_10                                           AS acc_le_10,
    vm.acc_gt_10                                           AS acc_gt_10,
    vm.avg_accuracy                                        AS avg_accuracy,
    vm.max_accuracy                                        AS max_accuracy,
    vm.min_accuracy                                        AS min_accuracy,
    vm.acc_list                                            AS acc_list,
    vm.inv_acc_list                                        AS inv_acc_list,
    vm.first_lat                                           AS first_lat,
    vm.first_long                                          AS first_long,
    vm.first_ts                                            AS first_ts
FROM observation_data AS o
LEFT JOIN vm
    ON o.device_id = vm.device_id AND o.file_name = vm.file_name
WHERE o.start_time >= {start:DateTime64(3)}
  AND o.start_time <  {end:DateTime64(3)}
  {o_device_filter}
ORDER BY o.device_id, o.start_time, o.file_name
SETTINGS join_use_nulls = 1
"""


def _clickhouse_client(section: str):
    """Build a clickhouse_connect client from db_credentials.ini [<section>]."""
    # Imported lazily so --help and the pure aggregation helpers work without
    # clickhouse_connect installed.
    import clickhouse_connect

    from atlas.critical_events_dashboard_service import (
        _clickhouse_http_port,
        get_clickhouse_params,
    )

    params = get_clickhouse_params(str(REPO_ROOT), section)
    return clickhouse_connect.get_client(
        host=params["host"],
        port=_clickhouse_http_port(params["port"]),
        username=params["user"],
        password=params.get("password") or "",
        database=params["database"],
    )


def _apply_device_filters(sql: str, vm_filter: str, o_filter: str) -> str:
    """Substitute the two device-filter placeholders.

    Uses str.replace rather than str.format because the SQL also carries
    ClickHouse server-side parameters written as {name:Type}, which str.format
    would misread as format specs.
    """
    return (
        sql.replace("{vm_device_filter}", vm_filter)
        .replace("{o_device_filter}", o_filter)
    )


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def fetch_per_file_frame(
    start: datetime,
    end: datetime,
    device_ids: list[str] | None,
    section: str,
) -> pd.DataFrame:
    """Query ClickHouse for the per-observation-file frame both reports build on."""
    client = _clickhouse_client(section)

    # str.format() cannot be used here: ClickHouse's own {name:Type} parameter
    # syntax looks like a format spec to it and raises KeyError('start').
    if device_ids is None:
        sql = _apply_device_filters(PER_FILE_SQL, "", "")
        batches = [{"start": start, "end": end}]
    else:
        sql = _apply_device_filters(
            PER_FILE_SQL,
            "AND device_id IN {devices:Array(String)}",
            "AND o.device_id IN {devices:Array(String)}",
        )
        batches = [
            {"start": start, "end": end, "devices": chunk}
            for chunk in _chunks(device_ids, DEVICE_CHUNK)
        ]

    frames: list[pd.DataFrame] = []
    for index, parameters in enumerate(batches, start=1):
        logger.log_info(f"ClickHouse query batch {index}/{len(batches)}")
        frame = client.query_df(sql, parameters=parameters)
        if frame is not None and not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=_expected_frame_columns())

    combined = pd.concat(frames, ignore_index=True)
    logger.log_info(
        f"Fetched {len(combined)} observation files for "
        f"{combined['device_id'].nunique()} device(s)"
    )
    return combined


def _expected_frame_columns() -> list[str]:
    return [
        "device_id", "file_name", "udid", "ota", "start_time", "end_time",
        "ignition_status", "uptime", "min_speed", "max_speed", "metadatastatus",
        "rssi", "sinr", "nw_source", "nw_recorded_time", "duration_sec", "duration_ms",
        "acc_count", "invalid_acc_count", "acc_le_2", "acc_le_3_5", "acc_le_6",
        "acc_le_10", "acc_gt_10", "avg_accuracy", "max_accuracy", "min_accuracy",
        "acc_list", "inv_acc_list", "first_lat", "first_long", "first_ts",
    ]


# ---------------------------------------------------------------------------
# Step 3 - condition subsets and per-group aggregation (Reports 1a-1c)
# ---------------------------------------------------------------------------

def _cond1(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[(_num(frame["ignition_status"]) == 1) & (_num(frame["uptime"]) > 300)]


def _cond2(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[(_num(frame["ignition_status"]) == 1) & (_num(frame["max_speed"]) > 5)]


def _cond3(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[_num(frame["ignition_status"]) == 1]


# (key, human label, filename tag, filter)
CONDITIONS: list[tuple[str, str, str, Callable[[pd.DataFrame], pd.DataFrame]]] = [
    ("cond1", "Ignition ON and uptime > 300 s", "Cond1_IgnON_UptimeGT300", _cond1),
    ("cond2", "Ignition ON and speed > 5 mph", "Cond2_IgnON_SpeedGT5", _cond2),
    ("cond3", "Ignition ON", "Cond3_IgnON", _cond3),
]


def _ota_label(series: pd.Series) -> str:
    """Single OTA version, or 'OTA Updated' when a device changed mid-window."""
    versions = [str(v) for v in series.dropna().unique() if str(v).strip() not in ("", "None")]
    if not versions:
        return ""
    return versions[0] if len(versions) == 1 else "OTA Updated"


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float | str:
    value = _num(values)
    weight = _num(weights).fillna(0)
    mask = value.notna() & (weight > 0)
    total = weight[mask].sum()
    if total <= 0:
        return NO_GPS_LABEL
    return float((value[mask] * weight[mask]).sum() / total)


def _extreme(series: pd.Series, how: str) -> float | str:
    value = _num(series).dropna()
    if value.empty:
        return NO_GPS_LABEL
    return float(value.max() if how == "max" else value.min())


def _aggregate_group(
    group: pd.DataFrame,
    unfiltered: pd.DataFrame,
    date_range_label: str,
    device_id: str,
    tenant_map: dict[str, str],
) -> dict[str, Any]:
    obs_count = len(group)
    expected = _isum(group["duration_sec"])
    actual = _isum(group["acc_count"])
    invalid = _isum(group["invalid_acc_count"])

    no_gps_files = int(
        group["file_name"].fillna("").astype(str).str.contains(NO_GPS_SENTINEL, regex=False).sum()
    )

    ignition = _num(unfiltered["ignition_status"])

    return {
        "Given Date Range": date_range_label,
        "Device ID": device_id,
        "Tenant Display Name": tenant_map.get(str(device_id), ""),
        "OTA Version": _ota_label(group["ota"]),
        "ObsCount": obs_count,
        "0-2 m": _isum(group["acc_le_2"]),
        "0-3.5 m": _isum(group["acc_le_3_5"]),
        "0-6 m": _isum(group["acc_le_6"]),
        "0-10 m": _isum(group["acc_le_10"]),
        "Above 10 m": _isum(group["acc_gt_10"]),
        "GPS Loss %": (invalid * 100.0 / expected) if expected > 0 else 0.0,
        "No GPS %": (no_gps_files * 100.0 / obs_count) if obs_count > 0 else 0.0,
        "Expected Accuracy Count": expected,
        "Actual Accuracy Count": actual,
        "Invalid Accuracy Count": invalid,
        "Average Accuracy": _weighted_mean(group["avg_accuracy"], group["acc_count"]),
        "Maximum Accuracy": _extreme(group["max_accuracy"], "max"),
        "Minimum Accuracy": _extreme(group["min_accuracy"], "min"),
        "IGN High Count": int((ignition > 0).sum()),
        "IGN Low Count": int((ignition == 0).sum()),
    }


def build_observation_report(
    condition_frame: pd.DataFrame,
    full_frame: pd.DataFrame,
    date_range_label: str,
    tenant_map: dict[str, str],
) -> pd.DataFrame:
    """Aggregate one condition subset into the GPS Observation Report."""
    if condition_frame.empty:
        return pd.DataFrame(columns=OBS_COLUMNS)

    rows: list[dict[str, Any]] = []

    for device_id, group in condition_frame.groupby("device_id", sort=True):
        # IGN High/Low are deliberately taken from the UNFILTERED group: in an
        # ignition-ON condition subset IGN Low is structurally always zero.
        unfiltered = full_frame[full_frame["device_id"] == device_id]
        rows.append(
            _aggregate_group(group, unfiltered, date_range_label, device_id, tenant_map)
        )

    return pd.DataFrame(rows, columns=OBS_COLUMNS)


# ---------------------------------------------------------------------------
# Step 4 - GPS Summary Report (one row per video file)
# ---------------------------------------------------------------------------

def build_summary_report(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    out = pd.DataFrame(
        {
            "Device ID": frame["device_id"],
            "File Name": frame["file_name"],
            "Videometadata_Accuracy_Count": _num(frame["acc_count"]).fillna(0).astype(int),
            "Videometadata_Invalid_Accuracy_Count": _num(frame["invalid_acc_count"])
            .fillna(0)
            .astype(int),
            "Start Time": pd.to_datetime(frame["start_time"], errors="coerce"),
            "Duration": _num(frame["duration_ms"]).fillna(0).astype("int64"),
            "Uptime": _num(frame["uptime"]),
            "Ignition_Status": _num(frame["ignition_status"]),
            "Nw_Recordedtime_Epoch": _num(frame["nw_recorded_time"]),
            "Nw_Recordedtime": frame["nw_recorded_time"].map(_epoch_to_gmt),
            "Nwsource": frame["nw_source"],
            "Rssi": _num(frame["rssi"]),
            "Sinr": _num(frame["sinr"]),
            "Videometadata_Accuracy": frame["acc_list"].fillna(""),
            "Videometadata_Invalidaccuracy": frame["inv_acc_list"].fillna(""),
            "App_Ver": frame["ota"],
            "Min Speed": _num(frame["min_speed"]),
            "Max Speed": _num(frame["max_speed"]),
            "Metadatastatus": frame["metadatastatus"],
            "Udid": frame["udid"],
            "avg_accuracy": _num(frame["avg_accuracy"]),
            "Latitude": _num(frame["first_lat"]),
            "Longitude": _num(frame["first_long"]),
            "Timestamp": pd.to_datetime(frame["first_ts"], errors="coerce"),
        },
        columns=SUMMARY_COLUMNS,
    )
    return out.sort_values(["Device ID", "Start Time", "File Name"], kind="stable")


# ---------------------------------------------------------------------------
# Step 5 - Excel formatting (spec section 4)
#
# Cells are written individually rather than via DataFrame.to_excel so that the
# centre/middle alignment, the yellow bold header and the per-column number
# formats apply to every cell.
# ---------------------------------------------------------------------------

HEADER_COLOR = "#FFFF00"
DATETIME_FORMAT = "yyyy-mm-dd hh.mm.ss"
PERCENT_FORMAT = "0.00"
DEVICE_ID_FORMAT = "0"
DATETIME_COLUMNS = {"Start Time", "Timestamp"}
DEVICE_ID_COLUMNS = {"Device ID"}
MAX_COLUMN_WIDTH = 40
WIDTH_SAMPLE_ROWS = 5000

# Excel's hard sheet limit is 1,048,576 rows; one is spent on the header.
# xlsxwriter only *warns* on out-of-range writes and drops the data, so the
# GPS Summary (one row per video file) is split across sheets instead.
EXCEL_MAX_DATA_ROWS = 1_048_575


def _build_formats(workbook) -> dict[str, Any]:
    base = {"align": "center", "valign": "vcenter"}
    return {
        "header": workbook.add_format({**base, "bold": True, "bg_color": HEADER_COLOR, "border": 1}),
        "body": workbook.add_format(base),
        "percent": workbook.add_format({**base, "num_format": PERCENT_FORMAT}),
        "device_id": workbook.add_format({**base, "num_format": DEVICE_ID_FORMAT}),
        "datetime": workbook.add_format({**base, "num_format": DATETIME_FORMAT}),
    }


def _column_format(column: str, formats: dict[str, Any]):
    if column in DEVICE_ID_COLUMNS:
        return formats["device_id"]
    if column.strip().endswith("%"):
        return formats["percent"]
    if column in DATETIME_COLUMNS:
        return formats["datetime"]
    return formats["body"]


def _column_width(frame: pd.DataFrame, column: str) -> int:
    width = len(str(column))
    if not frame.empty:
        if column in DATETIME_COLUMNS:
            width = max(width, len(DATETIME_FORMAT))
        else:
            # Sample rather than scan: on a summary sheet with hundreds of
            # thousands of files, measuring every cell costs more than the
            # column width is worth.
            sample = frame[column].head(WIDTH_SAMPLE_ROWS).astype(str).str.len()
            width = max(width, int(sample.max()) if sample.notna().any() else width)
    return min(width + 2, MAX_COLUMN_WIDTH)


def _is_missing(value: Any) -> bool:
    """True for None, NaN and NaT.

    np.isscalar() is not usable as the guard here: it returns False for pd.NaT,
    which is itself an instance of datetime and would otherwise reach
    write_datetime and raise "NaTType does not support isocalendar".
    """
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(missing) if np.ndim(missing) == 0 else False


def _write_cell(worksheet, row: int, col: int, value: Any, cell_format, column: str) -> None:
    if _is_missing(value):
        worksheet.write_blank(row, col, None, cell_format)
        return

    if column in DEVICE_ID_COLUMNS:
        try:
            worksheet.write_number(row, col, int(str(value).strip()), cell_format)
            return
        except (TypeError, ValueError):
            pass

    if isinstance(value, (pd.Timestamp, datetime)):
        stamp = pd.Timestamp(value)
        if stamp.tz is not None:
            stamp = stamp.tz_localize(None)
        worksheet.write_datetime(row, col, stamp.to_pydatetime(), cell_format)
        return

    if isinstance(value, (bool, np.bool_)):
        worksheet.write_string(row, col, str(bool(value)), cell_format)
        return

    if isinstance(value, (int, np.integer)):
        worksheet.write_number(row, col, int(value), cell_format)
        return

    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value):
            worksheet.write_blank(row, col, None, cell_format)
            return
        worksheet.write_number(row, col, float(value), cell_format)
        return

    worksheet.write_string(row, col, str(value), cell_format)


def _write_sheet(writer, frame: pd.DataFrame, sheet_name: str) -> None:
    workbook = writer.book
    formats = _build_formats(workbook)
    worksheet = workbook.add_worksheet(_sanitize_sheet_name(sheet_name))
    writer.sheets[sheet_name] = worksheet

    columns = list(frame.columns)

    for col_index, column in enumerate(columns):
        worksheet.write_string(0, col_index, str(column), formats["header"])
        worksheet.set_column(
            col_index,
            col_index,
            _column_width(frame, column),
            _column_format(column, formats),
        )

    # Column-wise extraction: frame.iterrows() builds a Series per row, which
    # dominates runtime on a summary sheet with hundreds of thousands of files.
    values = [frame[column].tolist() for column in columns]
    cell_formats = [_column_format(column, formats) for column in columns]

    for row_index in range(len(frame)):
        for col_index, column in enumerate(columns):
            _write_cell(
                worksheet,
                row_index + 1,
                col_index,
                values[col_index][row_index],
                cell_formats[col_index],
                column,
            )

    worksheet.freeze_panes(1, 0)
    if columns:
        worksheet.autofilter(0, 0, max(len(frame), 1), len(columns) - 1)


def _split_for_excel(sheet_name: str, frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """Chunk a frame that would overflow one worksheet into _2, _3, ... sheets."""
    if len(frame) <= EXCEL_MAX_DATA_ROWS:
        return [(sheet_name, frame)]

    parts: list[tuple[str, pd.DataFrame]] = []
    for offset in range(0, len(frame), EXCEL_MAX_DATA_ROWS):
        index = offset // EXCEL_MAX_DATA_ROWS + 1
        name = sheet_name if index == 1 else f"{sheet_name}_{index}"
        parts.append((name, frame.iloc[offset : offset + EXCEL_MAX_DATA_ROWS]))
    logger.log_warning(
        f"{sheet_name}: {len(frame)} rows exceeds the Excel sheet limit; "
        f"split across {len(parts)} sheets"
    )
    return parts


def write_workbook(path: Path, sheets: list[tuple[str, pd.DataFrame]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        for sheet_name, frame in sheets:
            for part_name, part in _split_for_excel(sheet_name, frame):
                _write_sheet(writer, part, part_name)
    return path


# ---------------------------------------------------------------------------
# Step 6 - CLI
# ---------------------------------------------------------------------------

def _report_path(output_dir: Path, custom_stem: str | None, tag: str, timestamp: str) -> Path:
    if custom_stem:
        return output_dir / f"{custom_stem}_{tag}.xlsx"
    return output_dir / f"GPS_Obs_{tag}_{timestamp}.xlsx"


def generate_reports(
    start: datetime,
    end: datetime,
    output_dir: Path,
    tenant_map: dict[str, str],
    device_ids: list[str] | None,
    section: str,
    custom_stem: str | None,
    per_device_sheets: bool,
) -> list[Path]:
    frame = fetch_per_file_frame(start, end, device_ids, section)

    if not frame.empty:
        frame = frame.copy()
        frame["device_id"] = frame["device_id"].astype(str)
        frame["report_date"] = pd.to_datetime(frame["start_time"], errors="coerce").dt.date
    else:
        logger.log_warning("No observation data in range; writing header-only workbooks")
        frame["report_date"] = pd.Series(dtype="object")

    timestamp = time.strftime("%Y-%m-%d-%H_%M")
    date_range_label = f"{start.date()} to {end.date()}"
    written: list[Path] = []

    for _key, label, tag, condition in CONDITIONS:
        subset = condition(frame) if not frame.empty else frame
        report = build_observation_report(subset, frame, date_range_label, tenant_map)
        path = _report_path(output_dir, custom_stem, tag, timestamp)
        write_workbook(path, [("GPS_Observation", report)])
        logger.log_info(f"{label}: {len(subset)} files -> {len(report)} rows")
        written.append(path)

    summary = build_summary_report(frame)
    sheets: list[tuple[str, pd.DataFrame]] = [("GPS_Summary", summary)]
    if per_device_sheets and not summary.empty:
        for device_id, group in summary.groupby("Device ID", sort=True):
            sheets.append((_sanitize_sheet_name(device_id, "Device"), group))

    summary_name = (
        f"{custom_stem}_Summary.xlsx" if custom_stem else f"GPS_Summary_{timestamp}.xlsx"
    )
    summary_path = write_workbook(output_dir / summary_name, sheets)
    logger.log_info(f"GPS Summary: {len(summary)} rows across {len(sheets)} sheet(s)")
    written.append(summary_path)

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", required=True, help="Start datetime, inclusive (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End datetime, EXCLUSIVE (YYYY-MM-DD)")
    parser.add_argument(
        "--filename",
        default=None,
        help=(
            "Custom Excel filename stem. Each report keeps its discriminator, so "
            "--filename GPS_Wk25.xlsx yields GPS_Wk25_Cond1.xlsx ... GPS_Wk25_Summary.xlsx"
        ),
    )
    parser.add_argument(
        "--devices",
        default=None,
        help="Comma-separated device IDs, overriding the INSTALLED device list",
    )
    parser.add_argument(
        "--all-devices",
        action="store_true",
        help="Query every device in range (no device filter). Ignores the device list",
    )
    parser.add_argument(
        "--device-list-root",
        default=str(DEFAULT_DEVICE_LIST_ROOT),
        help=f"Directory holding <ota>/polling/<date>/device_data_<ota>.csv. Default: {DEFAULT_DEVICE_LIST_ROOT}",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_ROOT),
        help=f"Base output directory. Default: {DEFAULT_OUTPUT_ROOT}",
    )
    parser.add_argument(
        "--clickhouse-section",
        default=DEFAULT_CLICKHOUSE_SECTION,
        help=f"db_credentials.ini section. Default: {DEFAULT_CLICKHOUSE_SECTION}",
    )
    parser.add_argument(
        "--per-device-sheets",
        action="store_true",
        help="Also add one sheet per device to the GPS Summary workbook",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    start = _parse_dt(args.start)
    end = _parse_dt(args.end)
    if end <= start:
        print(f"error: --end ({args.end}) must be after --start ({args.start})", file=sys.stderr)
        return 2

    span = end - start
    logger.log_info(
        f"Window: {start} <= start_time < {end} "
        f"({span.days} day(s) {span.seconds // 3600}h)"
    )

    tenant_map: dict[str, str] = {}
    device_ids: list[str] | None

    if args.all_devices:
        device_ids = None
        logger.log_info("Querying all devices in range (--all-devices)")
    elif args.devices:
        device_ids = [item.strip() for item in args.devices.split(",") if item.strip()]
        logger.log_info(f"Querying {len(device_ids)} device(s) from --devices")
        try:
            tenant_map = load_installed_devices(Path(args.device_list_root))
        except FileNotFoundError:
            logger.log_warning("No device list found; Tenant Display Name will be blank")
    else:
        tenant_map = load_installed_devices(Path(args.device_list_root))
        device_ids = sorted(tenant_map)
        if not device_ids:
            print(
                f"error: no {INSTALLED_STATE} devices found under {args.device_list_root}",
                file=sys.stderr,
            )
            return 1

    custom_stem = None
    if args.filename:
        custom_stem = Path(args.filename).stem

    output_dir = Path(args.output) / _date_range_slug(args.start, args.end)

    try:
        written = generate_reports(
            start=start,
            end=end,
            output_dir=output_dir,
            tenant_map=tenant_map,
            device_ids=device_ids,
            section=args.clickhouse_section,
            custom_stem=custom_stem,
            per_device_sheets=args.per_device_sheets,
        )
    except Exception as exc:
        logger.log_error(f"Report generation failed: {exc}")
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for path in written:
        print(path.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
