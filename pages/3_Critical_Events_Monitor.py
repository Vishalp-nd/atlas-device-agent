from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

from atlas.streamlit_ui import API_BASE_URL, REQUEST_TIMEOUT, _render_sidebar_nav, configure_app


REPO_ROOT = Path(__file__).resolve().parents[1]
ERROR_PRIORITIES = {
    "P0": "direct video/data loss",
    "P1": "major telemetry/safety signal loss",
    "P2": "moderate functional impact",
    "P3": "connectivity/auxiliary impact",
    "P4": "minor/no immediate loss",
}
# Keeps the landing-page hero (title -> OTA manager -> overview pies) inside the first
# viewport so both home pie charts are fully visible without scrolling. The global CSS
# reserves 4.75rem above the main container for Streamlit's fixed toolbar; 2.6rem still
# clears it while reclaiming vertical space, and the rest trims heading/caption gaps.
COMPACT_LAYOUT_STYLE_BLOCK = """
<style>
[data-testid="stMainBlockContainer"] {
    padding-top: 2.6rem !important;
    padding-bottom: 2rem !important;
}
/* Only the top-level stack is tightened; nested containers keep their own spacing. */
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] {
    gap: 0.6rem;
}
[data-testid="stMainBlockContainer"] h1 {
    margin-top: 0;
    padding-top: 0;
    margin-bottom: 0.15rem;
    line-height: 1.15;
}
[data-testid="stMainBlockContainer"] h2,
[data-testid="stMainBlockContainer"] h3 {
    margin-top: 0.35rem;
    padding-top: 0;
    margin-bottom: 0.2rem;
}
[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"],
[data-testid="stMainBlockContainer"] .stCaption {
    margin-bottom: 0.1rem;
}
/* The OTA manager row sits between the title and the pies, so keep it shallow. */
[data-testid="stVerticalBlockBorderWrapper"]:has(.ota-manager-meta),
[data-testid="stVerticalBlockBorderWrapper"]:has(form[data-testid="stForm"]) {
    padding: 0.7rem 0.9rem !important;
}
[data-testid="stForm"] {
    padding: 0.7rem 0.9rem 0.25rem !important;
}
.ota-manager-meta {
    margin-bottom: 0.5rem !important;
}
</style>
"""

