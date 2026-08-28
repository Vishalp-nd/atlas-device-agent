from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import psycopg2
import streamlit as st
from dotenv import load_dotenv

PIPELINE_ROOT = Path(__file__).resolve().parents[1] / "pipeline"
if str(PIPELINE_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PIPELINE_ROOT))

from fetch_device_config import read_db_config

DEFAULT_TABLE = "criticalinfo_snowflakes_data"
DEFAULT_SECTION = "IRAVATH_DB"
PRIORITY_TABLE = "unique_cinfo_priority_map"


@dataclass(frozen=True)
class DashboardConfig:
    repo_root: Path
    table_name: str = DEFAULT_TABLE
    postgres_section: str = DEFAULT_SECTION


def _load_env(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _parse_env_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def configured_ota_versions(repo_root: Path) -> list[str]:
    _load_env(repo_root)
    for key in ("CINFO_REPORT", "CINFO_OTA_VERSIONS", "OTA_VERSIONS"):
        value = os.getenv(key, "").strip()
        if value:
            return _parse_env_list(value)
    return []


@st.cache_resource(show_spinner=False)
def get_connection(repo_root: str, postgres_section: str):
    db_config_path = Path(repo_root) / "db_credentials.ini"
    params = read_db_config(str(db_config_path), postgres_section)
    conn = psycopg2.connect(**params)
    conn.autocommit = True
    return conn


def _read_sql(repo_root: Path, postgres_section: str, sql: str, params: tuple | None = None) -> pd.DataFrame:
    conn = get_connection(str(repo_root), postgres_section)
    return pd.read_sql_query(sql, conn, params=params)


def load_dashboard_frame(config: DashboardConfig) -> pd.DataFrame:
    sql = f'''
        SELECT
            "DEVICE_ID",
            "TIMESTAMP",
            "PROCESS_NAME",
            "CODE",
            "CODE_AUX",
            "COUNT",
            "DESCRIPTION",
            "DEVICE_VERSION",
            type
        FROM "{config.table_name}"
    '''
    frame = _read_sql(config.repo_root, config.postgres_section, sql)
    if frame.empty:
        return frame
    frame["TIMESTAMP"] = pd.to_datetime(frame["TIMESTAMP"], errors="coerce")
    frame["COUNT"] = pd.to_numeric(frame["COUNT"], errors="coerce").fillna(0)
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["DEVICE_VERSION"] = frame["DEVICE_VERSION"].fillna("UNKNOWN").astype(str)
    frame["DEVICE_ID"] = frame["DEVICE_ID"].fillna("").astype(str)
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    return frame


def load_priority_map(config: DashboardConfig) -> pd.DataFrame:
    sql = f'''
        SELECT
            "CODE",
            sample_description,
            description_pattern,
            "TYPE",
            priority
        FROM "{PRIORITY_TABLE}"
    '''
    try:
        frame = _read_sql(config.repo_root, config.postgres_section, sql)
    except Exception:
        return pd.DataFrame(columns=["CODE", "sample_description", "description_pattern", "TYPE", "priority"])
    if frame.empty:
        return frame
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["priority"] = frame["priority"].fillna("UNMAPPED").astype(str).str.upper()
    frame["TYPE"] = frame["TYPE"].fillna("").astype(str).str.upper()
    return frame


def build_enriched_frame(config: DashboardConfig) -> pd.DataFrame:
    events = load_dashboard_frame(config)
    if events.empty:
        return events
    priority_map = load_priority_map(config)
    if priority_map.empty:
        events["priority"] = "UNMAPPED"
        return events

    merged = events.merge(
        priority_map[["CODE", "TYPE", "priority"]].drop_duplicates(),
        how="left",
        left_on=["CODE", "type"],
        right_on=["CODE", "TYPE"],
    )
    merged["priority"] = merged["priority"].fillna("UNMAPPED")
    return merged


def ota_summary(frame: pd.DataFrame, ota_versions: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["DEVICE_VERSION", "type", "events"])
    filtered = frame[frame["DEVICE_VERSION"].isin(ota_versions)] if ota_versions else frame.copy()
    if filtered.empty:
        return pd.DataFrame(columns=["DEVICE_VERSION", "type", "events"])
    summary = (
        filtered.groupby(["DEVICE_VERSION", "type"], dropna=False)["COUNT"]
        .sum()
        .reset_index(name="events")
        .sort_values(["DEVICE_VERSION", "type"])
    )
    return summary


def ota_detail(frame: pd.DataFrame, ota_version: str, device_ids: list[str] | None = None) -> pd.DataFrame:
    filtered = frame[frame["DEVICE_VERSION"] == ota_version].copy()
    if device_ids:
        filtered = filtered[filtered["DEVICE_ID"].isin(device_ids)]
    return filtered