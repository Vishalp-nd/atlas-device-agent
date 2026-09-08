"""gps_summary_service.py — backend data access for the GPS Summary dashboard.

Everything that needs ClickHouse, the local OUTPUT/ tree, or the report
generator itself lives here, so it only ever runs in the FastAPI process. The
Streamlit page is a pure HTTP client and needs neither clickhouse_connect nor a
copy of OUTPUT/ — same split as critical_events_dashboard_service.py.

Wraps scripts/gps_performance_report.py. Reports for a range that has already
been generated are reused rather than re-queried, keyed on a deterministic
output path.
"""

from __future__ import annotations

import sys
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import gps_performance_report as G

FALLBACK_PRODUCT_LINES = ["octo"]


class GpsDataAccessError(RuntimeError):
    """ClickHouse (or the report generator) could not serve the request."""


@dataclass(frozen=True)
class GpsSummaryConfig:
    repo_root: Path
    clickhouse_section: str = G.DEFAULT_CLICKHOUSE_SECTION

    @property
    def output_root(self) -> Path:
        return self.repo_root / "OUTPUT"

    @property
    def report_root(self) -> Path:
        return self.output_root / "gps_performance_reports"


# ---------------------------------------------------------------------------
# Data availability + product lines
# ---------------------------------------------------------------------------

def load_available_days(config: GpsSummaryConfig) -> pd.DataFrame:
    """One row per calendar day that has observation data, with its file count."""
    try:
        client = G._clickhouse_client(config.clickhouse_section)
        frame = client.query_df(
            "SELECT toDate(start_time) AS day, count() AS files "
            "FROM observation_data WHERE start_time IS NOT NULL "
            "GROUP BY day ORDER BY day"
        )
    except Exception as exc:
        raise GpsDataAccessError(str(exc) or "ClickHouse query failed") from exc

    if frame is None or frame.empty:
        return pd.DataFrame(columns=["day", "files"])
    frame["day"] = pd.to_datetime(frame["day"]).dt.date
    return frame


def discover_product_lines(config: GpsSummaryConfig) -> list[str]:
    """Product lines that actually have polled device data on disk.

    Mirrors the layout load_installed_devices() globs:
    OUTPUT/<product_line>/<ota>/polling/<date>/device_data_<ota>.csv
    """
    output_root = config.output_root
    if not output_root.is_dir():
        return list(FALLBACK_PRODUCT_LINES)

    found = [
        entry.name
        for entry in sorted(output_root.iterdir())
        if entry.is_dir() and any(entry.glob("*/polling/*/device_data_*.csv"))
    ]
    return found or list(FALLBACK_PRODUCT_LINES)


# ---------------------------------------------------------------------------
# Report locations and reuse
# ---------------------------------------------------------------------------

def report_context(
    config: GpsSummaryConfig, product_line: str, start: date, end_inclusive: date
) -> dict:
    """Deterministic output paths for one (product line, range) request.

    Both dates are INCLUSIVE and in UTC, matching the script's --start/--end, so
    the same dates typed at the CLI and picked in the UI produce the same window
    and the same output directory. The whole of end_inclusive is covered, which
    is why the exclusive SQL bound is that day plus one -- exactly what
    _inclusive_end_to_exclusive() does for a date-only --end.

    A fixed stem — rather than the CLI's run-timestamped default — is what makes
    the "already generated?" check possible.
    """
    slug = G._date_range_slug(start.isoformat(), end_inclusive.isoformat())
    stem = G._sanitize_name(f"GPS_{product_line}_{slug}")
    output_dir = config.report_root / product_line / slug
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
    """Zip the reports into a single folder inside the archive."""
    zip_path = context["zip_path"]
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    folder = context["folder_name"]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for report in context["reports"]:
            archive.write(report, arcname=f"{folder}/{report.name}")
    return zip_path


def generate(config: GpsSummaryConfig, context: dict, product_line: str) -> Path:
    """Run the report generator for this range, then zip the output."""
    device_list_root = config.output_root / product_line
    tenant_map = G.load_installed_devices(device_list_root)
    device_ids = sorted(tenant_map)
    if not device_ids:
        raise FileNotFoundError(
            f"No {G.INSTALLED_STATE} devices found under {device_list_root}"
        )

    try:
        G.generate_reports(
            start=context["start_dt"],
            end=context["end_dt"],
            output_dir=context["output_dir"],
            tenant_map=tenant_map,
            device_ids=device_ids,
            section=config.clickhouse_section,
            custom_stem=context["stem"],
            per_device_files=False,
            max_buffer_rows=G.DEFAULT_MAX_BUFFER_ROWS,
        )
    except Exception as exc:
        raise GpsDataAccessError(f"Report generation failed: {exc}") from exc
    return build_zip(context)


def ensure_reports(
    config: GpsSummaryConfig,
    product_line: str,
    start: date,
    end_inclusive: date,
    force: bool = False,
) -> tuple[dict, bool]:
    """Return (context, reused). Generates only when missing or force is set."""
    context = report_context(config, product_line, start, end_inclusive)
    if is_cached(context) and not force:
        return context, True
    generate(config, context, product_line)
    return context, False
