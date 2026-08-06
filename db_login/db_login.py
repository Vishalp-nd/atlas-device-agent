import configparser
import logging
import os
import socket
from typing import Dict

import psycopg2
from psycopg2.pool import ThreadedConnectionPool


logger = logging.getLogger(__name__)


def _resolve_host(section: str, host: str) -> str:
    """Resolve DB host with env overrides and Linux-safe fallback."""
    section_override = os.getenv(f"{section}_HOST", "").strip()
    global_override = os.getenv("DB_HOST_OVERRIDE", "").strip()
    resolved_host = section_override or global_override or host

    if section_override or global_override:
        source = f"{section}_HOST" if section_override else "DB_HOST_OVERRIDE"
        logger.warning(
            "Using DB host override from %s for section %s: %s",
            source,
            section,
            resolved_host,
        )

    if resolved_host != "host.docker.internal":
        return resolved_host

    try:
        socket.gethostbyname(resolved_host)
        return resolved_host
    except OSError:
        fallback_host = os.getenv("DB_DOCKER_HOST_FALLBACK", "127.0.0.1").strip() or "127.0.0.1"
        logger.warning(
            "host.docker.internal is not resolvable on this host; using fallback %s for section %s. "
            "Set %s_HOST or DB_HOST_OVERRIDE to control this explicitly.",
            fallback_host,
            section,
            section,
        )
        return fallback_host


def _default_config_paths() -> list[str]:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    return [
        os.getenv("DB_CREDENTIALS_FILE", ""),
        os.path.join(repo_root, "db_credentials.ini"),
        os.path.join(os.path.dirname(__file__), "db_credentials.ini"),
    ]


def read_db_config(section: str, config_file: str | None = None) -> Dict[str, str]:
    parser = configparser.ConfigParser()

    paths = [config_file] if config_file else _default_config_paths()
    paths = [p for p in paths if p]

    read_ok = parser.read(paths)
    if not read_ok:
        raise FileNotFoundError(
            f"No db credentials file found. Checked: {paths}"
        )

    if not parser.has_section(section):
        raise ValueError(f"Section '{section}' not found in {read_ok}")

    host = parser.get(section, "host")
    resolved_host = _resolve_host(section, host)

    return {
        "database": parser.get(section, "database"),
        "host": resolved_host,
        "user": parser.get(section, "user"),
        "password": parser.get(section, "password"),
        "port": parser.get(section, "port"),
    }


def db_connect_pool(section: str, minconn: int = 1, maxconn: int = 20) -> ThreadedConnectionPool:
    params = read_db_config(section)
    return ThreadedConnectionPool(
        minconn=minconn,
        maxconn=maxconn,
        dbname=params["database"],
        host=params["host"],
        user=params["user"],
        password=params["password"],
        port=params["port"],
    )


def db_connect(section: str):
    params = read_db_config(section)
    return psycopg2.connect(
        dbname=params["database"],
        host=params["host"],
        user=params["user"],
        password=params["password"],
        port=params["port"],
    )


def connect_to_db(params):
    return psycopg2.connect(
        dbname=params["database"],
        host=params["host"],
        user=params["user"],
        password=params["password"],
        port=params["port"],
    )
