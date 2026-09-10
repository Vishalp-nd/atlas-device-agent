"""Shared ClickHouse plumbing for the retention scripts.

Used by scripts/purge_old_data.py (nightly rolling-window purge) and
scripts/repartition_clickhouse_daily.py (one-time migration to daily partitions).

Deliberately does NOT import from pipeline/critical_events_pipeline.py: that module
pulls in cinfo_classifier and the whole SVM/semantic-similarity stack at import time,
which is far too heavy for a maintenance job. It also does not shell out through
`sudo docker exec` the way that pipeline does -- `sudo` from cron needs a NOPASSWD
rule and a docker binary on a minimal PATH, and a destructive job that fails silently
at 01:00 is the worst possible failure mode.
"""

from __future__ import annotations

import configparser
import os
import socket
from pathlib import Path

import clickhouse_connect
from clickhouse_connect.driver.exceptions import ClickHouseError

# Re-exported so the scripts can report driver failures without importing the driver.
__all__ = ["ClickHouseError"]

DEFAULT_CLICKHOUSE_SECTION = "CLICKHOUSE_DB"

# The only tables retention is allowed to touch, mapped to the column its partition
# key is derived from. This is an explicit allowlist, never a prefix match: a pattern
# like 'criticalinfo%' would also catch the ad-hoc criticalinfo_snowflakes_data_backup_*
# tables, which are manual copies that retention must never delete.
TABLE_TIME_COLUMNS = {
    "criticalinfo_snowflakes_data": "TIMESTAMP",
    "observation_data": "start_time",
    "video_metadata": "start_time",
}

# Drop order: video_metadata (GPS detail) before observation_data (its parent). The two
# are partition-aligned by construction -- video_metadata.start_time is denormalized from
# the parent observation row -- so either order is correct, but if a run is interrupted
# midway, observations missing their GPS detail are coherent while orphaned GPS rows
# pointing at nothing are not.
PURGE_ORDER = ["video_metadata", "observation_data", "criticalinfo_snowflakes_data"]

# A NULL start_time collapses to epoch 0 through the partition key's ifNull(), landing
# those rows in partition 19700101. They are undated, not old, so an age rule must never
# touch them. See pipeline/observation_extraction.py epoch_to_utc(), which returns None
# for a missing, negative or out-of-range startTime.
SENTINEL_PARTITIONS = frozenset({"19700101"})

# Nothing in this project predates 2023. A computed cutoff below this means a bad
# --months or a system clock jump, not genuinely ancient data.
DEFAULT_MIN_PARTITION = "20230101"

# ClickHouse error raised when a partition exceeds the server's max_partition_size_to_drop.
TABLE_SIZE_EXCEEDS_MAX_DROP_SIZE_LIMIT = 359


def quote_identifier(name: str) -> str:
    """Backtick-quote a ClickHouse identifier."""
    return "`" + name.replace("`", "``") + "`"


def resolve_host(host: str, section: str) -> str:
    """Resolve a configured host, falling back when host.docker.internal is unavailable.

    db_credentials.ini ships with host=host.docker.internal so Atlas can run in Docker
    against databases on the host. That name does not resolve when the caller is itself
    on the host, which is the normal case for a cron job. Mirrors _resolve_db_host in
    pipeline/critical_events_pipeline.py and the inline chain in observation_extraction.py.
    """
    if host != "host.docker.internal":
        return host

    override = os.environ.get(f"{section.upper()}_HOST", "").strip() or os.environ.get(
        "DB_HOST_OVERRIDE", ""
    ).strip()
    if override:
        return override

    try:
        socket.gethostbyname(host)
        return host
    except OSError:
        fallback = os.environ.get("DB_DOCKER_HOST_FALLBACK", "").strip() or "127.0.0.1"
        print(
            f"WARNING: {host} is not resolvable here; using {fallback} for section {section}. "
            f"Set {section.upper()}_HOST or DB_HOST_OVERRIDE to control this explicitly."
        )
        return fallback


def read_config(config_file: Path, section: str) -> dict[str, str]:
    parser = configparser.ConfigParser()
    if not parser.read(config_file):
        raise FileNotFoundError(f"Could not read {config_file}")
    if not parser.has_section(section):
        raise ValueError(f"Section '{section}' not found in {config_file}")
    return {
        "host": parser.get(section, "host", fallback="127.0.0.1"),
        "port": parser.get(section, "port", fallback="9000"),
        "user": parser.get(section, "user", fallback="default"),
        "password": parser.get(section, "password", fallback=""),
        "database": parser.get(section, "database", fallback="default"),
    }


def http_port(raw_port: str) -> int:
    """db_credentials.ini lists the native port; clickhouse_connect speaks HTTP."""
    port = int(raw_port)
    return 8123 if port == 9000 else port


def connect(config_file: Path, section: str, timeout: int | None = None):
    """Return (client, database_name) for the configured ClickHouse.

    `timeout` (seconds) raises the HTTP read timeout above clickhouse_connect's default,
    which the migration's INSERT ... SELECT over hundreds of millions of rows exceeds.
    """
    params = read_config(config_file, section)
    database = params["database"]
    extra = {"send_receive_timeout": timeout} if timeout else {}
    client = clickhouse_connect.get_client(
        host=resolve_host(params["host"], section),
        port=http_port(params["port"]),
        username=params["user"],
        password=params["password"],
        database=database,
        **extra,
    )
    return client, database


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:,.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:,.2f} TiB"
