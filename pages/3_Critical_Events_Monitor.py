from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit.components.v1 as components
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
PRIORITY_BREAKDOWN_LABEL_LIMIT = 24
CHART_CARD_STYLE_BLOCK = """
<style>
.chart-card {
    border: 1px solid rgba(49, 51, 63, 0.18);
    border-radius: 20px;
    padding: 0.95rem 1rem 0.7rem 1rem;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.78), rgba(248, 249, 252, 0.96));
    box-shadow: 0 12px 28px rgba(15, 23, 42, 0.07);
    margin-bottom: 0.9rem;
}
.chart-card-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: rgb(49, 51, 63);
    margin-bottom: 0.35rem;
}
.chart-card-caption {
    font-size: 0.84rem;
    color: rgba(49, 51, 63, 0.68);
    margin-bottom: 0.55rem;
}
div[data-testid="stPlotlyChart"] {
    background: transparent;
}
div[data-testid="stPlotlyChart"] > div {
    border-radius: 16px;
}
</style>
"""


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


def _dashboard_api_delete(path: str, payload: dict[str, object]) -> dict[str, object]:
    response = requests.delete(f"{API_BASE_URL}{path}", json=payload, timeout=REQUEST_TIMEOUT)
    if not response.ok:
        _raise_dashboard_api_error(response)
    return response.json()


def _apply_chart_theme(fig, title: str):
    fig.update_layout(
        title={"text": title, "x": 0.02, "xanchor": "left"},
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(255,255,255,0.92)",
        font={"color": "rgb(49, 51, 63)"},
        legend={
            "bgcolor": "rgba(255,255,255,0.72)",
            "font": {"color": "rgb(49, 51, 63)"},
            "title": {"font": {"color": "rgb(49, 51, 63)"}},
        },
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor="rgba(49, 51, 63, 0.08)",
        zeroline=False,
        linecolor="rgba(49, 51, 63, 0.12)",
        tickfont={"color": "rgb(49, 51, 63)"},
        title_font={"color": "rgb(49, 51, 63)"},
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(49, 51, 63, 0.08)",
        zeroline=False,
        linecolor="rgba(49, 51, 63, 0.12)",
        tickfont={"color": "rgb(49, 51, 63)"},
        title_font={"color": "rgb(49, 51, 63)"},
    )
    return fig


def _render_chart_card(fig, title: str, caption: str | None = None, key: str | None = None) -> None:
    st.markdown(CHART_CARD_STYLE_BLOCK, unsafe_allow_html=True)
    st.markdown("<div class='chart-card'>", unsafe_allow_html=True)
    st.markdown(f"<div class='chart-card-title'>{title}</div>", unsafe_allow_html=True)
    if caption:
        st.markdown(f"<div class='chart-card-caption'>{caption}</div>", unsafe_allow_html=True)
    st.plotly_chart(_apply_chart_theme(fig, title), use_container_width=True, key=key)
    st.markdown("</div>", unsafe_allow_html=True)


@st.cache_data(show_spinner=False, ttl=60)
def _load_allowed_ota_versions() -> dict[str, object]:
    return _dashboard_api_get("/atlas/dashboard/critical-events/allowed-ota-versions")


