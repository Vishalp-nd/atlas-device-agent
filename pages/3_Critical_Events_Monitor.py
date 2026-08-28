from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from atlas.critical_events_dashboard import DashboardConfig, build_enriched_frame, configured_ota_versions, ota_detail, ota_summary
from atlas.streamlit_ui import configure_app


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = DashboardConfig(repo_root=REPO_ROOT)


def _render_sidebar() -> None:
    with st.sidebar:
        st.page_link("streamlit_app.py", label="Dashboard")
        st.markdown("### Agents")
        st.page_link("pages/1_Atlas.py", label="Atlas", icon=":material/precision_manufacturing:")
        st.page_link("pages/2_Cypher.py", label="Cypher", icon=":material/account_tree:")
        st.page_link("pages/3_Critical_Events_Monitor.py", label="Critical Events Monitor", icon=":material/monitoring:")


@st.cache_data(show_spinner=True, ttl=300)
def _load_data() -> pd.DataFrame:
    return build_enriched_frame(CONFIG)


def _pie(data: pd.DataFrame, names: str, values: str, title: str, hole: float = 0.45):
    fig = px.pie(data, names=names, values=values, hole=hole)
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(title=title, margin=dict(l=10, r=10, t=50, b=10), legend_title_text="")
    return fig


def _bar(data: pd.DataFrame, x: str, y: str, color: str | None, title: str):
    fig = px.bar(data, x=x, y=y, color=color, title=title)
    fig.update_layout(margin=dict(l=10, r=10, t=50, b=10), xaxis_title="", yaxis_title="Events")
    return fig


def _render_home(frame: pd.DataFrame, ota_versions: list[str]) -> None:
    st.subheader("Production OTA overview")
    summary = ota_summary(frame, ota_versions)
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


def _render_ota_page(frame: pd.DataFrame, ota_version: str) -> None:
    st.subheader(f"OTA detail: {ota_version}")
    ota_frame = ota_detail(frame, ota_version)
    if ota_frame.empty:
        st.info("No data found for the selected OTA.")
        return

    available_devices = sorted(device for device in ota_frame["DEVICE_ID"].dropna().unique().tolist() if device)
    selected_devices = st.multiselect("Filter by device ID", available_devices)
    filtered = ota_detail(frame, ota_version, selected_devices)
    if filtered.empty:
        st.warning("The selected device filter returned no rows.")
        return

    if st.button("Back to OTA overview"):
        st.query_params.clear()
        st.rerun()

    top_row = st.columns(2)
    error_frame = filtered[filtered["type"] == "ERROR"]
    priority_counts = (
        error_frame.groupby("priority", as_index=False)["COUNT"]
        .sum()
        .rename(columns={"COUNT": "events"})
        .sort_values("priority")
    )
    type_counts = (
        filtered.groupby("type", as_index=False)["COUNT"]
        .sum()
        .rename(columns={"COUNT": "events"})
    )
    with top_row[0]:
        if priority_counts.empty:
            st.info("No mapped error priorities found for this OTA.")
        else:
            st.plotly_chart(_pie(priority_counts, "priority", "events", "Error priority split"), use_container_width=True)
    with top_row[1]:
        st.plotly_chart(_pie(type_counts, "type", "events", "Errors vs Info"), use_container_width=True)

    trend_cols = st.columns(2)
    daily = filtered.dropna(subset=["TIMESTAMP"]).copy()
    if not daily.empty:
        daily["day"] = daily["TIMESTAMP"].dt.date
        daily_counts = (
            daily.groupby(["day", "type"], as_index=False)["COUNT"]
            .sum()
            .rename(columns={"COUNT": "events"})
        )
        with trend_cols[0]:
            st.plotly_chart(_bar(daily_counts, "day", "events", "type", "Daily event trend"), use_container_width=True)

    process_counts = (
        filtered.groupby("PROCESS_NAME", as_index=False)["COUNT"]
        .sum()
        .rename(columns={"COUNT": "events"})
        .sort_values("events", ascending=False)
        .head(10)
    )
    with trend_cols[1]:
        st.plotly_chart(_bar(process_counts, "PROCESS_NAME", "events", None, "Top noisy processes"), use_container_width=True)

    bottom_cols = st.columns(2)
    code_counts = (
        error_frame.groupby("CODE", as_index=False)["COUNT"]
        .sum()
        .rename(columns={"COUNT": "events"})
        .sort_values("events", ascending=False)
        .head(10)
    )
    device_counts = (
        filtered.groupby("DEVICE_ID", as_index=False)["COUNT"]
        .sum()
        .rename(columns={"COUNT": "events"})
        .sort_values("events", ascending=False)
        .head(10)
    )
    with bottom_cols[0]:
        if code_counts.empty:
            st.info("No error codes found for this OTA selection.")
        else:
            st.plotly_chart(_bar(code_counts, "CODE", "events", None, "Top error codes"), use_container_width=True)
    with bottom_cols[1]:
        st.plotly_chart(_bar(device_counts, "DEVICE_ID", "events", None, "Most affected devices"), use_container_width=True)

    st.markdown("### Filtered rows")
    st.dataframe(
        filtered[["TIMESTAMP", "DEVICE_ID", "PROCESS_NAME", "CODE", "DESCRIPTION", "type", "priority", "COUNT"]]
        .sort_values("TIMESTAMP", ascending=False)
        .head(250),
        use_container_width=True,
        hide_index=True,
    )


def main() -> None:
    configure_app()
    _render_sidebar()
    st.title("Critical Events Monitor")
    st.caption("Production dashboard backed by the local critical-events PostgreSQL table.")

    frame = _load_data()
    ota_versions = configured_ota_versions(REPO_ROOT)
    selected_ota = st.query_params.get("ota")

    if selected_ota:
        _render_ota_page(frame, selected_ota)
    else:
        _render_home(frame, ota_versions)


if __name__ == "__main__":
    main()