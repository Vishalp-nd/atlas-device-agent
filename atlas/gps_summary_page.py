"""GPS Summary page — pick a date range and product line, get a zip of both reports.

Wraps scripts/gps_performance_report.py. The date picker is bounded to the range
ClickHouse actually holds observation data for, reports for a range that has
already been generated are reused rather than re-queried, and the resulting zip
auto-downloads once it is ready (with a visible button as fallback, since
browsers may block a scripted download).
"""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import gps_performance_report as G

OUTPUT_ROOT = REPO_ROOT / "OUTPUT"
REPORT_ROOT = OUTPUT_ROOT / "gps_performance_reports"
FALLBACK_PRODUCT_LINES = ["octo"]
DOWNLOAD_LABEL = "Download GPS reports (.zip)"
AVAILABILITY_TTL = 300


# ---------------------------------------------------------------------------
# Data availability + product lines
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=True, ttl=AVAILABILITY_TTL)
def load_available_days(section: str) -> pd.DataFrame:
    """One row per calendar day that has observation data, with its file count."""
    client = G._clickhouse_client(section)
    frame = client.query_df(
        "SELECT toDate(start_time) AS day, count() AS files "
        "FROM observation_data WHERE start_time IS NOT NULL "
        "GROUP BY day ORDER BY day"
    )
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["day", "files"])
    frame["day"] = pd.to_datetime(frame["day"]).dt.date
    return frame


def discover_product_lines() -> list[str]:
    """Product lines that actually have polled device data on disk.

    Mirrors the layout load_installed_devices() globs:
    OUTPUT/<product_line>/<ota>/polling/<date>/device_data_<ota>.csv
    """
    if not OUTPUT_ROOT.is_dir():
        return list(FALLBACK_PRODUCT_LINES)

    found = [
        entry.name
        for entry in sorted(OUTPUT_ROOT.iterdir())
        if entry.is_dir() and any(entry.glob("*/polling/*/device_data_*.csv"))
    ]
    return found or list(FALLBACK_PRODUCT_LINES)


# ---------------------------------------------------------------------------
# Report locations and reuse
# ---------------------------------------------------------------------------

def report_context(product_line: str, start: date, end_inclusive: date) -> dict:
    """Deterministic output paths for one (product line, range) request.

    Both dates are INCLUSIVE and in UTC, matching the script's --start/--end, so
    the same dates typed at the CLI and picked here produce the same window and
    the same output directory. The whole of end_inclusive is covered, which is
    why the exclusive SQL bound is that day plus one -- exactly what
    _inclusive_end_to_exclusive() does for a date-only --end.

    A fixed stem — rather than the CLI's run-timestamped default — is what makes
    the "already generated?" check possible.
    """
    slug = G._date_range_slug(start.isoformat(), end_inclusive.isoformat())
    stem = G._sanitize_name(f"GPS_{product_line}_{slug}")
    output_dir = REPORT_ROOT / product_line / slug
    reports = G.expected_report_paths(output_dir, stem)
    return {
        "slug": slug,
        "stem": stem,
        "output_dir": output_dir,
        "reports": reports,
        "zip_path": output_dir / f"{stem}.zip",
        "folder_name": stem,
        "start_dt": pd.Timestamp(start).to_pydatetime(),
        "end_dt": pd.Timestamp(end_inclusive + timedelta(days=1)).to_pydatetime(),
    }


def is_cached(context: dict) -> bool:
    return context["zip_path"].is_file() and all(p.is_file() for p in context["reports"])


def build_zip(context: dict) -> Path:
    """Zip the four reports into a single folder inside the archive."""
    zip_path = context["zip_path"]
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    folder = context["folder_name"]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for report in context["reports"]:
            archive.write(report, arcname=f"{folder}/{report.name}")
    return zip_path


def generate(context: dict, product_line: str, section: str) -> Path:
    """Run the report generator for this range, then zip the output."""
    device_list_root = OUTPUT_ROOT / product_line
    tenant_map = G.load_installed_devices(device_list_root)
    device_ids = sorted(tenant_map)
    if not device_ids:
        raise RuntimeError(
            f"No {G.INSTALLED_STATE} devices found under {device_list_root}"
        )

    G.generate_reports(
        start=context["start_dt"],
        end=context["end_dt"],
        output_dir=context["output_dir"],
        tenant_map=tenant_map,
        device_ids=device_ids,
        section=section,
        custom_stem=context["stem"],
        per_device_files=False,
        max_buffer_rows=G.DEFAULT_MAX_BUFFER_ROWS,
    )
    return build_zip(context)


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
    page, does not re-trigger a download; only a freshly written archive does.
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


