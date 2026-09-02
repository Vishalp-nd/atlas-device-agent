from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from atlas.critical_events_dashboard import (
    DashboardConfig,
    configured_ota_versions,
    load_ota_date_bounds,
    load_ota_daily_counts,
    load_ota_detail,
    load_ota_devices,
    load_ota_priority_counts,
    load_ota_summary,
    load_ota_top_code_details,
    load_ota_top_codes,
    load_ota_top_devices,
    load_ota_top_processes,
    load_ota_type_counts,
)
from atlas.streamlit_ui import configure_app


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = DashboardConfig(repo_root=REPO_ROOT)
DETAIL_TABLE_LIMIT = 10


def _render_sidebar() -> None:
    with st.sidebar:
        st.page_link("streamlit_app.py", label="Dashboard")
        st.markdown("### Agents")
        st.page_link("pages/1_Atlas.py", label="Atlas", icon=":material/precision_manufacturing:")
        st.page_link("pages/2_Cypher.py", label="Cypher", icon=":material/account_tree:")
        st.page_link("pages/3_Critical_Events_Monitor.py", label="Critical Events Monitor", icon=":material/monitoring:")


@st.cache_data(show_spinner=True, ttl=300)
def _load_summary(ota_versions: tuple[str, ...]) -> pd.DataFrame:
    return load_ota_summary(CONFIG, list(ota_versions))


@st.cache_data(show_spinner=True, ttl=300)
def _load_detail(
    ota_version: str,
    device_ids: tuple[str, ...],
    start_date: str | None,
    end_date_exclusive: str | None,
) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_detail(CONFIG, ota_version, list(device_ids), start_ts=start_ts, end_ts=end_ts, limit=DETAIL_TABLE_LIMIT)


