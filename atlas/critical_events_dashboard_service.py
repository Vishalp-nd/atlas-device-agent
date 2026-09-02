from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

import clickhouse_connect
import pandas as pd

DEFAULT_TABLE = "criticalinfo_snowflakes_data"
DEFAULT_SECTION = "CLICKHOUSE_DB"
PRIORITY_TABLE = "unique_cinfo_priority_map"
DEFAULT_DETAIL_LIMIT = 5000


class DashboardDataAccessError(RuntimeError):
    pass


@dataclass(frozen=True)
class DashboardConfig:
    repo_root: Path
    table_name: str = DEFAULT_TABLE
    clickhouse_section: str = DEFAULT_SECTION


def _read_clickhouse_config(repo_root: Path, section: str) -> dict[str, str]:
    parser = configparser.ConfigParser()
    db_config_path = repo_root / "db_credentials.ini"
    parser.read(db_config_path)
    if not parser.has_section(section):
        raise ValueError(f"Section '{section}' not found in {db_config_path}")
    return {
        "host": parser.get(section, "host", fallback="127.0.0.1"),
        "port": parser.get(section, "port", fallback="9000"),
        "user": parser.get(section, "user", fallback="default"),
        "password": parser.get(section, "password", fallback=""),
        "database": parser.get(section, "database", fallback="default"),
    }


def get_clickhouse_params(repo_root: str, clickhouse_section: str) -> dict[str, str]:
    return _read_clickhouse_config(Path(repo_root), clickhouse_section)


def _clickhouse_http_port(raw_port: str) -> int:
    port = int(raw_port)
    return 8123 if port == 9000 else port