def render_gps_summary_page() -> None:
    st.title("GPS Summary")
    st.caption(
        "Generate the GPS Observation report (all three ignition conditions, side "
        "by side) and the GPS Summary report for a UTC date range, bundled as a zip."
    )

    section = G.DEFAULT_CLICKHOUSE_SECTION

    try:
        days = load_available_days(section)
    except ImportError as exc:
        # Not a credentials problem: the app is on an interpreter without the
        # project dependencies. Blaming db_credentials.ini here sends people
        # looking in the wrong place.
        st.error(f"A required package is missing: {exc}")
        st.info(
            "Streamlit is running on an interpreter that does not have this "
            "project's dependencies. Launch it from the project venv:\n\n"
            "`.venv/bin/python -m streamlit run streamlit_app.py`\n\n"
            "or install them into the interpreter you are using: "
            "`pip install -r requirements.txt`",
            icon=":material/deployed_code_alert:",
        )
        return
    except Exception as exc:
        st.error(f"Could not read observation data availability from ClickHouse: {exc}")
        st.info(
            "Check the CLICKHOUSE_DB section of db_credentials.ini and that "
            "observation_data has been populated."
        )
        return

    if days.empty:
        st.warning("observation_data is empty — there is nothing to report on yet.")
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
        product_lines = discover_product_lines()
        product_line = st.selectbox("Product line", product_lines, index=0)

    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        return

    _render_availability(days, start_date, end_date)

    context = report_context(product_line, start_date, end_date)
    cached = is_cached(context)

    action = st.columns([1, 1, 2])
    with action[0]:
        submitted = st.button("Generate reports", type="primary", use_container_width=True)
    with action[1]:
        force = st.checkbox("Force regenerate", value=False, disabled=not cached)

    if cached and not submitted:
        st.info(f"Reports for this range already exist in `{context['output_dir']}`.")

    if submitted:
        try:
            if cached and not force:
                zip_path = context["zip_path"]
                st.success(f"Reused existing reports from `{context['output_dir']}`.")
            else:
                with st.spinner("Querying ClickHouse and building reports…"):
                    zip_path = generate(context, product_line, section)
                st.success(f"Generated 4 reports in `{context['output_dir']}`.")

            # Only the path is kept: the archive itself is never read into
            # memory here, so a large zip does not sit in session state for the
            # life of the connection.
            st.session_state["gps_zip"] = {
                "path": str(zip_path),
                "name": zip_path.name,
                # mtime in the token so a regenerate re-fires the auto-download
                # but an ordinary rerun does not.
                "token": f"{zip_path}:{zip_path.stat().st_mtime_ns}",
            }
        except FileNotFoundError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            st.error(f"Report generation failed: {exc}")
            return

    payload = st.session_state.get("gps_zip")
    if payload:
        zip_file = Path(payload["path"])
        if not zip_file.is_file():
            st.session_state.pop("gps_zip", None)
            st.warning(
                "The generated archive is no longer on disk. Generate the reports again."
            )
            return

        size_mb = zip_file.stat().st_size / (1024 * 1024)
        st.download_button(
            DOWNLOAD_LABEL,
            # Deferred data generation: the callable runs only when the button is
            # clicked, on its own thread, and returns an open handle so the
            # archive streams off disk. Passing bytes instead would read the
            # whole file into memory on every rerun and hold it there.
            data=lambda path=zip_file: path.open("rb"),
            file_name=payload["name"],
            mime="application/zip",
            use_container_width=True,
        )
        st.caption(
            f"`{payload['name']}` · {size_mb:.1f} MB · contains "
            f"`{context['folder_name']}/` with {len(context['reports'])} CSV reports. "
            "Streamed from disk on click. The download starts automatically; use "
            "the button if your browser blocks it."
        )
        _auto_download(payload["token"])