@st.cache_data(show_spinner=True, ttl=300)
def _load_date_bounds(ota_version: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    return load_ota_date_bounds(CONFIG, ota_version)


@st.cache_data(show_spinner=True, ttl=300)
def _load_devices(ota_version: str, start_date: str | None, end_date_exclusive: str | None) -> list[str]:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_devices(CONFIG, ota_version, start_ts=start_ts, end_ts=end_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_type_counts(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_type_counts(CONFIG, ota_version, list(device_ids), start_ts=start_ts, end_ts=end_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_priority_counts(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_priority_counts(CONFIG, ota_version, list(device_ids), start_ts=start_ts, end_ts=end_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_daily_counts(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_daily_counts(CONFIG, ota_version, list(device_ids), start_ts=start_ts, end_ts=end_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_top_processes(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_top_processes(CONFIG, ota_version, list(device_ids), start_ts=start_ts, end_ts=end_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_top_codes(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_top_codes(CONFIG, ota_version, list(device_ids), start_ts=start_ts, end_ts=end_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_top_code_details(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_top_code_details(CONFIG, ota_version, list(device_ids), start_ts=start_ts, end_ts=end_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_top_devices(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date_exclusive) if end_date_exclusive else None
    return load_ota_top_devices(CONFIG, ota_version, list(device_ids), start_ts=start_ts, end_ts=end_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_ota_page_data(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> dict[str, pd.DataFrame]:
    loaders = {
        "type_counts": lambda: _load_type_counts(ota_version, device_ids, start_date, end_date_exclusive),
        "priority_counts": lambda: _load_priority_counts(ota_version, device_ids, start_date, end_date_exclusive),
        "daily_counts": lambda: _load_daily_counts(ota_version, device_ids, start_date, end_date_exclusive),
        "process_counts": lambda: _load_top_processes(ota_version, device_ids, start_date, end_date_exclusive),
        "code_counts": lambda: _load_top_codes(ota_version, device_ids, start_date, end_date_exclusive),
        "code_details": lambda: _load_top_code_details(ota_version, device_ids, start_date, end_date_exclusive),
        "device_counts": lambda: _load_top_devices(ota_version, device_ids, start_date, end_date_exclusive),
        "filtered": lambda: _load_detail(ota_version, device_ids, start_date, end_date_exclusive),
    }
    with ThreadPoolExecutor(max_workers=len(loaders)) as executor:
        futures = {name: executor.submit(loader) for name, loader in loaders.items()}
    return {name: future.result() for name, future in futures.items()}


def _pie(data: pd.DataFrame, names: str, values: str, title: str, hole: float = 0.45):
    fig = px.pie(data, names=names, values=values, hole=hole)
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(title=title, margin=dict(l=10, r=10, t=50, b=10), legend_title_text="")
    return fig


def _bar(data: pd.DataFrame, x: str, y: str, color: str | None, title: str):
    fig = px.bar(data, x=x, y=y, color=color, title=title)
    fig.update_layout(margin=dict(l=10, r=10, t=50, b=10), xaxis_title="", yaxis_title="Events")
    return fig


def _categorical_bar(data: pd.DataFrame, x: str, y: str, title: str):
    plot_data = data.copy()
    plot_data[x] = plot_data[x].astype(str)
    fig = px.bar(plot_data, x=x, y=y, title=title)
    fig.update_layout(
        margin=dict(l=10, r=10, t=50, b=10),
        xaxis_title="",
        yaxis_title="Events",
        xaxis={"type": "category", "categoryorder": "array", "categoryarray": plot_data[x].tolist()},
    )
    fig.update_xaxes(tickmode="array", tickvals=plot_data[x].tolist(), ticktext=plot_data[x].tolist())
    return fig


def _render_home(summary: pd.DataFrame, ota_versions: list[str]) -> None:
    st.subheader("Production OTA overview")
    if summary.empty:
        st.info("No production critical-events data found for the OTA versions configured in .env.")
        return

    col1, col2 = st.columns([1.2, 1])
    with col1:
        st.plotly_chart(_pie(summary, "DEVICE_VERSION", "events", "Event share by OTA"), use_container_width=True)
    with col2:
        type_totals = summary.groupby("type", as_index=False)["events"].sum()
        st.plotly_chart(_pie(type_totals, "type", "events", "Error vs Info split"), use_container_width=True)

    st.markdown("### OTA tiles")
    tile_cols = st.columns(3)
    totals = summary.groupby("DEVICE_VERSION", as_index=False)["events"].sum().sort_values("DEVICE_VERSION")
    type_lookup = summary.pivot_table(index="DEVICE_VERSION", columns="type", values="events", aggfunc="sum", fill_value=0)
    for idx, row in enumerate(totals.itertuples(index=False)):
        ota = row.DEVICE_VERSION
        error_count = int(type_lookup.loc[ota].get("ERROR", 0)) if ota in type_lookup.index else 0
        info_count = int(type_lookup.loc[ota].get("INFO", 0)) if ota in type_lookup.index else 0
        with tile_cols[idx % 3]:
            st.metric(ota, int(row.events), help="Total weighted events for this OTA")
            st.caption(f"Error: {error_count} | Info: {info_count}")
            if st.button(f"Open {ota}", key=f"open_{ota}", use_container_width=True):
                st.query_params["ota"] = ota
                st.rerun()


def _render_ota_page(ota_version: str) -> None:
    st.subheader(f"OTA detail: {ota_version}")
    min_ts, max_ts = _load_date_bounds(ota_version)
    if min_ts is None or max_ts is None or pd.isna(min_ts) or pd.isna(max_ts):
        st.info("No data found for the selected OTA.")
        return

    st.caption(
        f"Available data range: {min_ts.strftime('%Y-%m-%d %H:%M:%S')} to {max_ts.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    filter_cols = st.columns([1, 1, 1.6])
    min_date = min_ts.date()
    max_date = max_ts.date()
    default_start = max(min_date, (max_ts - pd.Timedelta(days=1)).date())
    default_end = max_date
    with filter_cols[0]:
        start_date = st.date_input("Start date", value=default_start, min_value=min_date, max_value=max_date)
    with filter_cols[1]:
        end_date = st.date_input("End date", value=default_end, min_value=min_date, max_value=max_date)

    if start_date > end_date:
        st.warning("Start date must be on or before end date.")
        return

    start_date_str = start_date.isoformat()
    end_date_exclusive_str = (pd.Timestamp(end_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    available_devices = _load_devices(ota_version, start_date_str, end_date_exclusive_str)
    with filter_cols[2]:
        selected_devices = st.multiselect(
            "Filter by device ID",
            available_devices,
            placeholder="Search and select device IDs",
        )

    selected_device_ids = tuple(selected_devices)
    page_data = _load_ota_page_data(ota_version, selected_device_ids, start_date_str, end_date_exclusive_str)
    type_counts = page_data["type_counts"]
    if type_counts.empty:
        st.info("No data found for the selected OTA and date range.")
        return

    priority_counts = page_data["priority_counts"]
    daily_counts = page_data["daily_counts"]
    process_counts = page_data["process_counts"]
    code_counts = page_data["code_counts"]
    code_details = page_data["code_details"]
    device_counts = page_data["device_counts"]
    filtered = page_data["filtered"]

    if st.button("Back to OTA overview"):
        st.query_params.clear()
        st.rerun()

    top_row = st.columns(2)
    with top_row[0]:
        if priority_counts.empty:
            st.info("No mapped error priorities found for this OTA.")
        else:
            st.plotly_chart(_pie(priority_counts, "priority", "events", "Error priority split"), use_container_width=True)
    with top_row[1]:
        st.plotly_chart(_pie(type_counts, "type", "events", "Errors vs Info"), use_container_width=True)

    trend_cols = st.columns(2)
    if not daily_counts.empty:
        with trend_cols[0]:
            st.plotly_chart(_bar(daily_counts, "day", "events", "type", "Daily event trend"), use_container_width=True)

    with trend_cols[1]:
        st.plotly_chart(_bar(process_counts, "PROCESS_NAME", "events", None, "Top noisy processes"), use_container_width=True)

    bottom_cols = st.columns(2)
    with bottom_cols[0]:
        if code_counts.empty:
            st.info("No error codes found for this OTA selection.")
        else:
            st.plotly_chart(_categorical_bar(code_counts, "CODE", "events", "Top error codes"), use_container_width=True)
    with bottom_cols[1]:
        st.plotly_chart(_categorical_bar(device_counts, "DEVICE_ID", "events", "Most affected devices"), use_container_width=True)

    st.markdown("### Top Error Code Details")
    if code_details.empty:
        st.info("No top error code detail rows found for this OTA selection.")
    else:
        detail_frame = code_details[["CODE", "description_pattern", "events"]].reset_index(drop=True)
        st.caption("Breakdown of the current Top Error Codes by code and description pattern.")
        st.table(detail_frame)

    st.markdown("### Filtered rows")
    if filtered.empty:
        st.info(f"Showing 0 rows in the latest {DETAIL_TABLE_LIMIT} records for this selection.")
        return
    table_frame = (
        filtered[["TIMESTAMP", "DEVICE_ID", "PROCESS_NAME", "CODE", "DESCRIPTION", "type", "priority", "COUNT"]]
        .sort_values("TIMESTAMP", ascending=False)
        .reset_index(drop=True)
    )
    st.caption(f"Showing the latest {len(table_frame)} rows for this selection. Charts are aggregated across the full selected date range.")
    st.table(table_frame)


def main() -> None:
    configure_app()
    _render_sidebar()
    st.title("Critical Events Monitor")
    st.caption("Production dashboard backed by ClickHouse summary and detail queries.")

    ota_versions = configured_ota_versions(REPO_ROOT)
    summary = _load_summary(tuple(ota_versions))
    selected_ota = st.query_params.get("ota")

    if selected_ota:
        _render_ota_page(selected_ota)
    else:
        _render_home(summary, ota_versions)


if __name__ == "__main__":
    main()