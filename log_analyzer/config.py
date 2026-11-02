"""config.py — paths and knobs for the Log Analyzer (env overrides in parentheses)."""

from __future__ import annotations

import os
from pathlib import Path

# log_analyzer/config.py -> parents[1] == atlas-device-agent/ (repo root)
REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "lib"
UI_DIR = Path(__file__).resolve().parent / "ui"

# One folder per run: run.log, reports/<ota>/ota_report.html, reports/<ota>/devices/...
RUNS_DIR = Path(os.environ.get("LOG_ANALYZER_RUNS_DIR", REPO_ROOT / "OUTPUT" / "log_analyzer_runs"))


def runs_relative(path: str | os.PathLike) -> str:
    """Path as stored in the DB: relative to RUNS_DIR ("4/reports/<ota>/ota_report.html"),
    so the same rows work wherever the repo is mounted (host, /repo in Docker, another server)."""
    return Path(os.path.relpath(path, RUNS_DIR)).as_posix()


def run_subpath(run_id: int, stored: str, under: str = "") -> Path:
    """Part of a stored path below <run_id>/<under>, for rows saved relative (current) or as
    absolute paths from another mount (older rows, e.g. /home/ubuntu/... read inside /repo)."""
    parts = Path(stored).parts
    anchor = [str(run_id)] + ([under] if under else [])
    for i in range(len(parts) - len(anchor), -1, -1):
        if list(parts[i:i + len(anchor)]) == anchor:
            return Path(*parts[i + len(anchor):])
    raise ValueError(f"{stored!r} is not under run {run_id}/{under}")
LOG_DIR = Path(os.environ.get("ATLAS_LOG_DIR", REPO_ROOT / "logs"))
SERVICE_LOG = LOG_DIR / "log_analyzer.log"

# atlas_db; poll_user owns the database, so it can create the log_analyzer schema (LOG_ANALYZER_DB_SECTION)
DB_SECTION = os.environ.get("LOG_ANALYZER_DB_SECTION", "POLL_USER_DB")
DB_SCHEMA = "log_analyzer"
SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"

# pytest device-validator tests; the image has no checkout, so the extracted steps
# are snapshotted here whenever the checkout is available (LOG_ANALYZER_TESTS_DIR, DEVICE_AUTOMATION_ROOT)
_DEVICE_AUTOMATION_ROOT = os.environ.get("DEVICE_AUTOMATION_ROOT", "/home/ubuntu/device-automation")
TESTS_DIR = Path(os.environ.get(
    "LOG_ANALYZER_TESTS_DIR",
    Path(_DEVICE_AUTOMATION_ROOT).expanduser() / "pytest_device_validator" / "tests",
))
TESTS_SNAPSHOT = REPO_ROOT / "INPUT" / "testcase_log_steps.json"

# Worker processes per run (LOG_ANALYZER_WORKERS); default: 3 per CPU, see lib/ota_report.py
# (OTA_REPORT_WORKERS_PER_CPU, OTA_REPORT_MEMORY_GB = 20)
WORKERS = int(os.environ["LOG_ANALYZER_WORKERS"]) if os.environ.get("LOG_ANALYZER_WORKERS") else None
# Guard for device-list runs (LOG_ANALYZER_MAX_DEVICES)
MAX_DEVICES = int(os.environ.get("LOG_ANALYZER_MAX_DEVICES", "500"))
# OTA runs: devices currently on the OTA (nddevicemaster.c_p_version, via
# pipeline/fetch_device_list_by_product_line.py), capped at this many (LOG_ANALYZER_OTA_DEVICE_LIMIT)
OTA_DEVICE_LIMIT = int(os.environ.get("LOG_ANALYZER_OTA_DEVICE_LIMIT", "200"))

# Everything a run touches depends on its environment, picked in the UI's top-right switch:
#   bucket      s3 bucket the device logs are downloaded from (lib/downloader.py)
#   device_db   db_credentials.ini section with nddevicemaster (OTA -> devices)
#   log_root    local log cache, <log_root>/<device>/<day>/ (kept apart so a device id
#               present in both environments never mixes their logs)
#   config_root CFG_PRSR json cache derived from those logs
# Staging keeps the original OUTPUT/log and OUTPUT/config, which other tools use.
ENVIRONMENTS = {
    "stag": {
        "label": "Staging",
        "bucket": "idms-staging",
        "device_db": os.environ.get("LOG_ANALYZER_STAG_DEVICE_DB", "STAG_DB"),
        "log_root": REPO_ROOT / "OUTPUT" / "log",
        "config_root": REPO_ROOT / "OUTPUT" / "config",
    },
    "prod": {
        "label": "Production",
        "bucket": "idms-production",
        "device_db": os.environ.get("LOG_ANALYZER_PROD_DEVICE_DB", "PROD_DB"),
        "log_root": REPO_ROOT / "OUTPUT" / "log_prod",
        "config_root": REPO_ROOT / "OUTPUT" / "config_prod",
    },
}
DEFAULT_ENVIRONMENT = "stag"

# Only one run does heavy log I/O at a time; later runs wait on this advisory lock
RUN_LOCK_KEY = 0x4C4F4741  # "LOGA"