# Covers the viewport while main() renders, so partially-built widgets are never visible.
LOADING_OVERLAY_HTML = """
<style>
.atlas-loading-overlay {
    position: fixed;
    inset: 0;
    z-index: 2147483000;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.9rem;
    background: var(--background-color, #ffffff);
}
.atlas-loading-spinner {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    border: 4px solid rgba(49, 51, 63, 0.12);
    border-top-color: #2f7de1;
    animation: atlas-loading-spin 0.85s linear infinite;
}
.atlas-loading-text {
    font-size: 0.9rem;
    color: var(--text-color, #5a6472);
    opacity: 0.75;
}
@keyframes atlas-loading-spin {
    to { transform: rotate(360deg); }
}
</style>
<div class="atlas-loading-overlay">
    <div class="atlas-loading-spinner"></div>
    <div class="atlas-loading-text">Loading dashboard…</div>
</div>
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
        # Default scope="app", so this is a full rerun that closes the dialog fragment.
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


def _render_allowed_ota_versions_manager() -> None:
    st.markdown("### OTA versions")
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
        /* The chip list sits directly on the page -- no card. st.container(border=False) below
           already drops the border; this also flattens the wrapper Streamlit emits either way,
           so no stray frame survives a Streamlit version bump. */
        [data-testid="stVerticalBlockBorderWrapper"]:has(.ota-manager-meta),
        [data-testid="stVerticalBlockBorderWrapper"]:has(.ota-manager-meta) > div {
            border: none !important;
            padding: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
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
            max-height: 15rem;
            overflow-y: auto;
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
        /* The cross is an <a>, so Streamlit's default link styling (blue + underline) has to be
           overridden explicitly or the chip shows a blue underlined "x". */
        .ota-chip-remove,
        .ota-chip-remove:link,
        .ota-chip-remove:visited {
            position: absolute;
            top: 0.28rem;
            right: 0.42rem;
            width: 1rem;
            height: 1rem;
            border-radius: 999px;
            border: 1px solid rgba(49, 51, 63, 0.14);
            background: rgba(248, 249, 252, 0.98);
            color: rgb(49, 51, 63) !important;
            font-size: 0.72rem;
            font-weight: 700;
            line-height: 0.9rem;
            text-align: center;
            cursor: pointer;
            box-shadow: 0 2px 6px rgba(15, 23, 42, 0.06);
            text-decoration: none !important;
        }
        .ota-chip-remove:hover {
            border-color: rgba(49, 51, 63, 0.28);
            background: rgba(255, 255, 255, 1);
            color: rgb(17, 17, 17) !important;
            text-decoration: none !important;
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
        # border=False plus the CSS override above: the chip list sits straight on the page.
        # Keeping the container (rather than dropping it) preserves the grouping the chip CSS
        # and the remove-dialog guard below both rely on.
        with st.container(border=False):
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
                        f"<span class='ota-chip-label'>{html.escape(ota)}</span>"
                        f"<a class='ota-chip-remove' href='?remove_ota={quote(ota, safe='')}' "
                        "target='_self' title='Remove'>&times;</a>"
                        "</span>"
                    )
                    for ota in ota_versions
                )
                # Rendered via st.markdown (not components.html) so the chips live in the parent
                # document and pick up the .ota-chip CSS above -- an iframe would not inherit it.
                st.markdown(
                    f"<div class='ota-chip-wrap'>{chip_markup}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown("<div class='ota-empty'>No OTA versions configured yet.</div>", unsafe_allow_html=True)

        # The chip's "x" navigates to ?remove_ota=<ota>. Consume the param in the same run that
        # opens the dialog: left in the URL it would survive every later rerun (Add OTA, Cancel,
        # dismissing via the corner X or an outside click) and keep reopening the dialog.
        #
        # Deleting it here is safe and does not cut the dialog short:
        #   - st.query_params mutation only pushes a URL update, it does not trigger a rerun;
        #   - st.dialog is fragment-backed, so typing/clicking inside the dialog reruns only the
        #     dialog body, never this guard -- the dialog stays open on its own.
        # Every way of closing it ends in a full rerun where the param is already gone, so Cancel,
        # the corner X and an outside click all behave the same: closed, and it stays closed.
        remove_ota = st.query_params.get("remove_ota")
        if remove_ota:
            del st.query_params["remove_ota"]
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


@st.cache_data(show_spinner=False, ttl=300)
def _load_summary(ota_versions: tuple[str, ...]) -> pd.DataFrame:
    payload = _dashboard_api_post("/atlas/dashboard/critical-events/summary", {"ota_versions": list(ota_versions)})
    frame = _frame_from_rows(payload.get("rows", []))
    if frame.empty:
        return pd.DataFrame(columns=["DEVICE_VERSION", "type", "events"])
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    frame["DEVICE_VERSION"] = frame["DEVICE_VERSION"].fillna("UNKNOWN").astype(str)
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    return frame


@st.cache_data(show_spinner=False, ttl=300)
def _load_date_bounds(ota_version: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    payload = _dashboard_api_get(f"/atlas/dashboard/critical-events/{ota_version}/date-bounds")
    min_ts = pd.to_datetime(payload.get("min_timestamp"), errors="coerce")
    max_ts = pd.to_datetime(payload.get("max_timestamp"), errors="coerce")
    return (None if pd.isna(min_ts) else min_ts, None if pd.isna(max_ts) else max_ts)


@st.cache_data(show_spinner=False, ttl=300)
def _load_devices(ota_version: str, start_date: str | None, end_date_exclusive: str | None) -> list[str]:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/devices",
        _filter_payload(ota_version, tuple(), start_date, end_date_exclusive),
    )
    return [str(device_id) for device_id in payload.get("device_ids", [])]


@st.cache_data(show_spinner=False, ttl=300)
def _load_process_code_table(ota_version: str, device_ids: tuple[str, ...], start_date: str | None, end_date_exclusive: str | None) -> pd.DataFrame:
    payload = _dashboard_api_post(
        f"/atlas/dashboard/critical-events/{ota_version}/process-code-table",
        _filter_payload(ota_version, device_ids, start_date, end_date_exclusive),
    )
    frame = _frame_from_rows(payload.get("rows", []))
    columns = ["PROCESS_NAME", "CODE", "CODE_AUX", "type", "priority", "description_pattern", "sample_description", "occurrences", "devices"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["PROCESS_NAME"] = frame["PROCESS_NAME"].fillna("UNKNOWN").astype(str)
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce").astype("Int64")
    frame["CODE_AUX"] = pd.to_numeric(frame["CODE_AUX"], errors="coerce").astype("Int64")
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    frame["priority"] = frame["priority"].astype(str).str.strip().str.upper()
    frame.loc[frame["priority"].isin(["", "NAN", "NONE"]), "priority"] = pd.NA
    frame["description_pattern"] = frame["description_pattern"].fillna("UNMAPPED").astype(str)
    frame["sample_description"] = frame["sample_description"].fillna("").astype(str)
    frame["occurrences"] = pd.to_numeric(frame["occurrences"], errors="coerce").fillna(0).astype("Int64")
    frame["devices"] = frame["devices"].map(lambda value: list(value) if isinstance(value, (list, tuple)) else [])
    return frame[columns]


OTA_TILE_STYLE_BLOCK = """
<style>
/* Tiles borrow the app shell's tokens (--accent/--border/--muted from streamlit_ui._inject_css)
   so the green card language of .hero / .agent-card carries over instead of a second palette. */
