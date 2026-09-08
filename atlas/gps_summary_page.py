"""GPS Summary page — pick a date range and product line, get a zip of both reports.

A pure HTTP client for /atlas/dashboard/gps/*, exactly like
pages/3_Critical_Events_Monitor.py. ClickHouse, the OUTPUT/ device-list tree and
the report generator itself all live in the backend
(atlas/gps_summary_service.py), so the Streamlit host needs neither
clickhouse_connect nor a copy of the data -- it only needs to reach the API.

The date picker is bounded to the range ClickHouse actually holds observation
data for, reports for a range the backend has already generated are reused
rather than re-queried, and the resulting zip auto-downloads once it is ready
(with a visible button as fallback, since browsers may block a scripted
download).
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

from atlas.streamlit_ui import API_BASE_URL, REQUEST_TIMEOUT

DOWNLOAD_LABEL = "Download GPS reports (.zip)"
AVAILABILITY_TTL = 300
# Report generation is a multi-day ClickHouse scan; the shared REQUEST_TIMEOUT
# (15 min) is the budget for it, while the metadata calls should fail fast.
METADATA_TIMEOUT = 30


class GpsApiError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def _raise_gps_api_error(response: requests.Response) -> None:
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
        detail = response.text.strip() or response.reason or "GPS API request failed"
    raise GpsApiError(f"GPS backend returned {response.status_code}: {detail}")


def _gps_api_get(path: str, params: dict | None = None, timeout: int = METADATA_TIMEOUT) -> dict:
    response = requests.get(f"{API_BASE_URL}{path}", params=params, timeout=timeout)
    if not response.ok:
        _raise_gps_api_error(response)
    return response.json()


def _gps_api_post(path: str, payload: dict, timeout: int = REQUEST_TIMEOUT) -> dict:
    response = requests.post(f"{API_BASE_URL}{path}", json=payload, timeout=timeout)
    if not response.ok:
        _raise_gps_api_error(response)
    return response.json()


@st.cache_data(show_spinner=True, ttl=AVAILABILITY_TTL)
def load_available_days() -> pd.DataFrame:
    """One row per calendar day that has observation data, with its file count."""
    payload = _gps_api_get("/atlas/dashboard/gps/availability")
    rows = payload.get("rows") or []
    if not rows:
        return pd.DataFrame(columns=["day", "files"])
    frame = pd.DataFrame(rows)
    frame["day"] = pd.to_datetime(frame["day"]).dt.date
    frame["files"] = pd.to_numeric(frame["files"], errors="coerce").fillna(0).astype(int)
    return frame.sort_values("day").reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=AVAILABILITY_TTL)
def load_product_lines() -> list[str]:
    payload = _gps_api_get("/atlas/dashboard/gps/product-lines")
    return list(payload.get("product_lines") or [])


def load_report_status(product_line: str, start: date, end_inclusive: date) -> dict:
    return _gps_api_get(
        "/atlas/dashboard/gps/reports/status",
        params={
            "product_line": product_line,
            "start": start.isoformat(),
            "end": end_inclusive.isoformat(),
        },
    )


def request_reports(product_line: str, start: date, end_inclusive: date, force: bool) -> dict:
    return _gps_api_post(
        "/atlas/dashboard/gps/reports",
        {
            "product_line": product_line,
            "start": start.isoformat(),
            "end": end_inclusive.isoformat(),
            "force": force,
        },
    )


def _download_url(url: str) -> str:
    return f"{API_BASE_URL.rstrip('/')}{url}" if url.startswith("/") else url


def fetch_zip_bytes(url: str) -> bytes:
    """Pull the archive from the backend. Called only when the button is clicked.

    Streamlit's deferred data generation means this does not run on every rerun,
    so the archive is not held in the frontend for the life of the connection.
    """
    response = requests.get(_download_url(url), timeout=REQUEST_TIMEOUT)
    if not response.ok:
        _raise_gps_api_error(response)
    return response.content


# ---------------------------------------------------------------------------
# Auto-download
# ---------------------------------------------------------------------------

def _auto_download(token: str) -> None:
    """Click the download button once, as soon as it exists.

    Clicking Streamlit's own button reuses its blob URL, so the archive is never
    inlined as a base64 data URI. st.html is used rather than
    components.v1.html (deprecated) because it is *not* iframed -- the script
    runs in the app document, so the button is reachable without a window.parent
    hop. Keyed on a token so an ordinary rerun, or the user returning to the
    page, does not re-trigger a download; only a fresh report request does.
    Polling covers the button not having painted yet, which is also what lets
    this fire after the user has switched to another browser tab.
    """
    payload = json.dumps({"token": token, "label": DOWNLOAD_LABEL})
    st.html(
        f"""
        <script>
        (function () {{
            const cfg = {payload};
            window.__gpsAutoDownloaded = window.__gpsAutoDownloaded || {{}};
            if (window.__gpsAutoDownloaded[cfg.token]) return;

            let attempts = 0;
            const findButton = () => Array.from(
                document.querySelectorAll('[data-testid="stDownloadButton"] button')
            ).find((btn) => (btn.innerText || '').indexOf(cfg.label) !== -1);

            const tick = () => {{
                if (window.__gpsAutoDownloaded[cfg.token]) return;
                const btn = findButton();
                if (btn) {{
                    window.__gpsAutoDownloaded[cfg.token] = true;
                    btn.click();
                    return;
                }}
                if (attempts++ < 60) setTimeout(tick, 250);
            }};
            tick();
        }})();
        </script>
        """,
        unsafe_allow_javascript=True,
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _render_availability(days: pd.DataFrame, start: date, end_inclusive: date) -> None:
    """Report coverage over the inclusive [start, end_inclusive] UTC window."""
    selected = {d.date() for d in pd.date_range(start, end_inclusive)}
    available = set(days["day"])
    missing = sorted(selected - available)
    covered = sorted(selected & available)

    files = int(days[days["day"].isin(covered)]["files"].sum()) if covered else 0
    st.caption(
        f"Covers **{start} 00:00:00** through **{end_inclusive} 23:59:59 UTC** "
        f"— {len(selected)} full day(s). "
        f"{len(covered)} have data ({files:,} observation files)."
    )
    if missing:
        shown = ", ".join(d.isoformat() for d in missing[:8])
        more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
        st.warning(f"No observation data on {len(missing)} day(s): {shown}{more}")


def _render_unreachable(exc: Exception) -> None:
    st.error(f"Could not load GPS data availability: {exc}")
    st.info(
        "This page reads everything over the Atlas API — it does not talk to "
        "ClickHouse itself. Check that the backend is running and reachable:\n\n"
        f"- API base URL: `{API_BASE_URL}`\n"
        "- Start it with `python -m atlas serve --host 0.0.0.0 --port 8501`\n"
        "- Point this page elsewhere with the `ATLAS_API_URL` environment variable\n\n"
        "If the API is up but this still fails, check the CLICKHOUSE_DB section "
        "of `db_credentials.ini` **on the backend host** and that "
        "`observation_data` has been populated.",
        icon=":material/cloud_off:",
    )


def render_gps_summary_page() -> None:
    st.title("GPS Summary")
    st.caption(
        "Generate the GPS Observation report (all three ignition conditions, side "
        "by side) and the GPS Summary report for a UTC date range, bundled as a zip."
    )

    try:
        days = load_available_days()
        product_lines = load_product_lines()
    except (GpsApiError, requests.RequestException) as exc:
        _render_unreachable(exc)
        return

    if days.empty:
        st.warning("observation_data is empty — there is nothing to report on yet.")
        return
    if not product_lines:
        st.warning(
            "No product lines with polled device data on the backend. Run "
            "`pipeline/data_polling.py` there first."
        )
        return

    min_date, max_date = min(days["day"]), max(days["day"])
    st.caption(
        f"Observation data available from **{min_date}** to **{max_date}** (UTC). "
        "Both dates below are **inclusive**, matching the script's `--start`/`--end`."
    )
    st.info(
        "All dates and times are **UTC** — `observation_data.start_time` and "
        "`video_metadata.timestamp` are stored in UTC, and the reports are "
        "generated in UTC. They are not converted to local time.",
        icon=":material/schedule:",
    )

    controls = st.columns([1, 1, 1.2])
    default_start = max(min_date, max_date - timedelta(days=6))
    with controls[0]:
        start_date = st.date_input(
            "Start date (UTC)", value=default_start,
            min_value=min_date, max_value=max_date,
            help="Inclusive — from 00:00:00 UTC on this day.",
        )
    with controls[1]:
        end_date = st.date_input(
            "End date (UTC)", value=max_date,
            min_value=min_date, max_value=max_date,
            help="Inclusive — through 23:59:59 UTC on this day, so all of it is covered.",
        )
    with controls[2]:
        product_line = st.selectbox("Product line", product_lines, index=0)

    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        return

    _render_availability(days, start_date, end_date)

    try:
        status = load_report_status(product_line, start_date, end_date)
    except (GpsApiError, requests.RequestException) as exc:
        st.error(f"Could not check for existing reports: {exc}")
        return
    cached = bool(status.get("cached"))

    action = st.columns([1, 1, 2])
    with action[0]:
        submitted = st.button("Generate reports", type="primary", use_container_width=True)
    with action[1]:
        force = st.checkbox("Force regenerate", value=False, disabled=not cached)

    if cached and not submitted:
        st.info(f"Reports for this range already exist in `{status['output_dir']}`.")

    if submitted:
        try:
            if cached and not force:
                result = request_reports(product_line, start_date, end_date, force=False)
                st.success(f"Reused existing reports from `{result['output_dir']}`.")
            else:
                with st.spinner("Backend is querying ClickHouse and building reports…"):
                    result = request_reports(product_line, start_date, end_date, force=force)
                if result.get("reused"):
                    st.success(f"Reused existing reports from `{result['output_dir']}`.")
                else:
                    st.success(
                        f"Generated {len(result['report_names'])} reports in "
                        f"`{result['output_dir']}`."
                    )
        except (GpsApiError, requests.RequestException) as exc:
            st.error(f"Report generation failed: {exc}")
            return

        download = result["download"]
        # Only the reference is kept -- the archive stays on the backend until
        # the download button is actually clicked.
        st.session_state["gps_zip"] = {
            "url": download["url"],
            "name": download["filename"],
            "size_bytes": result.get("size_bytes") or 0,
            "folder_name": result.get("folder_name", ""),
            "report_names": result.get("report_names", []),
            # The result id changes on every request, so a regenerate re-fires
            # the auto-download but an ordinary rerun does not.
            "token": download["id"],
        }

    payload = st.session_state.get("gps_zip")
    if payload:
        size_mb = payload["size_bytes"] / (1024 * 1024)
        st.download_button(
            DOWNLOAD_LABEL,
            # Deferred data generation: the callable runs only when the button
            # is clicked, on its own thread. Passing bytes instead would fetch
            # the whole archive from the backend on every rerun and hold it.
            data=lambda url=payload["url"]: fetch_zip_bytes(url),
            file_name=payload["name"],
            mime="application/zip",
            use_container_width=True,
        )
        st.caption(
            f"`{payload['name']}` · {size_mb:.1f} MB · contains "
            f"`{payload['folder_name']}/` with {len(payload['report_names'])} CSV reports. "
            "Fetched from the backend on click. The download starts automatically; "
            "use the button if your browser blocks it."
        )
        _auto_download(payload["token"])
