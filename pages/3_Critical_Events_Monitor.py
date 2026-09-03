from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from atlas.critical_events_dashboard import configured_ota_versions
from atlas.streamlit_ui import API_BASE_URL, REQUEST_TIMEOUT, configure_app


REPO_ROOT = Path(__file__).resolve().parents[1]
DETAIL_TABLE_LIMIT = 10
ERROR_PRIORITIES = {
    "P0": "direct video/data loss",
    "P1": "major telemetry/safety signal loss",
    "P2": "moderate functional impact",
    "P3": "connectivity/auxiliary impact",
    "P4": "minor/no immediate loss",
}


class DashboardApiError(RuntimeError):
    pass


def _raise_dashboard_api_error(response: requests.Response) -> None:
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        raw_detail = payload.get("detail")
        if isinstance(raw_detail, str):
            detail = raw_detail.strip()
    if not detail:
        detail = response.text.strip() or response.reason or "Dashboard API request failed"
    raise DashboardApiError(f"Dashboard backend returned {response.status_code}: {detail}")


def _dashboard_api_get(path: str) -> dict[str, object]:
    response = requests.get(f"{API_BASE_URL}{path}", timeout=REQUEST_TIMEOUT)
    if not response.ok:
        _raise_dashboard_api_error(response)
    return response.json()


def _dashboard_api_post(path: str, payload: dict[str, object]) -> dict[str, object]:
    response = requests.post(f"{API_BASE_URL}{path}", json=payload, timeout=REQUEST_TIMEOUT)
    if not response.ok:
        _raise_dashboard_api_error(response)
    return response.json()


def _frame_from_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _filter_payload(
    ota_version: str,
    device_ids: tuple[str, ...],
    start_date: str | None,
    end_date_exclusive: str | None,
    limit: int | None = None,
) -> dict[str, object]:
    return {
        "ota_version": ota_version,
        "device_ids": list(device_ids),
        "start_ts": start_date,
        "end_ts": end_date_exclusive,
        "limit": limit,
    }


def _render_sidebar() -> None:
    with st.sidebar:
        st.page_link("streamlit_app.py", label="Dashboard")
        st.markdown("### Agents")
        st.page_link("pages/1_Atlas.py", label="Atlas", icon=":material/precision_manufacturing:")
        st.page_link("pages/2_Cypher.py", label="Cypher", icon=":material/account_tree:")
        st.page_link("pages/3_Critical_Events_Monitor.py", label="Critical Events Monitor", icon=":material/monitoring:")


@st.cache_data(show_spinner=True, ttl=300)
def _load_summary(ota_versions: tuple[str, ...]) -> pd.DataFrame:
    payload = _dashboard_api_post("/atlas/dashboard/critical-events/summary", {"ota_versions": list(ota_versions)})
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["DEVICE_VERSION", "type", "events"])
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    frame["DEVICE_VERSION"] = frame["DEVICE_VERSION"].fillna("UNKNOWN").astype(str)
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    return frame


@st.cache_data(show_spinner=True, ttl=300)
def _load_detail(
    ota_version: str,
    device_ids: tuple[str, ...],
    start_date: str | None,
    end_date_exclusive: str | None,
) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/detail",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive, DETAIL_TABLE_LIMIT),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return frame
    frame["TIMESTAMP"] = pd.to_datetime(frame["TIMESTAMP"], errors="coerce")
    return frame


@st.cache_data(show_spinner=True, ttl=300)
def _load_date_bounds(ota_version: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    payload = _dashboard_api_get(f"/atlas/dashboard/critical-events/{ota_version}/date-bounds")
    min_ts = pd.to_datetime(payload.get("min_timestamp"), errors="coerce")
    max_ts = pd.to_datetime(payload.get("max_timestamp"), errors="coerce")
    return (None if pd.isna(min_ts) else min_ts, None if pd.isna(max_ts) else max_ts)


@st.cache_data(show_spinner=True, ttl=300)
def _load_devices(ota_version: str, start_date: str | None, end_date_exclusive: str | None) -> list[str]:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/devices",
        _filter_payload(ota_version, tuple(), start_date, end_date_exclusive),
    )
    return [str(device_id) for device_id in payload.get("device_ids", [])]


@st.cache_data(show_spinner=True, ttl=300)
def _load_type_counts(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/type-counts",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["type", "events"])
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


@st.cache_data(show_spinner=True, ttl=300)
def _load_priority_counts(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/priority-counts",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["priority", "events"])
    frame["priority"] = frame["priority"].fillna("UNMAPPED").astype(str).str.upper()
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


@st.cache_data(show_spinner=True, ttl=300)
def _load_daily_counts(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/daily-counts",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["day", "type", "events"])
    frame["day"] = pd.to_datetime(frame["day"], errors="coerce")
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


@st.cache_data(show_spinner=True, ttl=300)
def _load_top_processes(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/top-processes",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["PROCESS_NAME", "events"])
    frame["PROCESS_NAME"] = frame["PROCESS_NAME"].fillna("UNKNOWN").astype(str)
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


@st.cache_data(show_spinner=True, ttl=300)
def _load_top_codes(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/top-codes",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["CODE", "events"])
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


@st.cache_data(show_spinner=True, ttl=300)
def _load_top_code_details(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/top-code-details",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["CODE", "description_pattern", "events"])
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


@st.cache_data(show_spinner=True, ttl=300)
def _load_top_devices(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/top-devices",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["DEVICE_ID", "events"])
    frame["DEVICE_ID"] = frame["DEVICE_ID"].fillna("").astype(str)
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


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


def _priority_label(priority: str) -> str:
    description = ERROR_PRIORITIES.get(priority)
    if not description:
        return priority
    return f"{priority} - {description}"


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
            priority_plot = priority_counts.copy()
            priority_plot["priority_label"] = priority_plot["priority"].map(_priority_label)
            st.plotly_chart(_pie(priority_plot, "priority_label", "events", "Error priority split"), use_container_width=True)
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

    try:
        ota_versions = configured_ota_versions(REPO_ROOT)
        summary = _load_summary(tuple(ota_versions))
        selected_ota = st.query_params.get("ota")

        if selected_ota:
            _render_ota_page(selected_ota)
        else:
            _render_home(summary, ota_versions)
    except DashboardApiError as exc:
        st.error(str(exc))
        st.stop()


if __name__ == "__main__":
    main()