.ota-group {
    border: 2px solid rgba(126, 232, 170, 0.95);
    border-radius: 22px;
    padding: 1.15rem 1.25rem 1.3rem 1.25rem;
    margin-bottom: 1.1rem;
    background: var(--panel, #ffffff);
    box-shadow: 0 0 0 1px rgba(214, 255, 229, 0.9), 0 16px 34px rgba(0, 166, 81, 0.12);
}
.ota-group-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.55rem;
    padding-bottom: 0.7rem;
    margin-bottom: 0.95rem;
    border-bottom: 1px solid rgba(0, 166, 81, 0.16);
}
.ota-group-name {
    font-size: 1.05rem;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text, #111111);
}
.ota-group-meta {
    margin-left: auto;
    font-size: 0.84rem;
    color: var(--muted, #2f5a3f);
}
.ota-tile-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 0.85rem;
}
/* The whole tile is the click target. Streamlit styles bare <a> blue+underlined, so every
   link state has to be overridden explicitly or the tile renders as a blue hyperlink block. */
.ota-tile,
.ota-tile:link,
.ota-tile:visited,
.ota-tile:hover,
.ota-tile:active {
    text-decoration: none;
    color: var(--text, #111111);
}
.ota-tile {
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
    padding: 0.9rem 1rem 1rem 1rem;
    border: 1.5px solid rgba(0, 166, 81, 0.22);
    border-radius: 16px;
    background: linear-gradient(160deg, #ffffff 0%, #f7fffb 100%);
    box-shadow: 0 0 0 1px rgba(214, 255, 229, 0.7), 0 8px 18px rgba(0, 166, 81, 0.10);
    transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
}
.ota-tile:hover {
    transform: translateY(-2px);
    border-color: rgba(0, 166, 81, 0.62);
    box-shadow: 0 0 0 1px rgba(46, 207, 122, 0.18), 0 14px 28px rgba(0, 166, 81, 0.20);
}
.ota-tile-label {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted, #2f5a3f);
}
.ota-tile-version {
    /* Mono keeps the dotted version segments legible and picks up the app's second font. */
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.08rem;
    font-weight: 600;
    line-height: 1.35;
    letter-spacing: -0.01em;
    color: var(--text, #111111);
    overflow-wrap: break-word;
}
.ota-tile-split {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-top: 0.15rem;
}
.ota-tile-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.18rem 0.6rem;
    border-radius: 999px;
    font-size: 0.76rem;
    font-weight: 600;
    line-height: 1.4;
}
/* Dot + word, so the two states never rely on color alone. */
.ota-tile-pill::before {
    content: "";
    width: 0.42rem;
    height: 0.42rem;
    border-radius: 50%;
    background: currentColor;
}
.ota-tile-pill-error {
    background: rgba(194, 12, 45, 0.08);
    border: 1px solid rgba(194, 12, 45, 0.24);
    color: #a3132f;
}
.ota-tile-pill-info {
    background: rgba(0, 166, 81, 0.10);
    border: 1px solid rgba(0, 166, 81, 0.28);
    color: #0b6b3a;
}
</style>
"""

PRODUCT_LINE_PREFIXES = {
    "krait": "2.",
    "krait2": "4.",
    "bagheera2": "3.",
    "bagheera3": "5.",
    "octo": "7.",
}
UNGROUPED_PRODUCT_LINE = "other"
_VERSION_TRIPLE_RE = re.compile(r"\d+\.\d+\.\d+")


def _allowed_ota_order() -> list[str]:
    """ALLOWED_OTA_VERSIONS in .env order -- the same list the chips at the top of the page show.

    Served from the cached allowed-versions call the OTA manager already makes on this render,
    so this costs no extra request. An unreachable backend degrades to "no preferred order"
    rather than failing the page.
    """
    try:
        payload = _load_allowed_ota_versions()
    except DashboardApiError:
        return []
    return [str(value) for value in payload.get("ota_versions", [])]


def _product_line_for_version(version: str) -> str:
    """Product line for an OTA version, by the major number of its first recognisable version.

    Plain versions lead with the product major ("4.6.16.rc.5" -> krait2). Special packages carry
    their own build number in front and the real OTA inside the name
    ("63.1.1.sp.4.6.15.rc.3_awl18.1_..."), so every N.N.N run is scanned in order and the first
    one whose major maps to a product line wins -- "63.1.1" is skipped, "4.6.15" matches krait2.
    """
    by_prefix = {prefix: line for line, prefix in PRODUCT_LINE_PREFIXES.items()}
    for match in _VERSION_TRIPLE_RE.finditer(version):
        prefix = f"{match.group(0).split('.')[0]}."
        if prefix in by_prefix:
            return by_prefix[prefix]
    return UNGROUPED_PRODUCT_LINE


def _version_sort_key(version: str) -> list[tuple[int, int, str]]:
    """Natural version ordering: digit runs compare as numbers, the rest as text.

    Plain string sort puts "4.6.9" after "4.6.16"; splitting on digit runs keeps 16 > 9. The
    uniform (kind, number, text) tuple keeps int and str parts comparable at the same position.
    Same idea as version_key() in scripts/update_allowed_ota_versions.py.
    """
    return [
        (1, int(part), "") if part.isdigit() else (0, 0, part)
        for part in re.split(r"(\d+)", version)
        if part
    ]


def _render_home(summary: pd.DataFrame) -> None:
    if summary.empty:
        st.info("No production critical-events data found.")
        return

    st.markdown("### OTA tiles")
    totals = summary.groupby("DEVICE_VERSION", as_index=False)["events"].sum()
    # Configured versions first, in ALLOWED_OTA_VERSIONS order, then everything else latest-first.
    # Ordering the whole frame here (not per group) is enough: the group loop below appends in
    # this order, so each product line inherits it.
    allowed_rank = {ota: index for index, ota in enumerate(_allowed_ota_order())}
    versions = totals["DEVICE_VERSION"].tolist()
    configured = sorted((v for v in versions if v in allowed_rank), key=lambda v: allowed_rank[v])
    remaining = sorted((v for v in versions if v not in allowed_rank), key=_version_sort_key, reverse=True)
    totals = totals.set_index("DEVICE_VERSION").loc[configured + remaining].reset_index()
    type_lookup = summary.pivot_table(index="DEVICE_VERSION", columns="type", values="events", aggfunc="sum", fill_value=0)

    st.markdown(OTA_TILE_STYLE_BLOCK, unsafe_allow_html=True)
    grouped_tiles: dict[str, list[str]] = {}
    group_events: dict[str, int] = {}
    for row in totals.itertuples(index=False):
        ota = row.DEVICE_VERSION
        error_count = int(type_lookup.loc[ota].get("ERROR", 0)) if ota in type_lookup.index else 0
        info_count = int(type_lookup.loc[ota].get("INFO", 0)) if ota in type_lookup.index else 0
        safe_ota = html.escape(ota)
        # Version strings are one long unbreakable token; without explicit break opportunities
        # the tile wraps mid-segment ("...global.r" / "c.1", or a lone trailing digit off a build
        # hash). <wbr> after each dot and underscore keeps every wrap on a segment boundary.
        wrapped_ota = safe_ota.replace(".", ".<wbr>").replace("_", "_<wbr>")
        # href carries the same "?ota=<version>" the old Open button set by hand, so the tile
        # lands on the OTA detail view and drops any stale view/start/end params with it.
        tile = (
            f"<a class='ota-tile' href='?ota={quote(ota, safe='')}' target='_self' "
            f"title='Total weighted events for {safe_ota}'>"
            f"<span class='ota-tile-label'>{int(row.events):,} events</span>"
            f"<span class='ota-tile-version'>{wrapped_ota}</span>"
            "<span class='ota-tile-split'>"
            f"<span class='ota-tile-pill ota-tile-pill-error'>Error {error_count:,}</span>"
            f"<span class='ota-tile-pill ota-tile-pill-info'>Info {info_count:,}</span>"
            "</span>"
            "</a>"
        )
        product_line = _product_line_for_version(ota)
        grouped_tiles.setdefault(product_line, []).append(tile)
        group_events[product_line] = group_events.get(product_line, 0) + int(row.events)

    # Known product lines in the order declared above, then anything that matched no prefix.
    ordered_lines = [line for line in PRODUCT_LINE_PREFIXES if line in grouped_tiles]
    ordered_lines += [line for line in grouped_tiles if line not in PRODUCT_LINE_PREFIXES]
    for product_line in ordered_lines:
        tiles = grouped_tiles[product_line]
        meta = f"{len(tiles)} version{'' if len(tiles) == 1 else 's'} · {group_events[product_line]:,} events"
        st.markdown(
            "<div class='ota-group'>"
            "<div class='ota-group-head'>"
            # Uppercased in CSS, not here, so the group key stays the lookup value it came from.
            f"<span class='ota-group-name'>{html.escape(product_line)}</span>"
            f"<span class='ota-group-meta'>{html.escape(meta)}</span>"
            "</div>"
            f"<div class='ota-tile-grid'>{''.join(tiles)}</div>"
            "</div>",
            unsafe_allow_html=True,
        )


PROCESS_CODE_TABLE_STYLE_BLOCK = """
<style>
.process-code-table-wrap {
    max-height: 440px;
    overflow-y: auto;
    overflow-x: auto;
    border: 1px solid rgba(120, 120, 120, 0.25);
    border-radius: 8px;
    margin-bottom: 0.4rem;
}
.process-code-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
    white-space: nowrap;
}
.process-code-table thead th {
    position: sticky;
    top: 0;
    background: var(--background-color, #ffffff);
    text-align: left;
    padding: 0.5rem 0.6rem;
    border-bottom: 2px solid rgba(120, 120, 120, 0.35);
    z-index: 1;
}
.process-code-table tbody td {
    padding: 0.45rem 0.6rem;
    border-bottom: 1px solid rgba(120, 120, 120, 0.15);
    vertical-align: top;
}
.process-code-table td.wrap-cell {
    white-space: normal;
    min-width: 200px;
}
.process-code-devices {
    max-height: 70px;
    min-width: 140px;
    overflow-y: auto;
    white-space: normal;
    word-break: break-word;
    padding-right: 0.3rem;
}
.process-code-devices div {
    padding: 0.05rem 0;
}
.copy-devices-btn {
    font-size: 0.7rem;
    padding: 0.1rem 0.45rem;
    margin-bottom: 0.3rem;
    border: 1px solid rgba(120, 120, 120, 0.4);
    border-radius: 4px;
    background: transparent;
    color: inherit;
    cursor: pointer;
}
.copy-devices-btn:hover {
    background: rgba(120, 120, 120, 0.15);
}
</style>
"""


def _render_process_code_table(frame: pd.DataFrame) -> None:
    headers = [
        "Process", "Code", "Code Aux", "Severity", "Priority",
        "Description Pattern", "Sample Description", "Occurrences", "Device Count", "Devices",
    ]
    header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    row_chunks = []
    for row in frame.itertuples(index=False):
        code = "" if pd.isna(row.CODE) else str(int(row.CODE))
        code_aux = "" if pd.isna(row.CODE_AUX) else str(int(row.CODE_AUX))
        occurrences = 0 if pd.isna(row.occurrences) else int(row.occurrences)
        devices = row.devices or []
        device_count = len(devices)
        devices_inner = "".join(f"<div>{html.escape(str(device))}</div>" for device in devices)
        devices_text = "\n".join(str(device) for device in devices)
        copy_button = (
            "<button type='button' class='copy-devices-btn' title='Copy all device IDs' "
            f'data-devices="{html.escape(devices_text)}" '
            "onclick=\"navigator.clipboard.writeText(this.dataset.devices)\">Copy</button>"
        )
        row_chunks.append(
            "<tr>"
            f"<td>{html.escape(str(row.PROCESS_NAME))}</td>"
            f"<td>{html.escape(code)}</td>"
            f"<td>{html.escape(code_aux)}</td>"
            f"<td>{html.escape(str(row.type))}</td>"
            f"<td>{html.escape(str(row.priority))}</td>"
            f"<td class='wrap-cell'>{html.escape(str(row.description_pattern))}</td>"
            f"<td class='wrap-cell'>{html.escape(str(row.sample_description))}</td>"
            f"<td>{occurrences}</td>"
            f"<td>{device_count}</td>"
            f"<td>{copy_button}<div class='process-code-devices'>{devices_inner}</div></td>"
            "</tr>"
        )
    table_html = (
        "<div class='process-code-table-wrap'>"
        "<table class='process-code-table'>"
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(row_chunks)}</tbody>"
        "</table>"
        "</div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def _priority_family(priority: str) -> int:
    # ERROR-severity priorities are P0-P4; INFO-severity priorities are P100-P104.
    # Grouping by family (rather than one flat sequence) is what lets the caller
    # reset the 2-per-row pairing at the family boundary, so P100 always starts a
    # new row instead of pairing with P4.
    match = re.fullmatch(r"P(\d+)", priority)
    if not match:
        return 2
    return 0 if int(match.group(1)) < 100 else 1


def _priority_sort_key(priority: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"P(\d+)", priority)
    if match:
        return (_priority_family(priority), int(match.group(1)), priority)
    return (_priority_family(priority), 0, priority)


def _render_ota_page(ota_version: str) -> None:
    st.subheader(f"OTA detail: {ota_version}")
    min_ts, max_ts = _load_date_bounds(ota_version)
    if min_ts is None or max_ts is None or pd.isna(min_ts) or pd.isna(max_ts):
        st.info("No data found for the selected OTA.")
        return

    st.caption(
        f"Available data range: {min_ts.strftime('%Y-%m-%d %H:%M:%S')} to {max_ts.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    if st.button("Back to OTA overview"):
        st.query_params.clear()
        st.rerun()

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

    table = _load_process_code_table(ota_version, selected_device_ids, start_date_str, end_date_exclusive_str)
    if table.empty:
        st.info("No data found for the selected OTA and date range.")
        return

    severity_options = sorted(table["type"].dropna().unique().tolist())
    code_options = sorted(int(code) for code in table["CODE"].dropna().unique().tolist())

    filter_row2 = st.columns([1, 1, 2])
    with filter_row2[0]:
        selected_severities = st.multiselect(
            "Severity",
            severity_options,
            placeholder="All severities",
            key=f"severity_filter_{ota_version}",
        )
    with filter_row2[1]:
        selected_codes = st.multiselect(
            "Code",
            code_options,
            placeholder="All codes",
            key=f"code_filter_{ota_version}",
        )
    with filter_row2[2]:
        description_query = st.text_input(
            "Description pattern contains",
            placeholder="Search description pattern",
            key=f"description_filter_{ota_version}",
        )

    filtered = table
    if selected_severities:
        filtered = filtered[filtered["type"].isin(selected_severities)]
    if selected_codes:
        filtered = filtered[filtered["CODE"].isin(selected_codes)]
    if description_query.strip():
        filtered = filtered[
            filtered["description_pattern"].str.contains(description_query.strip(), case=False, na=False, regex=False)
        ]

    priority_groups = sorted(table["priority"].dropna().unique().tolist(), key=_priority_sort_key)

    # Split into families (P0-P4, P100-P104, ...) so the 2-per-row pairing below resets at
    # each family boundary instead of running as one flat sequence -- otherwise the last
    # P0-P4 table (when that family has an odd count) would end up paired with P100.
    families: list[list[str]] = []
    for priority in priority_groups:
        family_id = _priority_family(priority)
        if not families or _priority_family(families[-1][-1]) != family_id:
            families.append([])
        families[-1].append(priority)

    st.markdown("### Process / code details by priority")
    st.markdown(PROCESS_CODE_TABLE_STYLE_BLOCK, unsafe_allow_html=True)

    for family in families:
        for pair_start in range(0, len(family), 2):
            pair_cols = st.columns(2)
            for index, priority in enumerate(family[pair_start:pair_start + 2]):
                group_total = table[table["priority"] == priority]
                group_filtered = (
                    filtered[filtered["priority"] == priority]
                    .sort_values("occurrences", ascending=False)
                    .reset_index(drop=True)
                )
                priority_label = f"{priority} - {ERROR_PRIORITIES[priority]}" if priority in ERROR_PRIORITIES else priority
                with pair_cols[index]:
                    st.markdown(f"**{priority_label}**")
                    if group_filtered.empty:
                        st.info("No rows match the selected filters.")
                    else:
                        _render_process_code_table(group_filtered)
                    st.caption(f"Showing {len(group_filtered)} of {len(group_total)} entries.")


def main() -> None:
    configure_app()
    st.markdown(COMPACT_LAYOUT_STYLE_BLOCK, unsafe_allow_html=True)
    loading_overlay = st.empty()
    loading_overlay.markdown(LOADING_OVERLAY_HTML, unsafe_allow_html=True)

    try:
        _render_sidebar_nav()
        st.title("Critical Events Monitor")
        st.caption("Production dashboard backed by ClickHouse summary and detail queries.")

        # Empty tuple => load_ota_summary() runs without a DEVICE_VERSION filter, so the home
        # view covers every OTA present in the data. Filtering by CINFO_REPORT here used to cut
        # the tiles down to that subset (2 versions) while the data held many more.
        summary = _load_summary(())
        selected_ota = st.query_params.get("ota")

        if selected_ota:
            _render_ota_page(selected_ota)
        else:
            _render_allowed_ota_versions_manager()
            _render_home(summary)
    except DashboardApiError as exc:
        st.error(str(exc))
        st.stop()
    finally:
        loading_overlay.empty()


if __name__ == "__main__":
    main()