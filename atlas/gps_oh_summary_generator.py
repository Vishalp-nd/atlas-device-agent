"""obs_summary_generator.py — Generate OH Summary and GPS Summary from public.extracteddata.

Replaces the S3 zip download + JSON parse flow of ObsParsing.py by querying the DB directly.

Usage:
    python3 obs_summary_generator.py --start "2026-08-11" --end "2026-08-12"
    python3 obs_summary_generator.py --start "2026-08-11 00:00:00" --end "2026-08-11 23:59:59"
    python3 obs_summary_generator.py --start "2026-08-11" --end "2026-08-12" --output /path/to/output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "pipeline"))
from fetch_device_config import read_db_config
from lib.fetch_data import regionUS

DB_SECTION = "IRAVATH_DB"
DB_CONFIG_FILE = str(REPO_ROOT / "db_credentials.ini")
DEFAULT_OUTPUT_ROOT = str(REPO_ROOT / "OUTPUT" / "obs_summaries")
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
FAMILY_CONFIG = regionUS.FAMILY_CONFIG


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _make_engine():
    p = read_db_config(DB_CONFIG_FILE, DB_SECTION)
    url = f"postgresql+psycopg2://{p['user']}:{p['password']}@{p['host']}:{p['port']}/{p['database']}"
    return create_engine(url, pool_pre_ping=True)


def _normalize_product_families(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []

    families = [item.strip().lower() for item in raw_value.split(",") if item.strip()]
    if not families:
        return []

    if "all" in families:
        return sorted(FAMILY_CONFIG.keys())

    invalid = [family for family in families if family not in FAMILY_CONFIG]
    if invalid:
        valid = ", ".join(sorted(FAMILY_CONFIG.keys()))
        raise ValueError(
            "Invalid product family value(s): "
            f"{', '.join(invalid)}. Allowed values: {valid}, all"
        )

    return list(dict.fromkeys(families))


def _load_default_product_families() -> list[str]:
    load_dotenv(str(DEFAULT_ENV_PATH), override=False)
    return _normalize_product_families(os.getenv("PRODUCT_LINES", ""))


def _date_range_slug(start_dt: str, end_dt: str) -> str:
    start_slug = start_dt.split(" ")[0]
    end_slug = end_dt.split(" ")[0]
    return f"{start_slug}_to_{end_slug}" if start_slug != end_slug else start_slug


def _build_output_dir(base_output_root: str, product_family: str, start_dt: str, end_dt: str) -> str:
    return os.path.join(base_output_root, product_family, _date_range_slug(start_dt, end_dt))


def fetch_extracteddata(start_dt: str, end_dt: str, product_family: str | None = None) -> pd.DataFrame:
    """Query all needed columns from public.extracteddata for the given time range."""
    where_clause = "start_time >= :start AND start_time <= :end"
    params: dict[str, Any] = {"start": start_dt, "end": end_dt}

    if product_family:
        _, major_prefix = FAMILY_CONFIG[product_family]
        where_clause += " AND ota LIKE :ota_prefix"
        params["ota_prefix"] = f"{major_prefix}%"

    sql = text(f"""
        SELECT
            device_id, ota, udid, file_name, start_time, end_time,
            ignition_status, uptime, service_uptime, privacymode, dismode,
            voltage, processing_mode, inertial_processed, vision_processed,
            inward_vision_processed, nrt_status, metadatastatus, tripno,
            videometadatastatus, min_speed, max_speed, sensormetadata_count,
            driverinvariantsession, driverid, vehclass, vehicleid, cameras,
            prevvideoname, current_videoname, nextvideoname,
            alerts_data_num_alerts, alerts_data,
            audio_events_num_alerts, audio_events_data,
            videometadata, rssi, vin, can_firmware_ver, "offset",
            "faceImageCaptured", "audioEnable", user_generated_alert,
            obs_filetype, is_inward_cam_obstructed,
            has_multi_lane, has_road_boundary_tracks, has_ipc_events, is_hd_file,
            rtc_valid, rtc_jump_from, rtc_jump_to, session_count, valid_gps_entries,
            gps_start_time, gps_end_time, nw_source, sinr, nw_recorded_time,
            idle, obdformat, tc_recommendation, json_size_kb, s3_path,
            is_inward_processed, engine_status, protocol_info
        FROM public.extracteddata
        WHERE {where_clause}
        ORDER BY device_id, start_time
    """)
    engine = _make_engine()
    with engine.connect() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    engine.dispose()
    return df


# ---------------------------------------------------------------------------
# Derived field helpers
# ---------------------------------------------------------------------------

def _epoch_to_gmt(epoch_ms: Any) -> str:
    try:
        if epoch_ms is None or epoch_ms == 0 or str(epoch_ms) in ("None", "nan"):
            return ""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(epoch_ms) / 1000))
    except Exception:
        return ""


def _sig_strength(rssi: Any) -> Any:
    try:
        if rssi is None or rssi == "" or str(rssi) in ("None", "nan"):
            return ""
        v = int(rssi)
        if v < -100:
            return 1
        if v < -90:
            return 2
        if v < -70:
            return 3
        return 4
    except Exception:
        return ""


def _extract_alert_codes(alerts_data: Any) -> str:
    try:
        if not alerts_data:
            return ""
        if isinstance(alerts_data, str):
            alerts_data = json.loads(alerts_data)
        return " - ".join(str(a.get("event_code", "")) for a in alerts_data if a.get("event_code")) + " - "
    except Exception:
        return ""


def _extract_audio_codes(audio_data: Any) -> str:
    try:
        if not audio_data:
            return ""
        if isinstance(audio_data, str):
            audio_data = json.loads(audio_data)
        return " - ".join(str(a.get("event_code", "")) for a in audio_data if a.get("event_code")) + " - "
    except Exception:
        return ""


def _extract_alert_playback(audio_data: Any) -> str:
    try:
        if not audio_data:
            return ""
        if isinstance(audio_data, str):
            audio_data = json.loads(audio_data)
        return " - ".join(str(a.get("playback_success", "")) for a in audio_data) + " - "
    except Exception:
        return ""


def _extract_alert_uuids(alerts_data: Any) -> tuple[str, int]:
    try:
        if not alerts_data:
            return "", 0
        if isinstance(alerts_data, str):
            alerts_data = json.loads(alerts_data)
        uuids = [a.get("uuid", "") for a in alerts_data]
        uuid_str = " - ".join(str(u) if u else "." for u in uuids) + " - "
        return uuid_str, sum(1 for u in uuids if u)
    except Exception:
        return "", 0


def _extract_audio_uuids(audio_data: Any) -> tuple[str, int]:
    try:
        if not audio_data:
            return "", 0
        if isinstance(audio_data, str):
            audio_data = json.loads(audio_data)
        uuids = [a.get("uuid", "") for a in audio_data]
        uuid_str = " - ".join(str(u) if u else "." for u in uuids) + " - "
        return uuid_str, sum(1 for u in uuids if u)
    except Exception:
        return "", 0


def _parse_videometadata_gps(vm: Any) -> dict:
    """Extract GPS accuracy, speed, lat/long counts + min/max from videometadata JSONB."""
    result = {
        "VideoMetadata_accuracy_count": 0,
        "VideoMetadata_invalid_accuracy_count": 0,
        "VideoMetadata_speed": 0,
        "VideoMetadata_lat": 0,
        "VideoMetadata_long": 0,
        "VideoMetadata_altitude": 0,
        "VideoMetadata_bearing": 0,
        "VideoMetadata_timestamp": 0,
        "VideoMetadata_raw_timestamp": 0,
        "VideoMetadata_accuracy": "",
        "VideoMetadata_invalidaccuracy": "",
        "Min_ACC": None,
        "Max_ACC": None,
        "GPS_Min_Speed": None,
        "GPS_Max_Speed": None,
    }
    if not vm:
        return result
    try:
        if isinstance(vm, str):
            vm = json.loads(vm)
        if not isinstance(vm, list):
            return result

        acc_list, inv_acc_list, speeds = [], [], []
        for item in vm:
            if not isinstance(item, dict):
                continue
            if "speed" in item:
                result["VideoMetadata_speed"] += 1
                speeds.append(item["speed"])
            if "lat" in item:
                result["VideoMetadata_lat"] += 1
            if "long" in item:
                result["VideoMetadata_long"] += 1
            if "altitude" in item:
                result["VideoMetadata_altitude"] += 1
            if "bearing" in item:
                result["VideoMetadata_bearing"] += 1
            if "timestamp" in item:
                result["VideoMetadata_timestamp"] += 1
            if "raw_timestamp" in item:
                result["VideoMetadata_raw_timestamp"] += 1
            if "accuracy" in item:
                if item.get("valid") == 1:
                    result["VideoMetadata_accuracy_count"] += 1
                    acc_list.append(item["accuracy"])
                    result["VideoMetadata_accuracy"] += str(item["accuracy"]) + " - "
                else:
                    result["VideoMetadata_invalid_accuracy_count"] += 1
                    inv_acc_list.append(item["accuracy"])
                    result["VideoMetadata_invalidaccuracy"] += str(item["accuracy"]) + " - "

        if acc_list:
            result["Min_ACC"] = round(min(acc_list), 2)
            result["Max_ACC"] = round(max(acc_list), 2)
        if speeds:
            result["GPS_Min_Speed"] = round(min(speeds), 2)
            result["GPS_Max_Speed"] = round(max(speeds), 2)
    except Exception:
        pass
    return result


def _user_alert_count(ua: Any) -> int:
    try:
        if not ua:
            return 0
        if isinstance(ua, str):
            ua = json.loads(ua)
        return len(ua) if isinstance(ua, list) else 0
    except Exception:
        return 0


def _obs_filetype(row: pd.Series) -> str:
    val = row.get("obs_filetype")
    if val and str(val).strip() not in ("", "None", "nan"):
        return str(val).strip()
    return "Metadata"


def _hd_ld(row: pd.Series) -> str:
    return "HD" if row.get("is_hd_file") else "LD"


# ---------------------------------------------------------------------------
# Build flat per-row dict (like ObsParsing summ_dict entry)
# ---------------------------------------------------------------------------

def _build_row(row: pd.Series) -> dict:
    vm = _parse_videometadata_gps(row.get("videometadata"))
    alert_uuid, uuid_count = _extract_alert_uuids(row.get("alerts_data"))
    audio_uuid, audio_uuid_count = _extract_audio_uuids(row.get("audio_events_data"))
    alert_codes = _extract_alert_codes(row.get("alerts_data"))
    audio_codes = _extract_audio_codes(row.get("audio_events_data"))
    alert_playback = _extract_alert_playback(row.get("audio_events_data"))

    start_ms = row.get("gps_start_time") or row.get("starttime")
    end_ms = row.get("gps_end_time") or None

    # Duration in ms from DB timestamps
    try:
        dur = int((row["end_time"] - row["start_time"]).total_seconds() * 1000)
    except Exception:
        dur = 0

    rtc_jump_from = row.get("rtc_jump_from")
    rtc_jump_to = row.get("rtc_jump_to")
    try:
        time_drift = str(int(rtc_jump_to) - int(rtc_jump_from))
    except Exception:
        time_drift = "None"

    return {
        "deviceId": row.get("device_id", ""),
        "app_ver": row.get("ota", ""),
        "udid": row.get("udid", ""),
        "file_name": row.get("file_name", ""),
        "StartTime": row.get("start_time"),
        "EndTime": row.get("end_time"),
        "Duration": dur,
        "ignition_status": row.get("ignition_status"),
        "Uptime": row.get("uptime"),
        "serviceUpTime": row.get("service_uptime"),
        "privacymode": row.get("privacymode"),
        "processing_mode": row.get("processing_mode"),
        "disMode": row.get("dismode"),
        "Voltage": row.get("voltage"),
        "inertial_Processed": row.get("inertial_processed"),
        "vision_Processed": row.get("vision_processed"),
        "inward_vision__Processed": row.get("inward_vision_processed"),
        "NRT_STATUS": row.get("nrt_status"),
        "metadataStatus": row.get("metadatastatus"),
        "FileSize": row.get("json_size_kb"),
        "Alertcode": alert_codes,
        "Alert_playback": alert_playback,
        "Audiocode": audio_codes,
        "audioEnable": "Yes" if row.get("audioEnable") == 1 else ("No" if row.get("audioEnable") == 0 else None),
        "gpsStartTime": row.get("gps_start_time"),
        "gpsEndTime": row.get("gps_end_time"),
        "gpsStartTime_fmt": _epoch_to_gmt(row.get("gps_start_time")),
        "gpsEndTime_fmt": _epoch_to_gmt(row.get("gps_end_time")),
        "validGPSEntries": row.get("valid_gps_entries"),
        "sessionCount": row.get("session_count"),
        "rtcValid": row.get("rtc_valid"),
        "rtc_jump_from": rtc_jump_from,
        "rtc_jump_to": rtc_jump_to,
        "TimeDrift": time_drift,
        "transcode_recommendation": row.get("tc_recommendation"),
        "idle": row.get("idle"),
        "driverInvariantSession": row.get("driverinvariantsession"),
        "driverId": row.get("driverid"),
        "vehClass": row.get("vehclass"),
        "vehicleId": row.get("vehicleid"),
        "cameras": row.get("cameras"),
        "prevVideoName": row.get("prevvideoname"),
        "Current_videoName": row.get("current_videoname"),
        "nextVideoName": row.get("nextvideoname"),
        "obdFormat": row.get("obdformat"),
        "uuidcount": uuid_count,
        "Audiouuidcount": audio_uuid_count,
        "uuid": alert_uuid,
        "Audiouuid": audio_uuid,
        "playback_success": alert_playback,
        "NWSource": row.get("nw_source"),
        "NetworkInfo": 1 if row.get("nw_source") else 0,
        "rssi": row.get("rssi"),
        "sinr": row.get("sinr"),
        "SigStrengt": _sig_strength(row.get("rssi")),
        "NW_recordedTime": row.get("nw_recorded_time"),
        "NW_recordedTime_fmt": _epoch_to_gmt(row.get("nw_recorded_time")),
        "multiLane": "Yes" if row.get("has_multi_lane") else "No",
        "ipc_events": "Yes" if row.get("has_ipc_events") else "No",
        "roadBoundaryTracks": "Yes" if row.get("has_road_boundary_tracks") else "No",
        "carBoxTrackerListCompressed": "Yes" if row.get("is_hd_file") else "No",
        "Filetype": _hd_ld(row),
        "OBSFileType": _obs_filetype(row),
        "faceImageCaptured": row.get("faceImageCaptured"),
        "is_inward_cam_obstructed": row.get("is_inward_cam_obstructed"),
        "AVP": "Yes" if row.get("is_inward_processed") else "No",
        "engine_status": row.get("engine_status"),
        "protocol_info": row.get("protocol_info"),
        "VideoMetaDataLen": row.get("videometadatastatus") or 0,
        "VideoMetaDataStatus": "Yes" if (row.get("videometadatastatus") or 0) > 0 else "No",
        "User_generated_alert": _user_alert_count(row.get("user_generated_alert")),
        "verticalAngle": None,
        "horizontalAngle": None,
        "tripNo": row.get("tripno"),
        "offset": row.get("offset"),
        "Min_Speed": row.get("min_speed"),
        "Max_Speed": row.get("max_speed"),
        # GPS from videometadata JSONB
        **vm,
    }


# ---------------------------------------------------------------------------
# OH Summary (Obs_Summary) generation
# ---------------------------------------------------------------------------

def _get_col_widths(df: pd.DataFrame) -> list:
    idx_max = max([len(str(s)) for s in df.index.values] + [len(str(df.index.name))])
    return [idx_max] + [
        max([len(str(s)) for s in df[col].values] + [len(str(col))]) for col in df.columns
    ]


def _get_not_none(df: pd.DataFrame, col: str) -> pd.DataFrame:
    try:
        return df[df[col].astype(str).str.contains("None") == False]
    except Exception:
        return df


def _build_per_device_summary(device_df: pd.DataFrame, did: Any, rows: list[dict]) -> dict:
    """Compute per-device aggregate counts matching ObsParsing Excellconversion logic."""
    d: dict = {"DEVICEID": did}

    app_vers = device_df["app_ver"].dropna().unique()
    d["APP_VER"] = app_vers[0] if len(app_vers) == 1 else "OTA Updated"
    d["NoOfFiles"] = len(device_df)

    # Ignition / uptime
    ig = device_df[device_df["ignition_status"].notna()]
    d["IGNITION_ON"] = int((ig["ignition_status"].astype(float) > 0).sum())
    d["LowPowerMode"] = int((ig["ignition_status"].astype(float) == 0).sum())

    ut = ig[ig["Uptime"].notna()]
    d["Ignition_ON_Bootup_Count"] = int(
        ((ut["ignition_status"].astype(float) == 1) & (ut["Uptime"].astype(float) < 60)).sum()
    )
    d["LowpowerWakeupCount"] = int(
        ((ut["ignition_status"].astype(float) == 0) & (ut["Uptime"].astype(float) < 60)).sum()
    )

    idle_tmp = _get_not_none(device_df, "idle")
    try:
        d["Idle_files"] = int((idle_tmp["idle"].astype(float) > 0).sum())
    except Exception:
        d["Idle_files"] = 0

    pm_tmp = _get_not_none(device_df, "privacymode")
    try:
        d["PRIVACYMODE"] = int((pm_tmp["privacymode"].astype(float) > 0).sum())
    except Exception:
        d["PRIVACYMODE"] = 0

    d["ALERT_UUID"] = int((device_df["uuidcount"].fillna(0).astype(int) > 0).sum())
    d["AUDIO_UUID"] = int((device_df["Audiouuidcount"].fillna(0).astype(int) > 0).sum())
    d["Event_Code_Count"] = int((device_df["Alertcode"].fillna("").str.strip() != "").sum())

    d["NoGPS"] = int(device_df["file_name"].fillna("").str.contains("_91.0000_181.0000_").sum())
    d["LTE"] = int(device_df["NWSource"].fillna("").str.contains("LTE").sum())
    d["No Network"] = int(device_df["NWSource"].fillna("").str.contains("NA").sum())

    try:
        d["RSSI = 0"] = int((device_df["rssi"] == 0).sum())
    except Exception:
        d["RSSI = 0"] = "-"

    d["GPS Starttime = 0"] = int((device_df["gpsStartTime"].fillna(0) == 0).sum())
    d["NRT - Packet Drop"] = int(device_df["NRT_STATUS"].fillna("").str.contains("PACKET_DROP").sum())
    d["NRT - Speed Dict Empty"] = int(device_df["NRT_STATUS"].fillna("").str.contains("SPEED_DICT_EMPTY").sum())
    d["User Generated Alerts Count"] = int((device_df["User_generated_alert"].fillna(0).astype(int) > 0).sum())

    d["Min_FileSize"] = device_df["FileSize"].min()
    d["Max_FileSize"] = device_df["FileSize"].max()
    d["Avg_FileSize"] = device_df["FileSize"].mean()

    d["MetaDataCount"] = int(device_df["OBSFileType"].fillna("").str.contains("Metadata").sum())
    d["NoOfFaces"] = int(device_df["faceImageCaptured"].fillna(False).astype(bool).sum())
    d["INERTIAL_PROCESSED"] = int(device_df["inertial_Processed"].fillna("").astype(str).str.contains("True|Yes", case=False).sum())
    d["VISION_PROCESSED"] = int(device_df["vision_Processed"].fillna("").astype(str).str.contains("True|Yes", case=False).sum())
    d["INWARD_VISION__PROCESSED"] = int(device_df["inward_vision__Processed"].fillna("").astype(str).str.contains("True|Yes", case=False).sum())
    d["AUDIOENABLED"] = int(device_df["audioEnable"].fillna("").astype(str).str.contains("Yes|1").sum())

    d["CrankOffMetadata"] = int(
        ((device_df["OBSFileType"].str.contains("Metadata")) & (device_df["ignition_status"].fillna(-1).astype(float) == 0)).sum()
    )
    d["CrankONMetadata"] = int(
        ((device_df["OBSFileType"].str.contains("Metadata")) & (device_df["ignition_status"].fillna(-1).astype(float) > 0)).sum()
    )
    d["Partial Metadata File Count"] = int((device_df["metadataStatus"].fillna("None").astype(str) != "None").sum())
    d["Metadata LeftOver Count"] = int(device_df["metadataStatus"].fillna("").astype(str).str.contains("metadata_leftover").sum())

    try:
        d["GPS Time Mismatch"] = int((device_df["gpsStartTime"].fillna(0).astype(float) < 1572546600000).sum())
    except Exception:
        d["GPS Time Mismatch"] = 0
    d["Partial Files"] = d["Partial Metadata File Count"]

    # Voltage buckets
    try:
        d["Voltage > 14"] = int((device_df["Voltage"].dropna().astype(float) > 14).sum())
        d["Voltage < 10"] = int(((device_df["Voltage"].dropna().astype(float) < 10) & (device_df["Voltage"].dropna().astype(float) != 3)).sum())
        d["Voltage = 3"] = int((device_df["Voltage"].dropna().astype(float) == 3).sum())
    except Exception:
        d["Voltage > 14"] = d["Voltage < 10"] = d["Voltage = 3"] = 0

    # VideoMetadata item buckets
    vm_len = device_df["VideoMetaDataLen"].fillna(0).astype(int)
    d["Video Metadata = 0"] = int((vm_len == 0).sum())
    d["Video Metadata > 1 < 30"] = int(((vm_len > 0) & (vm_len <= 30)).sum())
    d["Video Metadata > 31 < 55"] = int(((vm_len >= 31) & (vm_len <= 55)).sum())
    d["Video Metadata > 56 < 59"] = int(((vm_len >= 56) & (vm_len <= 59)).sum())
    d["Video Metadata = 60"] = int((vm_len == 60).sum())
    d["Video Metadata > 60"] = int((vm_len > 60).sum())

    # OBD type
    obd_vals = device_df["obdFormat"].dropna()
    obd_vals = obd_vals[~obd_vals.isin(["unknown", "None"])]
    if len(obd_vals.unique()) == 1:
        d["OBD_Type"] = obd_vals.unique()[0]
    elif len(device_df) == 0:
        d["OBD_Type"] = "No OBS"
    elif len(obd_vals) == 0 and d["IGNITION_ON"] == 0:
        d["OBD_Type"] = "No Data - LP Mode"
    elif len(obd_vals) == 0:
        d["OBD_Type"] = "Disabled / Stack Detection Failed"
    else:
        d["OBD_Type"] = "Multiple OBD"

    # Driver IDs
    did_vals = device_df["driverId"].dropna()
    did_vals = did_vals[did_vals.astype(str) != "None"]
    driver_ids = list(set(j.strip() for i in did_vals for j in str(i).split() if j))
    d["DRIVERID"] = " ".join(f"{x} ," for x in driver_ids) if driver_ids else " "
    d["Diver ID Detected"] = int((device_df["driverId"].fillna("None").astype(str) != "None").sum())

    return d


def _as_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _extract_accuracy_values(acc_str: Any) -> list[float]:
    if acc_str is None:
        return []
    text = str(acc_str)
    if text.strip() in ("", "None", "nan"):
        return []
    vals: list[float] = []
    for tok in text.split(" - "):
        tok = tok.strip()
        if not tok:
            continue
        try:
            vals.append(float(tok))
        except Exception:
            continue
    return vals


def _gps_bucket_counts(values: list[float]) -> dict[str, int]:
    arr = np.array(values, dtype=float) if values else np.array([], dtype=float)
    if arr.size == 0:
        return {
            "0": 0,
            "0 - 3.5": 0,
            "3.5 - 6": 0,
            "6 - 10": 0,
            "10 - 15": 0,
            "15 - 20": 0,
            "20 - 25": 0,
            "25 - 30": 0,
            "> 30": 0,
            "3.5-6": 0,
            "6-15": 0,
            "0-1": 0,
            "1-2": 0,
            "2-3": 0,
            "3-3.5": 0,
        }

    return {
        "0": int(((arr >= 0) & (arr < 1)).sum()),
        "0 - 3.5": int(((arr >= 0) & (arr < 3.5)).sum()),
        "3.5 - 6": int(((arr >= 3.5) & (arr <= 6)).sum()),
        "6 - 10": int(((arr > 6) & (arr <= 10)).sum()),
        "10 - 15": int(((arr > 10) & (arr <= 15)).sum()),
        "15 - 20": int(((arr > 15) & (arr <= 20)).sum()),
        "20 - 25": int(((arr > 20) & (arr <= 25)).sum()),
        "25 - 30": int(((arr > 25) & (arr <= 30)).sum()),
        "> 30": int((arr > 30).sum()),
        "3.5-6": int(((arr >= 3.5) & (arr < 6)).sum()),
        "6-15": int(((arr >= 6) & (arr < 15)).sum()),
        "0-1": int(((arr >= 0) & (arr < 1)).sum()),
        "1-2": int(((arr >= 1) & (arr < 2)).sum()),
        "2-3": int(((arr >= 2) & (arr < 3)).sum()),
        "3-3.5": int(((arr >= 3) & (arr < 3.5)).sum()),
    }


def _build_gps_accuracy_row(device_df: pd.DataFrame, did: Any) -> dict[str, Any]:
    obs_count = len(device_df)
    app_vers = device_df["app_ver"].dropna().unique()
    app_ver = app_vers[0] if len(app_vers) == 1 else "OTA Updated"

    values: list[float] = []
    for v in device_df["VideoMetadata_accuracy"].tolist():
        values.extend(_extract_accuracy_values(v))

    counts = _gps_bucket_counts(values)
    acc_count = int(_as_numeric(device_df["VideoMetadata_accuracy_count"]).fillna(0).sum())
    invalid_acc_count = int(_as_numeric(device_df["VideoMetadata_invalid_accuracy_count"]).fillna(0).sum())

    no_nw_obs = int((_as_numeric(device_df["rssi"]).fillna(np.nan) == 0).sum())
    no_gps = int(device_df["file_name"].fillna("").str.contains("_91.0000_181.0000_").sum())

    min_acc = "No GPS"
    max_acc = "No GPS"
    non_zero = [x for x in values if x != 0]
    if values:
        max_acc = max(values)
    if non_zero:
        min_acc = min(non_zero)

    actual = acc_count if acc_count > 0 else len(values)
    denom = actual if actual > 0 else np.nan

    return {
        "Device ID": did,
        "Obscount": obs_count,
        "Nogps": no_gps,
        "No_Nw_Obs": no_nw_obs,
        "Min": min_acc,
        "Max": max_acc,
        "Exp_Accuracycount": obs_count * 60,
        "Actual_Accuracycount": actual,
        "Invalid_Accuracycount": invalid_acc_count,
        "0": counts["0"],
        "0 - 3.5": counts["0 - 3.5"],
        "3.5 - 6": counts["3.5 - 6"],
        "6 - 10": counts["6 - 10"],
        "10 - 15": counts["10 - 15"],
        "15 - 20": counts["15 - 20"],
        "20 - 25": counts["20 - 25"],
        "25 - 30": counts["25 - 30"],
        "> 30": counts["> 30"],
        "3.5-6": counts["3.5-6"],
        "6-15": counts["6-15"],
        "0-1": counts["0-1"],
        "1-2": counts["1-2"],
        "2-3": counts["2-3"],
        "3-3.5": counts["3-3.5"],
        "App_Ver": app_ver,
        "No Gps %": (counts["0"] / denom * 100) if not pd.isna(denom) else 0,
        "0 - 3.5 Mtrs %": (counts["0 - 3.5"] / denom * 100) if not pd.isna(denom) else 0,
    }


def _build_production_obsdata_row(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "Device ID": summary.get("DEVICEID"),
        "Zips": 0,
        "7Z": 0,
        "Obs": summary.get("NoOfFiles", 0),
        "Faces": summary.get("NoOfFaces", 0),
        "Size": 0,
        "Nogps": summary.get("NoGPS", 0),
        "Emptysensordata": 0,
        "Lte": summary.get("LTE", 0),
        "Idle_Files": summary.get("Idle_files", 0),
        "Metadatacount": summary.get("MetaDataCount", 0),
        "Audioenabled": summary.get("AUDIOENABLED", 0),
        "Privacymode": summary.get("PRIVACYMODE", 0),
        "Lowpowermode": summary.get("LowPowerMode", 0),
        "Partial Files": summary.get("Partial Files", 0),
        "Driverid": summary.get("DRIVERID", ""),
        "Event_Code_Count": summary.get("Event_Code_Count", 0),
        "Obd_Type": summary.get("OBD_Type", ""),
        "App_Ver": summary.get("APP_VER", ""),
        "Min_Filesize": summary.get("Min_FileSize"),
        "Max_Filesize": summary.get("Max_FileSize"),
        "Avg_Filesize": summary.get("Avg_FileSize"),
    }


# ---------------------------------------------------------------------------
# GPS accuracy exploder (for per-device GPS histogram)
# ---------------------------------------------------------------------------

def _explode_gps_accuracy(df: pd.DataFrame) -> pd.DataFrame:
    """Explode videometadata accuracy values into one row per GPS point."""
    records = []
    for _, row in df.iterrows():
        vm = row.get("videometadata")
        if not vm:
            continue
        try:
            if isinstance(vm, str):
                vm = json.loads(vm)
            if not isinstance(vm, list):
                continue
            for item in vm:
                if isinstance(item, dict) and "accuracy" in item and item.get("valid") == 1:
                    records.append({
                        "device_id": row["deviceId"],
                        "VIDEOMETADATA_ACCURACY": float(item["accuracy"]),
                    })
        except Exception:
            pass
    if not records:
        return pd.DataFrame(columns=["device_id", "VIDEOMETADATA_ACCURACY"])
    return pd.DataFrame(records)


def _build_gps_summary(device_df: pd.DataFrame, did: Any, acc_df: pd.DataFrame) -> dict:
    dev_acc = acc_df[acc_df["device_id"] == str(did)]
    d: dict = {"DEVICEID": did}

    app_vers = device_df["app_ver"].dropna().unique()
    d["APP_VER"] = app_vers[0] if len(app_vers) == 1 else "OTA Updated"
    d["OBSCount"] = len(device_df)

    try:
        rssi_tmp = device_df[device_df["rssi"].notna()]
        d["No_Nw_Obs"] = int((rssi_tmp["rssi"].astype(float) == 0).sum())
    except Exception:
        d["No_Nw_Obs"] = 0

    d["NoGPS"] = int(device_df["file_name"].fillna("").str.contains("_91.0000_181.0000_").sum())
    d["Exp_AccuracyCount"] = len(device_df) * 60
    d["Actual_AccuracyCount"] = len(dev_acc.dropna())
    d["Invalid_AccuracyCount"] = int(device_df["VideoMetadata_invalid_accuracy_count"].fillna(0).sum())

    if len(dev_acc) > 0:
        d["Max"] = dev_acc["VIDEOMETADATA_ACCURACY"].max()
        valid_acc = dev_acc[dev_acc["VIDEOMETADATA_ACCURACY"] != 0]["VIDEOMETADATA_ACCURACY"]
        d["Min"] = valid_acc.min() if len(valid_acc) > 0 else "No GPS"
        if d["Max"] == 0:
            d["Max"] = "No GPS"
    else:
        d["Max"] = "No GPS"
        d["Min"] = "No GPS"

    def _bucket(lo, hi, inclusive_lo=True, inclusive_hi=False):
        mask = pd.Series([True] * len(dev_acc))
        if inclusive_lo:
            mask = mask & (dev_acc["VIDEOMETADATA_ACCURACY"] >= lo)
        else:
            mask = mask & (dev_acc["VIDEOMETADATA_ACCURACY"] > lo)
        if hi is not None:
            if inclusive_hi:
                mask = mask & (dev_acc["VIDEOMETADATA_ACCURACY"] <= hi)
            else:
                mask = mask & (dev_acc["VIDEOMETADATA_ACCURACY"] < hi)
        return int(mask.sum())

    d["0"] = _bucket(0, 1, inclusive_lo=True, inclusive_hi=False)
    d["0 - 3.5"] = _bucket(0, 3.5, inclusive_lo=True, inclusive_hi=False)
    d["3.5 - 6"] = _bucket(3.5, 6.0, inclusive_lo=True, inclusive_hi=True)
    d["6 - 10"] = _bucket(6.0, 10, inclusive_lo=False, inclusive_hi=True)
    d["10 - 15"] = _bucket(10.0, 15, inclusive_lo=False, inclusive_hi=True)
    d["15 - 20"] = _bucket(15.0, 20, inclusive_lo=False, inclusive_hi=True)
    d["20 - 25"] = _bucket(20.0, 25, inclusive_lo=False, inclusive_hi=True)
    d["25 - 30"] = _bucket(25.0, 30, inclusive_lo=False, inclusive_hi=True)
    d["> 30"] = _bucket(30, None, inclusive_lo=False)
    d["3.5-6"] = _bucket(3.5, 6, inclusive_lo=True, inclusive_hi=False)
    d["6-15"] = _bucket(6, 15, inclusive_lo=True, inclusive_hi=False)
    d["0-1"] = _bucket(0, 1, inclusive_lo=True, inclusive_hi=False)
    d["1-2"] = _bucket(1, 2, inclusive_lo=True, inclusive_hi=False)
    d["2-3"] = _bucket(2, 3, inclusive_lo=True, inclusive_hi=False)
    d["3-3.5"] = _bucket(3, 3.5, inclusive_lo=True, inclusive_hi=False)

    return d


# ---------------------------------------------------------------------------
# Main entry: generate both summaries
# ---------------------------------------------------------------------------

def generate_summaries(start_dt: str, end_dt: str, output_dir: str, product_family: str | None = None) -> None:
    os.makedirs(output_dir, exist_ok=True)
    timestr = time.strftime("%Y-%m-%d-%H_%M")

    family_label = product_family or "all"
    print(f"Fetching extracteddata for family={family_label}: {start_dt} → {end_dt}")
    raw_df = fetch_extracteddata(start_dt, end_dt, product_family=product_family)
    print(f"Fetched {len(raw_df)} rows for {raw_df['device_id'].nunique()} devices")

    if raw_df.empty:
        print(f"No data found for product family '{family_label}' in the given range.")
        return

    # Build flat row dicts (one per OBS file)
    print("Building per-file summary rows...")
    flat_rows = [_build_row(row) for _, row in raw_df.iterrows()]
    flat_df = pd.DataFrame(flat_rows)

    # Copy back device_id column (needed for grouping)
    flat_df["device_id"] = raw_df["device_id"].values

    # Explode GPS accuracy for GPS histogram
    print("Exploding GPS accuracy data...")
    acc_df = _explode_gps_accuracy(flat_df)
    # normalize device_id type
    acc_df["device_id"] = acc_df["device_id"].astype(str)
    flat_df["device_id"] = flat_df["device_id"].astype(str)

    device_ids = sorted(flat_df["device_id"].unique())

    # -----------------------------------------------------------------------
    # OH Summary Excel (production-style consolidated sheets)
    # -----------------------------------------------------------------------
    obs_summary_path = os.path.join(output_dir, f"OH_Summary_{timestr}.xlsx")
    print(f"Writing OH Summary → {obs_summary_path}")

    obs_summary_rows: list[dict] = []
    with pd.ExcelWriter(obs_summary_path, engine="xlsxwriter") as writer:
        for did in device_ids:
            dev_df = flat_df[flat_df["device_id"] == str(did)].copy()
            if dev_df.empty:
                continue
            obs_summary_rows.append(_build_per_device_summary(dev_df, did, flat_rows))

        # ObsData summary sheet (matches production naming/columns)
        if obs_summary_rows:
            summary_df = pd.DataFrame([_build_production_obsdata_row(r) for r in obs_summary_rows])
            summary_df.to_excel(writer, sheet_name="ObsData", index=False, engine="xlsxwriter")
            ws = writer.sheets["ObsData"]
            for col, width in enumerate(_get_col_widths(summary_df)):
                ws.set_column(col - 1, col - 1, width + 2)

        # Alert code pivot (production sheet name)
        try:
            alert_data = flat_df[["device_id", "Alertcode"]].copy()
            alert_data = alert_data[alert_data["Alertcode"].str.strip().str.len() > 0]
            if len(alert_data) > 0:
                alert_data["Alertcode"] = alert_data["Alertcode"].str.strip(" -").str.strip()
                alert_expanded = (
                    alert_data
                    .set_index("device_id")["Alertcode"]
                    .str.split(" - ", expand=True)
                    .stack()
                    .reset_index(level=1, drop=True)
                    .reset_index()
                    .rename(columns={0: "Alertcode"})
                )
                alert_expanded = alert_expanded[alert_expanded["Alertcode"].str.strip() != ""]
                alert_pivot = pd.crosstab(alert_expanded["Alertcode"], alert_expanded["device_id"].fillna("n/a"))
                alert_pivot.to_excel(writer, sheet_name="AlertSummary", index=True, engine="xlsxwriter")
            else:
                pd.DataFrame(columns=["ALERTCODE"]).to_excel(writer, sheet_name="AlertSummary", index=False, engine="xlsxwriter")
        except Exception as e:
            print(f"  Warning: AlertSummary sheet skipped: {e}")

        # NRT summary
        try:
            nrt_df = flat_df[["device_id", "app_ver", "udid", "NRT_STATUS", "file_name"]].copy()
            nrt_df = nrt_df.rename(columns={
                "device_id": "Device ID",
                "app_ver": "App_Ver",
                "udid": "Udid",
                "NRT_STATUS": "Nrt Status",
                "file_name": "File Name",
            })
            nrt_df.to_excel(writer, sheet_name="NRT_Summary", index=False, engine="xlsxwriter")
        except Exception as e:
            print(f"  Warning: NRT_Summary sheet skipped: {e}")

        # CrankOff_Processed placeholder sheet (kept for compatibility with production format)
        pd.DataFrame().to_excel(writer, sheet_name="CrankOff_Processed", index=False, engine="xlsxwriter")

        # Metadata-only files sheet
        try:
            meta_df = flat_df[flat_df["OBSFileType"].fillna("").str.contains("Metadata")][
                ["device_id", "file_name", "StartTime", "EndTime", "Uptime", "serviceUpTime", "ignition_status", "idle"]
            ]
            meta_df = meta_df.rename(columns={
                "device_id": "Device ID",
                "file_name": "File Name",
                "StartTime": "Start Time",
                "EndTime": "End Time",
                "serviceUpTime": "Service Uptime",
                "ignition_status": "Ignition_Status",
                "idle": "Idle",
            })
            if "Start Time" in meta_df.columns:
                meta_df["Start Time"] = meta_df["Start Time"].astype(str)
            if "End Time" in meta_df.columns:
                meta_df["End Time"] = meta_df["End Time"].astype(str)
            meta_df.to_excel(writer, sheet_name="MetaData", index=False, engine="xlsxwriter")
        except Exception as e:
            print(f"  Warning: MetaData sheet skipped: {e}")

        # MetadataSummary
        try:
            if obs_summary_rows:
                msum = pd.DataFrame({
                    "Device ID": [r.get("DEVICEID") for r in obs_summary_rows],
                    "Nooffiles": [r.get("NoOfFiles", 0) for r in obs_summary_rows],
                    "Gps Time Mismatch": [r.get("GPS Time Mismatch", 0) for r in obs_summary_rows],
                    "Metadatacount": [r.get("MetaDataCount", 0) for r in obs_summary_rows],
                    "Crankoffmetadata": [r.get("CrankOffMetadata", 0) for r in obs_summary_rows],
                    "Crankonmetadata": [r.get("CrankONMetadata", 0) for r in obs_summary_rows],
                })
                msum["Percentage"] = (
                    np.where(msum["Nooffiles"] > 0, (msum["Metadatacount"] / msum["Nooffiles"]) * 100, 0)
                )
            else:
                msum = pd.DataFrame(columns=[
                    "Device ID", "Nooffiles", "Gps Time Mismatch", "Metadatacount",
                    "Crankoffmetadata", "Crankonmetadata", "Percentage"
                ])
            msum.to_excel(writer, sheet_name="MetadataSummary", index=False, engine="xlsxwriter")
        except Exception as e:
            print(f"  Warning: MetadataSummary sheet skipped: {e}")

        # WrongGPS
        try:
            wrong = flat_df[flat_df["file_name"].fillna("").str.contains("_91.0000_181.0000_")][
                ["device_id", "app_ver", "file_name", "StartTime", "EndTime", "Uptime", "serviceUpTime", "ignition_status"]
            ].rename(columns={
                "device_id": "Device ID",
                "app_ver": "App_Ver",
                "file_name": "File Name",
                "StartTime": "Start Time",
                "EndTime": "End Time",
                "serviceUpTime": "Service Uptime",
                "ignition_status": "Ignition_Status",
            })
            if "End Time" not in wrong.columns and "EndTime" in wrong.columns:
                wrong = wrong.rename(columns={"EndTime": "End Time"})
            wrong.to_excel(writer, sheet_name="WrongGPS", index=False, engine="xlsxwriter")
        except Exception as e:
            print(f"  Warning: WrongGPS sheet skipped: {e}")

        # PartialFiles
        try:
            partial = flat_df[flat_df["metadataStatus"].fillna("None").astype(str) != "None"][
                ["device_id", "app_ver", "metadataStatus", "file_name", "StartTime", "EndTime", "Uptime", "serviceUpTime", "ignition_status"]
            ].rename(columns={
                "device_id": "Device ID",
                "app_ver": "App_Ver",
                "metadataStatus": "Metadatastatus",
                "file_name": "File Name",
                "StartTime": "Start Time",
                "EndTime": "End Time",
                "serviceUpTime": "Service Uptime",
                "ignition_status": "Ignition_Status",
            })
            partial.to_excel(writer, sheet_name="PartialFiles", index=False, engine="xlsxwriter")
        except Exception as e:
            print(f"  Warning: PartialFiles sheet skipped: {e}")

        # GPS Accuracy sheets in OH workbook (production naming)
        try:
            gps_rows = []
            gps_rows_ig1 = []
            gps_rows_speed = []

            for did in device_ids:
                dev_df = flat_df[flat_df["device_id"] == str(did)].copy()
                if dev_df.empty:
                    continue
                gps_rows.append(_build_gps_accuracy_row(dev_df, did))

                ig1_df = dev_df[
                    (_as_numeric(dev_df["ignition_status"]) == 1)
                    & (_as_numeric(dev_df["Uptime"]) > 300)
                ]
                if not ig1_df.empty:
                    row_ig1 = _build_gps_accuracy_row(ig1_df, did)
                    denom = row_ig1["Actual_Accuracycount"] if row_ig1["Actual_Accuracycount"] else np.nan
                    gps_rows_ig1.append({
                        "Device ID": row_ig1["Device ID"],
                        "Obscount": row_ig1["Obscount"],
                        "Nogps": row_ig1["Nogps"],
                        "No_Nw_Obs": row_ig1["No_Nw_Obs"],
                        "Min": row_ig1["Min"],
                        "Max": row_ig1["Max"],
                        "Exp_Accuracycount": row_ig1["Exp_Accuracycount"],
                        "Actual_Accuracycount": row_ig1["Actual_Accuracycount"],
                        "Invalid_Accuracycount": row_ig1["Invalid_Accuracycount"],
                        "App_Ver": row_ig1["App_Ver"],
                        "No Gps %": round((row_ig1["0"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "3.5 Mtrs %": round((row_ig1["0 - 3.5"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "3.6 - 5 Mtrs %": round((((row_ig1["3.5 - 6"] - row_ig1["3.5-6"]) / denom) * 100), 2) if not pd.isna(denom) else 0,
                        "5 - 10 Mtrs %": round((row_ig1["6 - 10"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "10 - 15 Mtrs %": round((row_ig1["10 - 15"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "15 - 20 Mtrs %": round((row_ig1["15 - 20"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "20 - 25 Mtrs %": round((row_ig1["20 - 25"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "25 - 30 Mtrs %": round((row_ig1["25 - 30"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "> 30 Mtrs %": round((row_ig1["> 30"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "3.5-6 Mtrs %": round((row_ig1["3.5-6"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "6-15 Mtrs %": round((row_ig1["6-15"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "0-1 Mtrs %": round((row_ig1["0-1"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "1-2 Mtrs %": round((row_ig1["1-2"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "2-3 Mtrs %": round((row_ig1["2-3"] / denom * 100), 2) if not pd.isna(denom) else 0,
                        "3-3.5 Mtrs %": round((row_ig1["3-3.5"] / denom * 100), 2) if not pd.isna(denom) else 0,
                    })

                speed_df = ig1_df[_as_numeric(ig1_df["Max_Speed"]) > 5]
                if not speed_df.empty:
                    row_speed = _build_gps_accuracy_row(speed_df, did)
                    denom_s = row_speed["Actual_Accuracycount"] if row_speed["Actual_Accuracycount"] else np.nan
                    gps_rows_speed.append({
                        "Device ID": row_speed["Device ID"],
                        "Obscount": row_speed["Obscount"],
                        "Nogps": row_speed["Nogps"],
                        "No_Nw_Obs": row_speed["No_Nw_Obs"],
                        "Min": row_speed["Min"],
                        "Max": row_speed["Max"],
                        "Exp_Accuracycount": row_speed["Exp_Accuracycount"],
                        "Actual_Accuracycount": row_speed["Actual_Accuracycount"],
                        "Invalid_Accuracycount": row_speed["Invalid_Accuracycount"],
                        "App_Ver": row_speed["App_Ver"],
                        "No Gps %": round((row_speed["0"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "3.5 Mtrs %": round((row_speed["0 - 3.5"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "3.6 - 5 Mtrs %": round((((row_speed["3.5 - 6"] - row_speed["3.5-6"]) / denom_s) * 100), 2) if not pd.isna(denom_s) else 0,
                        "5 - 10 Mtrs %": round((row_speed["6 - 10"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "10 - 15 Mtrs %": round((row_speed["10 - 15"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "15 - 20 Mtrs %": round((row_speed["15 - 20"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "20 - 25 Mtrs %": round((row_speed["20 - 25"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "25 - 30 Mtrs %": round((row_speed["25 - 30"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "> 30 Mtrs %": round((row_speed["> 30"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "3.5-6 Mtrs %": round((row_speed["3.5-6"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "6-15 Mtrs %": round((row_speed["6-15"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "0-1 Mtrs %": round((row_speed["0-1"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "1-2 Mtrs %": round((row_speed["1-2"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "2-3 Mtrs %": round((row_speed["2-3"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                        "3-3.5 Mtrs %": round((row_speed["3-3.5"] / denom_s * 100), 2) if not pd.isna(denom_s) else 0,
                    })

            gps_acc_df = pd.DataFrame(gps_rows)
            if not gps_acc_df.empty:
                gps_acc_df.to_excel(writer, sheet_name="GPS Accuracy", index=False, engine="xlsxwriter")

            gps_ig1_df = pd.DataFrame(gps_rows_ig1)
            if not gps_ig1_df.empty:
                gps_ig1_df.to_excel(writer, sheet_name="GPS Accuracy ig1_UptimeGT5Min", index=False, engine="xlsxwriter")
            else:
                pd.DataFrame(columns=[
                    "Device ID", "Obscount", "Nogps", "No_Nw_Obs", "Min", "Max", "Exp_Accuracycount",
                    "Actual_Accuracycount", "Invalid_Accuracycount", "App_Ver", "No Gps %", "3.5 Mtrs %",
                    "3.6 - 5 Mtrs %", "5 - 10 Mtrs %", "10 - 15 Mtrs %", "15 - 20 Mtrs %", "20 - 25 Mtrs %",
                    "25 - 30 Mtrs %", "> 30 Mtrs %", "3.5-6 Mtrs %", "6-15 Mtrs %", "0-1 Mtrs %", "1-2 Mtrs %",
                    "2-3 Mtrs %", "3-3.5 Mtrs %"
                ]).to_excel(writer, sheet_name="GPS Accuracy ig1_UptimeGT5Min", index=False, engine="xlsxwriter")

            gps_speed_df = pd.DataFrame(gps_rows_speed)
            if not gps_speed_df.empty:
                gps_speed_df.to_excel(writer, sheet_name="ig1_speedGT5", index=False, engine="xlsxwriter")
            else:
                pd.DataFrame(columns=[
                    "Device ID", "Obscount", "Nogps", "No_Nw_Obs", "Min", "Max", "Exp_Accuracycount",
                    "Actual_Accuracycount", "Invalid_Accuracycount", "App_Ver", "No Gps %", "3.5 Mtrs %",
                    "3.6 - 5 Mtrs %", "5 - 10 Mtrs %", "10 - 15 Mtrs %", "15 - 20 Mtrs %", "20 - 25 Mtrs %",
                    "25 - 30 Mtrs %", "> 30 Mtrs %", "3.5-6 Mtrs %", "6-15 Mtrs %", "0-1 Mtrs %", "1-2 Mtrs %",
                    "2-3 Mtrs %", "3-3.5 Mtrs %"
                ]).to_excel(writer, sheet_name="ig1_speedGT5", index=False, engine="xlsxwriter")
        except Exception as e:
            print(f"  Warning: GPS Accuracy sheets skipped: {e}")

    print(f"  OH Summary written: {obs_summary_path}")

    # -----------------------------------------------------------------------
    # GPS Summary Excel (consolidated only)
    # -----------------------------------------------------------------------
    gps_summary_path = os.path.join(output_dir, f"GPS_Summary_{timestr}.xlsx")
    print(f"Writing GPS Summary → {gps_summary_path}")

    gps_summary_rows: list[dict] = []
    with pd.ExcelWriter(gps_summary_path, engine="xlsxwriter") as writer:
        for did in device_ids:
            dev_df = flat_df[flat_df["device_id"] == str(did)].copy()
            if dev_df.empty:
                continue
            gps_summary_rows.append(_build_gps_summary(dev_df, did, acc_df))

        # GPS_Acc Summary sheet
        if gps_summary_rows:
            gps_sum_df = pd.DataFrame(gps_summary_rows)
            # Add percentage columns
            total = gps_sum_df["Actual_AccuracyCount"].replace(0, np.nan)
            for bucket, label in [
                ("0", "No GPS %"), ("0 - 3.5", "0-3.5m %"), ("3.5 - 6", "3.5-6m %"),
                ("6 - 10", "6-10m %"), ("10 - 15", "10-15m %"), ("15 - 20", "15-20m %"),
                ("20 - 25", "20-25m %"), ("25 - 30", "25-30m %"), ("> 30", ">30m %"),
            ]:
                if bucket in gps_sum_df.columns:
                    gps_sum_df[label] = (gps_sum_df[bucket] / total * 100).round(2)
            gps_sum_df.to_excel(writer, sheet_name="GPS_Acc Summary", index=False, engine="xlsxwriter")
            ws = writer.sheets["GPS_Acc Summary"]
            for col, width in enumerate(_get_col_widths(gps_sum_df)):
                ws.set_column(col - 1, col - 1, width + 2)

    print(f"  GPS Summary written: {gps_summary_path}")
    print("Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate OH/GPS summaries from public.extracteddata")
    p.add_argument("--start", required=True, help="Start datetime (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)")
    p.add_argument("--end", required=True, help="End datetime (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)")
    p.add_argument(
        "--product-family",
        default=None,
        help="Comma-separated product family list (for example: octo or octo,krait). Defaults to PRODUCT_LINES from .env.",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Base output directory. Defaults to OUTPUT/obs_summaries/<product-family>/<start>_<end>/",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    start = args.start.strip()
    end = args.end.strip()
    product_families = _normalize_product_families(args.product_family) if args.product_family else _load_default_product_families()
    if not product_families:
        product_families = sorted(FAMILY_CONFIG.keys())

    base_output_root = args.output if args.output else DEFAULT_OUTPUT_ROOT

    print(f"Generating summaries for product families: {', '.join(product_families)}")
    for product_family in product_families:
        out_dir = _build_output_dir(base_output_root, product_family, start, end)
        print(f"\n=== Product family: {product_family} ===")
        print(f"Output directory: {out_dir}")
        generate_summaries(start, end, out_dir, product_family=product_family)