def _add_allowed_ota_version(ota_version: str) -> dict[str, object]:
    response = requests.post(
        f"{API_BASE_URL}/atlas/dashboard/critical-events/allowed-ota-versions",
        json={"ota_version": ota_version},
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        _raise_dashboard_api_error(response)
    _load_allowed_ota_versions.clear()
    _load_summary.clear()
    return response.json()


def _remove_allowed_ota_version(ota_version: str) -> dict[str, object]:
    result = _dashboard_api_delete(
        "/atlas/dashboard/critical-events/allowed-ota-versions",
        {"ota_version": ota_version},
    )
    _load_allowed_ota_versions.clear()
    _load_summary.clear()
    return result


@st.dialog("Confirm OTA removal")
def _confirm_remove_allowed_ota_dialog(ota_version: str) -> None:
    st.write(f"Retype `{ota_version}` to confirm removing it from monitoring.")
    confirmation_value = st.text_input(
        "Confirm OTA version",
        key=f"confirm_remove_ota_{ota_version}",
        placeholder=ota_version,
    )
    action_col, cancel_col = st.columns(2)
    with action_col:
        remove_clicked = st.button(
            "Remove OTA",
            key=f"confirm_remove_ota_button_{ota_version}",
            use_container_width=True,
            type="primary",
            disabled=confirmation_value.strip() != ota_version,
        )
    with cancel_col:
        cancel_clicked = st.button(
            "Cancel",
            key=f"cancel_remove_ota_button_{ota_version}",
            use_container_width=True,
        )

    if remove_clicked:
        try:
            result = _remove_allowed_ota_version(ota_version)
        except DashboardApiError as exc:
            st.error(str(exc))
            return
        st.session_state["allowed_ota_versions_feedback"] = (
            "success",
            f"Removed OTA version. Total configured: {len([str(value) for value in result.get('ota_versions', [])])}",
        )
        st.rerun()

    if cancel_clicked:
        st.rerun()


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


def _render_allowed_ota_versions_manager() -> None:
    st.markdown("### Allowed OTA versions")
    try:
        payload = _load_allowed_ota_versions()
    except DashboardApiError as exc:
        st.error(str(exc))
        return

    ota_versions = [str(value) for value in payload.get("ota_versions", [])]
    limit = int(payload.get("limit", 30))

    st.markdown(
        """
        <style>
        .ota-manager-card {
            border: 1px solid rgba(49, 51, 63, 0.18);
            border-radius: 18px;
            padding: 1rem 1.1rem;
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.72), rgba(248, 249, 252, 0.92));
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
            margin-bottom: 0.75rem;
            min-height: 100%;
        }
        .ota-manager-meta {
            font-size: 0.9rem;
            color: rgba(49, 51, 63, 0.72);
            margin-bottom: 0.85rem;
        }
        .ota-chip-wrap {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            align-items: flex-start;
        }
        .ota-chip {
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 2.5rem;
            padding: 0.55rem 2rem 0.45rem 0.95rem;
            border-radius: 999px;
            border: 1px solid rgba(49, 51, 63, 0.16);
            background: rgba(255, 255, 255, 0.94);
            color: rgb(49, 51, 63);
            font-size: 0.88rem;
            line-height: 1.2;
            box-shadow: 0 4px 10px rgba(15, 23, 42, 0.05);
            white-space: nowrap;
        }
        .ota-chip-label {
            display: inline-block;
        }
        .ota-chip-remove {
            position: absolute;
            top: 0.28rem;
            right: 0.42rem;
            width: 1rem;
            height: 1rem;
            border-radius: 999px;
            border: 1px solid rgba(49, 51, 63, 0.14);
            background: rgba(248, 249, 252, 0.98);
            color: rgb(49, 51, 63);
            font-size: 0.72rem;
            font-weight: 700;
            line-height: 0.9rem;
            text-align: center;
            cursor: pointer;
            box-shadow: 0 2px 6px rgba(15, 23, 42, 0.06);
        }
        .ota-chip-remove:hover {
            border-color: rgba(49, 51, 63, 0.28);
            background: rgba(255, 255, 255, 1);
        }
        .ota-empty {
            padding: 0.8rem 0.9rem;
            border-radius: 14px;
            border: 1px dashed rgba(49, 51, 63, 0.22);
            color: rgba(49, 51, 63, 0.72);
            background: rgba(255, 255, 255, 0.7);
            font-size: 0.92rem;
        }
        div[data-testid="stForm"] {
            border: 1px solid rgba(49, 51, 63, 0.18);
            border-radius: 18px;
            padding: 1rem 1rem 0.4rem 1rem;
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.72), rgba(248, 249, 252, 0.92));
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
            min-height: 100%;
        }
        div[data-testid="stForm"] [data-testid="stTextInputRootElement"],
        div[data-testid="stForm"] [data-baseweb="input"],
        div[data-testid="stForm"] [data-baseweb="base-input"] {
            background: rgba(255, 255, 255, 0.96);
            border: 1px solid rgba(49, 51, 63, 0.16);
            border-radius: 14px;
            box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        div[data-testid="stForm"] [data-baseweb="input"] > div,
        div[data-testid="stForm"] [data-baseweb="base-input"] > div {
            background: transparent !important;
            border-radius: 14px;
        }
        div[data-testid="stForm"] input {
            background: transparent !important;
            color: rgb(49, 51, 63) !important;
            caret-color: #000000 !important;
        }
        div[data-testid="stForm"] input::placeholder {
            color: rgba(49, 51, 63, 0.45) !important;
        }
        div[data-testid="stForm"] [data-baseweb="input"]:focus-within,
        div[data-testid="stForm"] [data-baseweb="base-input"]:focus-within,
        div[data-testid="stForm"] [data-testid="stTextInputRootElement"]:focus-within {
            border-color: rgba(49, 51, 63, 0.28);
            box-shadow: 0 0 0 1px rgba(49, 51, 63, 0.08), inset 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        div[data-testid="stForm"] button[kind="secondaryFormSubmit"] {
            background: rgba(255, 255, 255, 0.96);
            color: rgb(49, 51, 63);
            border: 1px solid rgba(49, 51, 63, 0.16);
            border-radius: 14px;
            box-shadow: 0 4px 10px rgba(15, 23, 42, 0.05);
        }
        div[data-testid="stForm"] button[kind="secondaryFormSubmit"]:hover {
            border-color: rgba(49, 51, 63, 0.28);
            background: rgba(248, 249, 252, 0.98);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    summary_col, input_col = st.columns(2)
    with summary_col:
        st.markdown("<div class='ota-manager-card'>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='ota-manager-meta'>Configured OTAs ({len(ota_versions)}/{limit})</div>",
            unsafe_allow_html=True,
        )
        feedback = st.session_state.pop("allowed_ota_versions_feedback", None)
        if isinstance(feedback, tuple) and len(feedback) == 2:
            level, message = feedback
            if level == "success":
                st.success(message)
            elif level == "error":
                st.error(message)
        if ota_versions:
            chip_markup = "".join(
                (
                    "<span class='ota-chip'>"
                    f"<span class='ota-chip-label'>{ota}</span>"
                    f"<a class='ota-chip-remove' href='?remove_ota={ota}' target='_self'>x</a>"
                    "</span>"
                )
                for ota in ota_versions
            )
            components.html(f"<div class='ota-chip-wrap'>{chip_markup}</div>", height=160, scrolling=True)
        else:
            st.markdown("<div class='ota-empty'>No OTA versions configured yet.</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        remove_ota = st.query_params.get("remove_ota")
        if remove_ota:
            _confirm_remove_allowed_ota_dialog(str(remove_ota))

    with input_col:
        with st.form("add_allowed_ota_version", clear_on_submit=True):
            st.caption("Add a new OTA version to be monitored. This will be reflected in the dashboard from 2AM IST the next day.")
            new_ota_version = st.text_input("Add OTA version", placeholder="9.6.14.rc.1")
            submitted = st.form_submit_button("Add OTA", use_container_width=True)
        if submitted:
            try:
                result = _add_allowed_ota_version(new_ota_version)
            except DashboardApiError as exc:
                st.error(str(exc))
            else:
                updated_versions = [str(value) for value in result.get("ota_versions", [])]
                st.success(f"Added OTA version. Total configured: {len(updated_versions)}")
                st.rerun()

    st.divider()


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
def _load_priority_code_breakdown(
    ota_version: str,
    device_ids: tuple[str, ...],
    start_date: str | None,
    end_date_exclusive: str | None,
) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/priority-code-breakdown",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["priority", "CODE", "normalized_description", "events"])
    frame["priority"] = frame["priority"].fillna("UNMAPPED").astype(str).str.upper()
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["normalized_description"] = frame["normalized_description"].fillna("UNMAPPED").astype(str)
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
        "priority_breakdown": lambda: _load_priority_code_breakdown(ota_version, device_ids, start_date, end_date_exclusive),
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


def _truncate_label(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(limit - 3, 0)].rstrip()}..."


def _priority_breakdown_bar(data: pd.DataFrame, priority: str):
    plot_data = data.copy()
    plot_data["full_label"] = plot_data.apply(
        lambda row: f"{int(row['CODE']) if pd.notna(row['CODE']) else 'NA'} | {row['normalized_description']}",
        axis=1,
    )
    plot_data["label"] = plot_data["full_label"].map(lambda value: _truncate_label(value, PRIORITY_BREAKDOWN_LABEL_LIMIT))
    fig = px.bar(
        plot_data,
        x="label",
        y="events",
        hover_data={"CODE": True, "normalized_description": True, "full_label": True, "label": False},
        title=f"{priority} breakdown",
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=50, b=10),
        xaxis_title="Code | Normalized description",
        yaxis_title="Count",
        xaxis={"type": "category", "categoryorder": "array", "categoryarray": plot_data["label"].tolist()},
    )
    fig.update_xaxes(tickangle=-35)
    fig.update_traces(hovertemplate="Code=%{customdata[0]}<br>Normalized description=%{customdata[1]}<br>Full label=%{customdata[2]}<br>Count=%{y}<extra></extra>")
    return fig


def _set_priority_breakdown_query_params(ota_version: str, start_date: str, end_date_exclusive: str, device_ids: tuple[str, ...]) -> None:
    st.query_params["ota"] = ota_version
    st.query_params["view"] = "priority-breakdown"
    st.query_params["start"] = start_date
    st.query_params["end"] = end_date_exclusive
    if device_ids:
        st.query_params["devices"] = list(device_ids)
    elif "devices" in st.query_params:
        del st.query_params["devices"]


def _clear_priority_breakdown_query_params() -> None:
    if "view" in st.query_params:
        del st.query_params["view"]
    if "start" in st.query_params:
        del st.query_params["start"]
    if "end" in st.query_params:
        del st.query_params["end"]
    if "devices" in st.query_params:
        del st.query_params["devices"]


def _render_home(summary: pd.DataFrame, ota_versions: list[str]) -> None:
    st.subheader("Production OTA overview")
    if summary.empty:
        st.info("No production critical-events data found for the OTA versions configured in .env.")
        return

    col1, col2 = st.columns([1.2, 1])
    with col1:
        _render_chart_card(
            _pie(summary, "DEVICE_VERSION", "events", "Event share by OTA"),
            "Event share by OTA",
            "Distribution of weighted events across configured OTA versions.",
            key="home_event_share_by_ota",
        )
    with col2:
        type_totals = summary.groupby("type", as_index=False)["events"].sum()
        _render_chart_card(
            _pie(type_totals, "type", "events", "Error vs Info split"),
            "Error vs Info split",
            "Overall production mix for the currently monitored OTA set.",
            key="home_error_info_split",
        )

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
            _render_chart_card(
                _pie(priority_plot, "priority_label", "events", "Error priority split"),
                "Error priority split",
                "Priority distribution for the selected OTA and filters.",
                key=f"priority_split_{ota_version}",
            )
            if st.button("View priority breakdown details", key=f"priority_breakdown_{ota_version}", use_container_width=True):
                _set_priority_breakdown_query_params(ota_version, start_date_str, end_date_exclusive_str, selected_device_ids)
                st.rerun()
    with top_row[1]:
        _render_chart_card(
            _pie(type_counts, "type", "events", "Errors vs Info"),
            "Errors vs Info",
            "Current selection split by event type.",
            key=f"type_split_{ota_version}",
        )

    trend_cols = st.columns(2)
    if not daily_counts.empty:
        with trend_cols[0]:
            _render_chart_card(
                _bar(daily_counts, "day", "events", "type", "Daily event trend"),
                "Daily event trend",
                "Daily volume trend split by event type.",
                key=f"daily_trend_{ota_version}",
            )

    with trend_cols[1]:
        _render_chart_card(
            _bar(process_counts, "PROCESS_NAME", "events", None, "Top noisy processes"),
            "Top noisy processes",
            "Processes contributing the highest event volume.",
            key=f"top_processes_{ota_version}",
        )

    bottom_cols = st.columns(2)
    with bottom_cols[0]:
        if code_counts.empty:
            st.info("No error codes found for this OTA selection.")
        else:
            _render_chart_card(
                _categorical_bar(code_counts, "CODE", "events", "Top error codes"),
                "Top error codes",
                "Highest-frequency error codes for the current selection.",
                key=f"top_codes_{ota_version}",
            )
    with bottom_cols[1]:
        _render_chart_card(
            _categorical_bar(device_counts, "DEVICE_ID", "events", "Most affected devices"),
            "Most affected devices",
            "Devices with the highest event counts in the current filter window.",
            key=f"top_devices_{ota_version}",
        )

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


def _render_priority_breakdown_page(ota_version: str) -> None:
    start_date = st.query_params.get("start")
    end_date_exclusive = st.query_params.get("end")
    device_params = st.query_params.get_all("devices") if hasattr(st.query_params, "get_all") else []
    selected_device_ids = tuple(device_params)

    st.subheader(f"Priority breakdown: {ota_version}")
    caption_parts = []
    if start_date and end_date_exclusive:
        end_inclusive = (pd.Timestamp(end_date_exclusive) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        caption_parts.append(f"Date range: {start_date} to {end_inclusive}")
    if selected_device_ids:
        caption_parts.append(f"Devices: {len(selected_device_ids)} selected")
    if caption_parts:
        st.caption(" | ".join(caption_parts))

    if st.button("Back to OTA detail", use_container_width=False):
        _clear_priority_breakdown_query_params()
        st.rerun()

    page_data = _load_ota_page_data(ota_version, selected_device_ids, start_date, end_date_exclusive)
    breakdown = page_data["priority_breakdown"]
    if breakdown.empty:
        st.info("No priority breakdown rows found for this OTA selection.")
        return

    priorities = [f"P{level}" for level in range(5)]
    chart_cols = st.columns(2)
    for index, priority in enumerate(priorities):
        priority_frame = breakdown[breakdown["priority"] == priority].copy()
        target = chart_cols[index % 2]
        with target:
            if priority_frame.empty:
                st.info(f"No rows found for {priority}.")
            else:
                _render_chart_card(
                    _priority_breakdown_bar(priority_frame, priority),
                    f"{priority} breakdown",
                    "Code-level breakdown for this priority bucket.",
                    key=f"priority_breakdown_chart_{priority}_{ota_version}",
                )

    unmapped = breakdown[~breakdown["priority"].isin(priorities)].copy()
    if not unmapped.empty:
        st.markdown("### Unmapped priorities")
        st.dataframe(
            unmapped[["priority", "CODE", "normalized_description", "events"]].reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
        )


def main() -> None:
    configure_app()
    _render_sidebar()
    st.title("Critical Events Monitor")
    st.caption("Production dashboard backed by ClickHouse summary and detail queries.")

    try:
        ota_versions = configured_ota_versions(REPO_ROOT)
        summary = _load_summary(tuple(ota_versions))
        selected_ota = st.query_params.get("ota")
        selected_view = st.query_params.get("view")

        if selected_ota and selected_view == "priority-breakdown":
            _render_priority_breakdown_page(selected_ota)
        elif selected_ota:
            _render_ota_page(selected_ota)
        else:
            _render_allowed_ota_versions_manager()
            _render_home(summary, ota_versions)
    except DashboardApiError as exc:
        st.error(str(exc))
        st.stop()


if __name__ == "__main__":
    main()