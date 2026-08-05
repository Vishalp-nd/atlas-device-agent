import configparser
import os
from typing import Dict

import psycopg2
from psycopg2.pool import ThreadedConnectionPool


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

    return {
        "database": parser.get(section, "database"),
        "host": parser.get(section, "host"),
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
