#!/usr/bin/env python3
"""Generate GPS Performance CSV reports for installed OCTA devices from ClickHouse.

Emits two CSV files per run:

  1. GPS Observation Report - all three ignition conditions in one file, laid
     out side by side with OBS_BLOCK_GAP blank columns between them:
       Condition 1  ignition ON and uptime > 300 s
       Condition 2  ignition ON and max speed > 5 mph
       Condition 3  ignition ON
     Each block carries a heading row naming the condition, then its own header
     row. Rows are one per device for the requested date range; the blocks are
     independent, since a device qualifying for one condition need not qualify
     for another.
  2. GPS Summary Report - one row per observation/video file, with the GPS
     samples inside each file collapsed into counts, joined value lists, a mean
     and the first valid fix.

The five accuracy-range columns are percentages of Actual Accuracy Count, not
counts. The ranges are cumulative, so "0-6 m %" means "% of valid fixes within
6 metres" and 0-10 m % + Above 10 m % == 100.

Data comes from the ClickHouse `observation_data` and `video_metadata` tables
(see clickhouse_schema.sql), joined on file_name. Device scope and tenant
display names come from OUTPUT/octo/<ota>/polling/<date>/device_data_<ota>.csv,
produced by pipeline/data_polling.py.

Memory: the result set is never held whole. Rows stream out of ClickHouse in
blocks, are buffered only up to --max-buffer-rows, and each buffered frame is
aggregated and appended to the CSVs before the next one is read. Because the
query is ordered by device_id and buffers are cut on a device boundary, every
device's rows are complete inside exactly one buffer, which is what lets the
per-device aggregation and the per-device files be written incrementally.

Times are UTC: observation_data.start_time and video_metadata.timestamp are
stored in UTC, and --start/--end are interpreted in UTC.

Note: --start and --end are both INCLUSIVE. A date-only --end covers that
whole day, so a 7-day week is --start 2026-06-15 --end 2026-06-21.

Usage:
    python scripts/gps_performance_report.py --start 2026-06-15 --end 2026-06-21
    python scripts/gps_performance_report.py --start 2026-06-15 --end 2026-06-16 \
        --devices 125072600091 --filename QA_Smoke.csv
"""

from __future__ import annotations

import argparse
import csv
import glob
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

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

# Rows buffered in RAM before a flush. A flush is cut back to the last complete
# device, so peak usage is roughly this many rows plus the trailing device.
DEFAULT_MAX_BUFFER_ROWS = 250_000

# ClickHouse block size for the streaming reads; keeps a single block from
# arriving as one multi-million-row DataFrame.
STREAM_BLOCK_SIZE = 65_536

# A device that never acquired a fix writes impossible lat/long (91 / 181) into
# its filename. Established marker carried over from the retired OH/GPS summary generator.
NO_GPS_SENTINEL = "_91.0000_181.0000_"

NO_GPS_LABEL = "No GPS"

# Matches the number format the Excel edition of this report displayed.
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
PERCENT_DECIMALS = 2

# Blank columns separating the three condition blocks in the Observation CSV.
OBS_BLOCK_GAP = 5