def _quote_ch(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _read_clickhouse_df(config: DashboardConfig, sql: str) -> pd.DataFrame:
    params = get_clickhouse_params(str(config.repo_root), config.clickhouse_section)
    try:
        client = clickhouse_connect.get_client(
            host=params["host"],
            port=_clickhouse_http_port(params["port"]),
            username=params["user"],
            password=params.get("password") or "",
            database=params["database"],
        )
        result = client.query_df(sql)
    except Exception as exc:
        raise DashboardDataAccessError(str(exc) or "ClickHouse query failed") from exc
    return result if result is not None else pd.DataFrame()


def load_priority_map(config: DashboardConfig) -> pd.DataFrame:
    sql = f'''
        SELECT
            "CODE",
            sample_description,
            description_pattern,
            "TYPE",
            priority
        FROM {PRIORITY_TABLE}
    '''
    try:
        frame = _read_clickhouse_df(config, sql)
    except Exception:
        return pd.DataFrame(columns=["CODE", "sample_description", "description_pattern", "TYPE", "priority"])
    if frame.empty:
        return frame
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["priority"] = frame["priority"].fillna("UNMAPPED").astype(str).str.upper()
    frame["TYPE"] = frame["TYPE"].fillna("").astype(str).str.upper()
    return frame


def load_ota_summary(config: DashboardConfig, ota_versions: list[str]) -> pd.DataFrame:
    ota_filter = ""
    if ota_versions:
        versions = ", ".join(f"'{_quote_ch(version)}'" for version in ota_versions)
        ota_filter = f"WHERE \"DEVICE_VERSION\" IN ({versions})"
    sql = f'''
        SELECT
            "DEVICE_VERSION",
            type,
            sum("COUNT") AS events
        FROM {config.table_name}
        {ota_filter}
        GROUP BY "DEVICE_VERSION", type
        ORDER BY "DEVICE_VERSION", type
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return pd.DataFrame(columns=["DEVICE_VERSION", "type", "events"])
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    frame["DEVICE_VERSION"] = frame["DEVICE_VERSION"].fillna("UNKNOWN").astype(str)
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    return frame


def load_ota_date_bounds(config: DashboardConfig, ota_version: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    sql = f'''
        SELECT
            min("TIMESTAMP") AS min_timestamp,
            max("TIMESTAMP") AS max_timestamp
        FROM {config.table_name}
        WHERE "DEVICE_VERSION" = '{_quote_ch(ota_version)}'
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return None, None
    min_ts = pd.to_datetime(frame.iloc[0].get("min_timestamp"), errors="coerce")
    max_ts = pd.to_datetime(frame.iloc[0].get("max_timestamp"), errors="coerce")
    return min_ts, max_ts


def load_ota_devices(
    config: DashboardConfig,
    ota_version: str,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
) -> list[str]:
    where_clauses = [f"\"DEVICE_VERSION\" = '{_quote_ch(ota_version)}'"]
    if start_ts is not None:
        where_clauses.append(f"\"TIMESTAMP\" >= toDateTime('{start_ts.strftime('%Y-%m-%d %H:%M:%S')}')")
    if end_ts is not None:
        where_clauses.append(f"\"TIMESTAMP\" < toDateTime('{end_ts.strftime('%Y-%m-%d %H:%M:%S')}')")
    where_sql = " AND ".join(where_clauses)
    sql = f'''
        SELECT DISTINCT "DEVICE_ID"
        FROM {config.table_name}
        WHERE {where_sql}
        ORDER BY "DEVICE_ID"
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty or "DEVICE_ID" not in frame.columns:
        return []
    return sorted(device for device in frame["DEVICE_ID"].fillna("").astype(str).tolist() if device)


def _ota_where_sql(
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
) -> str:
    where_clauses = [f"\"DEVICE_VERSION\" = '{_quote_ch(ota_version)}'"]
    if device_ids:
        device_list = ", ".join(f"'{_quote_ch(device_id)}'" for device_id in device_ids)
        where_clauses.append(f"\"DEVICE_ID\" IN ({device_list})")
    if start_ts is not None:
        where_clauses.append(f"\"TIMESTAMP\" >= toDateTime('{start_ts.strftime('%Y-%m-%d %H:%M:%S')}')")
    if end_ts is not None:
        where_clauses.append(f"\"TIMESTAMP\" < toDateTime('{end_ts.strftime('%Y-%m-%d %H:%M:%S')}')")
    return " AND ".join(where_clauses)


def load_ota_type_counts(
    config: DashboardConfig,
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
) -> pd.DataFrame:
    where_sql = _ota_where_sql(ota_version, device_ids, start_ts, end_ts)
    sql = f'''
        SELECT
            type,
            sum("COUNT") AS events
        FROM {config.table_name}
        WHERE {where_sql}
        GROUP BY type
        ORDER BY type
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return pd.DataFrame(columns=["type", "events"])
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


def load_ota_priority_counts(
    config: DashboardConfig,
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
) -> pd.DataFrame:
    where_sql = _ota_where_sql(ota_version, device_ids, start_ts, end_ts)
    sql = f'''
        SELECT
            "CODE",
            type,
            sum("COUNT") AS events
        FROM {config.table_name}
        WHERE {where_sql} AND type = 'ERROR'
        GROUP BY "CODE", type
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return pd.DataFrame(columns=["priority", "events"])
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    priority_map = load_priority_map(config)
    if priority_map.empty:
        return pd.DataFrame({"priority": ["UNMAPPED"], "events": [frame["events"].sum()]})
    merged = frame.merge(
        priority_map[["CODE", "TYPE", "priority"]].drop_duplicates(),
        how="left",
        left_on=["CODE", "type"],
        right_on=["CODE", "TYPE"],
    )
    merged["priority"] = merged["priority"].fillna("UNMAPPED")
    return merged.groupby("priority", as_index=False)["events"].sum().sort_values("priority")


def load_ota_daily_counts(
    config: DashboardConfig,
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
) -> pd.DataFrame:
    where_sql = _ota_where_sql(ota_version, device_ids, start_ts, end_ts)
    sql = f'''
        SELECT
            toDate("TIMESTAMP") AS day,
            type,
            sum("COUNT") AS events
        FROM {config.table_name}
        WHERE {where_sql}
        GROUP BY day, type
        ORDER BY day, type
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return pd.DataFrame(columns=["day", "type", "events"])
    frame["day"] = pd.to_datetime(frame["day"], errors="coerce").dt.date
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


def load_ota_top_processes(
    config: DashboardConfig,
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
    limit: int = 10,
) -> pd.DataFrame:
    where_sql = _ota_where_sql(ota_version, device_ids, start_ts, end_ts)
    sql = f'''
        SELECT
            "PROCESS_NAME",
            sum("COUNT") AS events
        FROM {config.table_name}
        WHERE {where_sql}
        GROUP BY "PROCESS_NAME"
        ORDER BY events DESC
        LIMIT {int(limit)}
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return pd.DataFrame(columns=["PROCESS_NAME", "events"])
    frame["PROCESS_NAME"] = frame["PROCESS_NAME"].fillna("UNKNOWN").astype(str)
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


def load_ota_top_codes(
    config: DashboardConfig,
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
    limit: int = 10,
) -> pd.DataFrame:
    where_sql = _ota_where_sql(ota_version, device_ids, start_ts, end_ts)
    sql = f'''
        SELECT
            "CODE",
            sum("COUNT") AS events
        FROM {config.table_name}
        WHERE {where_sql} AND type = 'ERROR'
        GROUP BY "CODE"
        ORDER BY events DESC
        LIMIT {int(limit)}
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return pd.DataFrame(columns=["CODE", "events"])
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


def load_ota_top_code_details(
    config: DashboardConfig,
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
    limit: int = 10,
) -> pd.DataFrame:
    where_sql = _ota_where_sql(ota_version, device_ids, start_ts, end_ts)
    normalized_expr = "replaceRegexpAll(ifNull(events.\"DESCRIPTION\", ''), '\\S*\\d\\S*', '<N>')"
    sql = f'''
        SELECT
            events."CODE",
            coalesce(map.description_pattern, {normalized_expr}) AS description_pattern,
            sum(events."COUNT") AS events
        FROM {config.table_name} AS events
        INNER JOIN (
            SELECT
                "CODE"
            FROM {config.table_name}
            WHERE {where_sql} AND type = 'ERROR'
            GROUP BY "CODE"
            ORDER BY sum("COUNT") DESC
            LIMIT {int(limit)}
        ) AS top_codes ON events."CODE" = top_codes."CODE"
        LEFT JOIN {PRIORITY_TABLE} AS map
            ON events."CODE" = map."CODE"
           AND events.type = map."TYPE"
           AND {normalized_expr} = map.description_pattern
        WHERE {where_sql} AND events.type = 'ERROR'
        GROUP BY events."CODE", description_pattern
        ORDER BY events."CODE", events DESC, description_pattern
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return pd.DataFrame(columns=["CODE", "description_pattern", "events"])
    rename_map = {}
    for column in frame.columns:
        lowered = str(column).strip().lower()
        if lowered == "code" or lowered.endswith(".code"):
            rename_map[column] = "CODE"
        elif lowered == "description_pattern":
            rename_map[column] = "description_pattern"
        elif lowered == "events":
            rename_map[column] = "events"
    frame = frame.rename(columns=rename_map)
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["description_pattern"] = frame["description_pattern"].fillna("UNMAPPED")
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame[["CODE", "description_pattern", "events"]]


def load_ota_top_devices(
    config: DashboardConfig,
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
    limit: int = 10,
) -> pd.DataFrame:
    where_sql = _ota_where_sql(ota_version, device_ids, start_ts, end_ts)
    sql = f'''
        SELECT
            "DEVICE_ID",
            sum("COUNT") AS events
        FROM {config.table_name}
        WHERE {where_sql}
        GROUP BY "DEVICE_ID"
        ORDER BY events DESC
        LIMIT {int(limit)}
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return pd.DataFrame(columns=["DEVICE_ID", "events"])
    frame["DEVICE_ID"] = frame["DEVICE_ID"].fillna("").astype(str)
    frame["events"] = pd.to_numeric(frame["events"], errors="coerce").fillna(0)
    return frame


def load_ota_detail(
    config: DashboardConfig,
    ota_version: str,
    device_ids: list[str] | None = None,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    where_sql = _ota_where_sql(ota_version, device_ids, start_ts, end_ts)
    limit_sql = f"LIMIT {int(limit)}" if limit is not None else ""
    sql = f'''
        SELECT
            "DEVICE_ID",
            "TIMESTAMP",
            "PROCESS_NAME",
            "CODE",
            "CODE_AUX",
            "COUNT",
            "DESCRIPTION",
            "DEVICE_VERSION",
            type
        FROM {config.table_name}
        WHERE {where_sql}
        ORDER BY "TIMESTAMP" DESC
        {limit_sql}
    '''
    frame = _read_clickhouse_df(config, sql)
    if frame.empty:
        return frame
    frame["TIMESTAMP"] = pd.to_datetime(frame["TIMESTAMP"], errors="coerce")
    frame["COUNT"] = pd.to_numeric(frame["COUNT"], errors="coerce").fillna(0)
    frame["CODE"] = pd.to_numeric(frame["CODE"], errors="coerce")
    frame["DEVICE_VERSION"] = frame["DEVICE_VERSION"].fillna("UNKNOWN").astype(str)
    frame["DEVICE_ID"] = frame["DEVICE_ID"].fillna("").astype(str)
    frame["type"] = frame["type"].fillna("UNKNOWN").astype(str).str.upper()
    priority_map = load_priority_map(config)
    if priority_map.empty:
        frame["priority"] = "UNMAPPED"
        return frame

    merged = frame.merge(
        priority_map[["CODE", "TYPE", "priority"]].drop_duplicates(),
        how="left",
        left_on=["CODE", "type"],
        right_on=["CODE", "TYPE"],
    )
    merged["priority"] = merged["priority"].fillna("UNMAPPED")
    return merged