OBS_COLUMNS = [
    "Given Date Range",
    "Device ID",
    "Tenant Display Name",
    "OTA Version",
    "ObsCount",
    "0-2 m %",
    "0-3.5 m %",
    "0-6 m %",
    "0-10 m %",
    "Above 10 m %",
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
# Re-implemented locally rather than shared with the retired OH/GPS summary
# generator, which pulled sqlalchemy and the Postgres device-config code at
# import time — none of which this ClickHouse-only script needs.
# ---------------------------------------------------------------------------

def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _isum(series: pd.Series) -> int:
    return int(_num(series).fillna(0).sum())


def _pct(value: float) -> float:
    return round(float(value), PERCENT_DECIMALS)


def _bucket_pct(series: pd.Series, actual: int) -> float:
    """An accuracy bucket as a percentage of the valid-fix count."""
    if actual <= 0:
        return 0.0
    return _pct(_isum(series) * 100.0 / actual)


def _epoch_ms_to_gmt(series: pd.Series) -> pd.Series:
    """Epoch milliseconds -> 'YYYY-MM-DD HH:MM:SS' GMT, '' when absent or 0.

    Vectorised on purpose: a per-row time.gmtime() call is one of the largest
    costs in the summary build once the window holds millions of files.
    """
    epoch = _num(series)
    stamps = pd.to_datetime(epoch.where(epoch != 0), unit="ms", errors="coerce")
    return stamps.dt.strftime(DATETIME_FORMAT).fillna("")


def _date_range_slug(start: str, end: str) -> str:
    start_slug, end_slug = start.split(" ")[0], end.split(" ")[0]
    return start_slug if start_slug == end_slug else f"{start_slug}_to_{end_slug}"


def _sanitize_name(name: Any, fallback: str = "part") -> str:
    """Reduce a value to something safe to use as a filename stem."""
    text = str(name).strip() if name is not None else ""
    if not text:
        text = fallback
    cleaned = "".join("_" if ch in set("[]:*?/\\") else ch for ch in text)
    return cleaned.strip("'")[:64] or fallback


def _inclusive_end_to_exclusive(value: str) -> datetime:
    """Turn an inclusive --end into the exclusive bound the SQL compares against.

    A date-only end means "through the end of that day", so it advances a whole
    day; an end carrying a time means "through that instant", so it advances by
    one DateTime64(3) tick. The SQL keeps `start_time < end` either way, which
    avoids boundary ambiguity at millisecond precision.

    All times are UTC -- observation_data.start_time is stored in UTC.
    """
    parsed = _parse_dt(value)
    if " " not in value.strip():
        return parsed + timedelta(days=1)
    return parsed + timedelta(milliseconds=1)


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
# PARTITION BY toYYYYMMDD(start_time) prunes on both sides, and it sidesteps
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


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _flush_on_device_boundary(
    blocks: Iterator[pd.DataFrame], max_buffer_rows: int
) -> Iterator[pd.DataFrame]:
    """Regroup streamed blocks into frames that end on a device boundary.

    The query is ordered by device_id, so the rows of the frame's last device
    may continue into the next block. Those trailing rows are carried over
    instead of being emitted, which guarantees every device's rows reach the
    aggregation together and keeps peak memory at ~max_buffer_rows.
    """
    carry: list[pd.DataFrame] = []
    carried = 0

    for block in blocks:
        if block is None or block.empty:
            continue
        carry.append(block)
        carried += len(block)
        if carried < max_buffer_rows:
            continue

        buffered = pd.concat(carry, ignore_index=True) if len(carry) > 1 else carry[0]
        carry, carried = [], 0

        # Rows are device_id-ordered, so the last device's rows are the tail.
        tail = buffered["device_id"] == buffered["device_id"].iloc[-1]
        head = buffered.loc[~tail]
        if not head.empty:
            yield head
        remainder = buffered.loc[tail]
        del buffered, head
        carry, carried = [remainder], len(remainder)

    if carry:
        buffered = pd.concat(carry, ignore_index=True) if len(carry) > 1 else carry[0]
        if not buffered.empty:
            yield buffered


def _query_blocks(client, sql: str, parameters: dict[str, Any]) -> Iterator[pd.DataFrame]:
    """Stream one query as DataFrame blocks, falling back to a single frame."""
    settings = {"max_block_size": STREAM_BLOCK_SIZE}
    stream = getattr(client, "query_df_stream", None)
    if stream is None:
        frame = client.query_df(sql, parameters=parameters)
        if frame is not None and not frame.empty:
            yield frame
        return

    with stream(sql, parameters=parameters, settings=settings) as blocks:
        for block in blocks:
            if block is not None and not block.empty:
                yield block


def iter_per_file_frames(
    start: datetime,
    end: datetime,
    device_ids: list[str] | None,
    section: str,
    max_buffer_rows: int,
) -> Iterator[pd.DataFrame]:
    """Yield the per-observation-file frame in device-complete chunks."""
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

    for index, parameters in enumerate(batches, start=1):
        logger.log_info(f"ClickHouse query batch {index}/{len(batches)}")
        blocks = _query_blocks(client, sql, parameters)
        # Device chunks never overlap, so a boundary flush per batch is enough.
        for frame in _flush_on_device_boundary(blocks, max_buffer_rows):
            frame["device_id"] = frame["device_id"].astype(str)
            yield frame


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


def _ignition_counts(frame: pd.DataFrame) -> pd.DataFrame:
    """Per-device IGN High/Low counts over the UNFILTERED rows.

    Computed once per buffered frame with a single groupby. Re-deriving it with
    a `frame[frame.device_id == d]` mask inside the per-device loop was an
    O(devices x rows) scan and dominated the runtime of all three reports.
    """
    ignition = _num(frame["ignition_status"])
    return (
        pd.DataFrame(
            {
                "device_id": frame["device_id"].to_numpy(),
                "high": (ignition > 0).to_numpy(),
                "low": (ignition == 0).to_numpy(),
            }
        )
        .groupby("device_id", sort=False)[["high", "low"]]
        .sum()
    )


def _aggregate_group(
    group: pd.DataFrame,
    ign_high: int,
    ign_low: int,
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

    return {
        "Given Date Range": date_range_label,
        "Device ID": device_id,
        "Tenant Display Name": tenant_map.get(str(device_id), ""),
        "OTA Version": _ota_label(group["ota"]),
        "ObsCount": obs_count,
        # Buckets are cumulative (0-2 is a subset of 0-3.5, ...), so the
        # denominator is Actual Accuracy Count rather than the sum of the
        # buckets, which would double-count. Reads as "% of valid fixes within
        # N metres"; 0-10 % + Above 10 % == 100.
        "0-2 m %": _bucket_pct(group["acc_le_2"], actual),
        "0-3.5 m %": _bucket_pct(group["acc_le_3_5"], actual),
        "0-6 m %": _bucket_pct(group["acc_le_6"], actual),
        "0-10 m %": _bucket_pct(group["acc_le_10"], actual),
        "Above 10 m %": _bucket_pct(group["acc_gt_10"], actual),
        "GPS Loss %": _pct(invalid * 100.0 / expected) if expected > 0 else 0.0,
        "No GPS %": _pct(no_gps_files * 100.0 / obs_count) if obs_count > 0 else 0.0,
        "Expected Accuracy Count": expected,
        "Actual Accuracy Count": actual,
        "Invalid Accuracy Count": invalid,
        "Average Accuracy": _weighted_mean(group["avg_accuracy"], group["acc_count"]),
        "Maximum Accuracy": _extreme(group["max_accuracy"], "max"),
        "Minimum Accuracy": _extreme(group["min_accuracy"], "min"),
        "IGN High Count": ign_high,
        "IGN Low Count": ign_low,
    }


def build_observation_rows(
    condition_frame: pd.DataFrame,
    full_frame: pd.DataFrame,
    date_range_label: str,
    tenant_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Aggregate one condition subset into GPS Observation Report rows."""
    if condition_frame.empty:
        return []

    # IGN High/Low are deliberately taken from the UNFILTERED frame: in an
    # ignition-ON condition subset IGN Low is structurally always zero.
    ign_counts = _ignition_counts(full_frame)

    rows: list[dict[str, Any]] = []
    for device_id, group in condition_frame.groupby("device_id", sort=True):
        high = low = 0
        if device_id in ign_counts.index:
            counts = ign_counts.loc[device_id]
            high, low = int(counts["high"]), int(counts["low"])
        rows.append(
            _aggregate_group(group, high, low, date_range_label, device_id, tenant_map)
        )
    return rows


# ---------------------------------------------------------------------------
# Step 4 - GPS Summary Report (one row per video file)
# ---------------------------------------------------------------------------

def build_summary_report(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per observation file, in the query's device/start_time order.

    No re-sort here: the query already orders by device_id, start_time,
    file_name, and device chunks are issued in sorted device order, so the
    appended CSV comes out globally sorted without a full-frame sort copy.
    """
    if frame.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    return pd.DataFrame(
        {
            "Device ID": frame["device_id"],
            "File Name": frame["file_name"],
            "Videometadata_Accuracy_Count": _num(frame["acc_count"]).fillna(0).astype("int64"),
            "Videometadata_Invalid_Accuracy_Count": _num(frame["invalid_acc_count"])
            .fillna(0)
            .astype("int64"),
            "Start Time": pd.to_datetime(frame["start_time"], errors="coerce"),
            "Duration": _num(frame["duration_ms"]).fillna(0).astype("int64"),
            "Uptime": _num(frame["uptime"]),
            "Ignition_Status": _num(frame["ignition_status"]),
            "Nw_Recordedtime_Epoch": _num(frame["nw_recorded_time"]),
            "Nw_Recordedtime": _epoch_ms_to_gmt(frame["nw_recorded_time"]),
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


# ---------------------------------------------------------------------------
# Step 5 - append-only CSV writers
#
# Every report file is opened once, its header written immediately (so a run
# with no data still produces header-only CSVs) and each buffered chunk is
# appended and dropped. Nothing accumulates in RAM waiting for a final write.
# ---------------------------------------------------------------------------

class CsvAppender:
    """One CSV file, header written up front, rows appended chunk by chunk."""

    def __init__(self, path: Path, columns: list[str]) -> None:
        self.path = path
        self.columns = columns
        self.rows = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(path, "w", newline="", encoding="utf-8")
        csv.writer(self._handle).writerow(columns)

    def append_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        writer = csv.DictWriter(
            self._handle, fieldnames=self.columns, extrasaction="ignore"
        )
        writer.writerows(rows)
        self.rows += len(rows)

    def append_frame(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        frame.to_csv(
            self._handle,
            header=False,
            index=False,
            date_format=DATETIME_FORMAT,
            lineterminator="\n",
        )
        self.rows += len(frame)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "CsvAppender":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def write_device_csv(directory: Path, device_id: Any, frame: pd.DataFrame) -> None:
    """Write one device's summary rows to its own CSV.

    Safe as a single 'w' write: buffered frames are cut on device boundaries,
    so a device's rows are never split across two chunks.
    """
    directory.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        directory / f"{_sanitize_name(device_id, 'device')}.csv",
        index=False,
        date_format=DATETIME_FORMAT,
        lineterminator="\n",
    )


def write_observation_report(
    path: Path, blocks: list[tuple[str, list[dict[str, Any]]]]
) -> Path:
    """Write the condition blocks side by side into one CSV.

    Each block is a heading row naming the condition, then the OBS_COLUMNS
    header row, then its data rows. Blocks are separated by OBS_BLOCK_GAP blank
    columns. Blocks are padded to equal depth rather than zipped: a device that
    qualifies for one condition need not qualify for another, so row N of one
    block is not the same device as row N of the next.
    """
    width = len(OBS_COLUMNS)
    gap = [""] * OBS_BLOCK_GAP

    heading: list[Any] = []
    header: list[Any] = []
    for index, (label, _rows) in enumerate(blocks):
        if index:
            heading += gap
            header += gap
        heading += [label] + [""] * (width - 1)
        header += list(OBS_COLUMNS)

    depth = max((len(rows) for _label, rows in blocks), default=0)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(heading)
        writer.writerow(header)
        for offset in range(depth):
            line: list[Any] = []
            for index, (_label, rows) in enumerate(blocks):
                if index:
                    line += gap
                if offset < len(rows):
                    row = rows[offset]
                    line += [
                        "" if row.get(column) is None else row.get(column, "")
                        for column in OBS_COLUMNS
                    ]
                else:
                    line += [""] * width
            writer.writerow(line)
    return path


# ---------------------------------------------------------------------------
# Step 6 - CLI
# ---------------------------------------------------------------------------

def _observation_path(output_dir: Path, custom_stem: str | None, timestamp: str) -> Path:
    if custom_stem:
        return output_dir / f"{custom_stem}_Observation.csv"
    return output_dir / f"GPS_Observation_{timestamp}.csv"


def _summary_path(output_dir: Path, custom_stem: str | None, timestamp: str) -> Path:
    if custom_stem:
        return output_dir / f"{custom_stem}_Summary.csv"
    return output_dir / f"GPS_Summary_{timestamp}.csv"


def expected_report_paths(
    output_dir: Path, custom_stem: str | None, timestamp: str = ""
) -> list[Path]:
    """The report paths, in report order: combined Observation, then Summary.

    All three ignition conditions share one Observation CSV, laid out side by
    side -- see write_observation_report(). Single source of truth for report
    filenames, shared with atlas/gps_summary_service.py's "already generated"
    check so the two cannot drift apart. Only meaningful for a custom_stem; the
    default names carry a run timestamp and are not predictable.
    """
    return [
        _observation_path(output_dir, custom_stem, timestamp),
        _summary_path(output_dir, custom_stem, timestamp),
    ]


def generate_reports(
    start: datetime,
    end: datetime,
    output_dir: Path,
    tenant_map: dict[str, str],
    device_ids: list[str] | None,
    section: str,
    custom_stem: str | None,
    per_device_files: bool,
    max_buffer_rows: int,
) -> list[Path]:
    timestamp = time.strftime("%Y-%m-%d-%H_%M")
    # `end` is exclusive, so the label must show the last day actually covered,
    # otherwise a 31 Aug -> 7 Sep request reads as "to 2026-09-08".
    date_range_label = f"{start.date()} to {(end - timedelta(seconds=1)).date()}"

    summary_path = _summary_path(output_dir, custom_stem, timestamp)
    device_dir = output_dir / (
        f"{custom_stem}_Summary_by_device" if custom_stem else f"GPS_Summary_{timestamp}_by_device"
    )

    total_files = 0
    devices_seen: set[str] = set()
    device_files = 0
    condition_files = {key: 0 for key, _label, _tag, _fn in CONDITIONS}
    # Observation rows are accumulated rather than streamed: the three blocks sit
    # side by side, so column N of a line depends on all three. Bounded by the
    # device count (one row per device per condition), not the file count.
    obs_rows: dict[str, list[dict[str, Any]]] = {
        key: [] for key, _label, _tag, _fn in CONDITIONS
    }

    with ExitStack() as stack:
        summary_writer = stack.enter_context(CsvAppender(summary_path, SUMMARY_COLUMNS))

        for frame in iter_per_file_frames(start, end, device_ids, section, max_buffer_rows):
            total_files += len(frame)
            devices_seen.update(frame["device_id"].unique().tolist())

            for key, _label, _tag, condition in CONDITIONS:
                subset = condition(frame)
                condition_files[key] += len(subset)
                obs_rows[key].extend(
                    build_observation_rows(subset, frame, date_range_label, tenant_map)
                )
                del subset

            summary = build_summary_report(frame)
            summary_writer.append_frame(summary)
            if per_device_files:
                for device_id, group in summary.groupby("Device ID", sort=True):
                    write_device_csv(device_dir, device_id, group)
                    device_files += 1
            del summary, frame

        if total_files == 0:
            logger.log_warning("No observation data in range; wrote header-only CSVs")
        else:
            logger.log_info(
                f"Fetched {total_files} observation files for {len(devices_seen)} device(s)"
            )

        for key, label, _tag, _fn in CONDITIONS:
            logger.log_info(
                f"{label}: {condition_files[key]} files -> {len(obs_rows[key])} rows"
            )
        logger.log_info(f"GPS Summary: {summary_writer.rows} rows")
        if per_device_files:
            logger.log_info(f"Per-device summaries: {device_files} CSV file(s) in {device_dir}")

    observation_path = write_observation_report(
        _observation_path(output_dir, custom_stem, timestamp),
        [
            (f"Condition {index} - {label}", obs_rows[key])
            for index, (key, label, _tag, _fn) in enumerate(CONDITIONS, start=1)
        ],
    )
    logger.log_info(
        f"GPS Observation: 3 condition blocks side by side in {observation_path.name}"
    )

    return [observation_path, summary_path]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--start", required=True,
        help="Start datetime, INCLUSIVE, UTC (YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS')",
    )
    parser.add_argument(
        "--end", required=True,
        help=(
            "End datetime, INCLUSIVE, UTC. A date-only value covers that whole "
            "day, so --start 2026-08-31 --end 2026-09-07 is 8 days"
        ),
    )
    parser.add_argument(
        "--filename",
        default=None,
        help=(
            "Custom CSV filename stem. Each report keeps its discriminator, so "
            "--filename GPS_Wk25.csv yields GPS_Wk25_Observation.csv and "
            "GPS_Wk25_Summary.csv"
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
        "--per-device-files",
        "--per-device-sheets",
        dest="per_device_files",
        action="store_true",
        help="Also write one summary CSV per device into a <summary>_by_device/ directory",
    )
    parser.add_argument(
        "--max-buffer-rows",
        type=int,
        default=DEFAULT_MAX_BUFFER_ROWS,
        help=(
            "Rows held in RAM before a flush. Lower it on a memory-tight host, "
            f"raise it for speed. Default: {DEFAULT_MAX_BUFFER_ROWS}"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    start = _parse_dt(args.start)
    # --end is inclusive; the SQL bound is exclusive, so advance past it.
    end = _inclusive_end_to_exclusive(args.end)
    if end <= start:
        print(
            f"error: --end ({args.end}) must be on or after --start ({args.start})",
            file=sys.stderr,
        )
        return 2
    if args.max_buffer_rows < 1:
        print("error: --max-buffer-rows must be at least 1", file=sys.stderr)
        return 2

    span = end - start
    logger.log_info(
        f"Window (UTC): {start} <= start_time < {end} "
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
            per_device_files=args.per_device_files,
            max_buffer_rows=args.max_buffer_rows,
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
