"""
Fetch device configuration from the IDMS database and store as INI files.
Optionally fetch drive metrics (drive_minutes) from Snowflake via AWS SSO.

Usage:
    python fetch_device_config.py --device-ids 1234 5678 --env prod
    python fetch_device_config.py --device-ids 1234 --env prod --start-date 2026-04-20 --end-date 2026-04-24
"""

ENV_TO_DB_SECTION = {
    "prod": "PROD_DB",
    "production": "PROD_DB",
    "staging": "STAG_DB",
    "stag": "STAG_DB",
}

ENV_TO_SNOWFLAKE_SECTION = {
    "prod": "SNOWFLAKE_DB",
    "production": "SNOWFLAKE_DB",
    "staging": "SNOWFLAKE_STAG_DB",
    "stag": "SNOWFLAKE_STAG_DB",
}

import argparse
import configparser
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

def _load_snowflake_private_key_from_ssm(ssm_parameter_name, aws_profile=None):
    """Fetch Snowflake RSA private key from AWS SSM Parameter Store and return DER bytes."""
    import boto3
    from botocore.exceptions import ProfileNotFound
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    if aws_profile:
        try:
            session = boto3.Session(profile_name=aws_profile)
        except ProfileNotFound:
            print(
                f"  AWS profile '{aws_profile}' not found; falling back to default AWS credential chain"
            )
            session = boto3.Session()
    else:
        session = boto3.Session()

    ssm = session.client("ssm", region_name="us-west-1")
    resp = ssm.get_parameter(Name=ssm_parameter_name, WithDecryption=True)
    pem_data = resp["Parameter"]["Value"].encode("utf-8")

    p_key = serialization.load_pem_private_key(
        pem_data, password=None, backend=default_backend()
    )
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def read_db_config(config_file="db_credentials.ini", section="PROD_DB"):
    """Read database connection parameters from an INI config file."""
    parser = configparser.ConfigParser()
    parser.read(config_file)
    if not parser.has_section(section):
        raise ValueError(f"Section '{section}' not found in {config_file}")
    return {
        "database": parser.get(section, "database"),
        "host": parser.get(section, "host"),
        "user": parser.get(section, "user"),
        "password": parser.get(section, "password"),
        "port": parser.getint(section, "port"),
    }


def connect_to_db(params):
    """Connect to PostgreSQL and return the connection."""
    try:
        conn = psycopg2.connect(
            dbname=params["database"],
            host=params["host"],
            user=params["user"],
            password=params["password"],
            port=params["port"],
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        return conn
    except Exception as e:
        print(f"Failed to connect to database: {e}")
        return None


def connect_to_snowflake(config_file, section, aws_profile=None):
    """Connect to Snowflake using key-pair auth (private key fetched from AWS SSM via SSO)."""
    import snowflake.connector

    parser = configparser.ConfigParser()
    parser.read(config_file)
    if not parser.has_section(section):
        raise ValueError(f"Section '{section}' not found in {config_file}")

    ssm_param = parser.get(section, "pk_ssm_parameter")
    print(f"  Fetching private key from SSM: {ssm_param}")
    if aws_profile:
        print(f"  Using AWS profile: {aws_profile}")
    else:
        print("  Using default AWS credential chain (env/instance role)")
    try:
        pk_bytes = _load_snowflake_private_key_from_ssm(ssm_param, aws_profile=aws_profile)
    except Exception as e:
        print(f"  Failed to fetch private key from SSM: {e}")
        return None

    try:
        conn = snowflake.connector.connect(
            user=parser.get(section, "user"),
            account=parser.get(section, "account"),
            private_key=pk_bytes,
            role=parser.get(section, "role"),
            warehouse=parser.get(section, "warehouse"),
            database=parser.get(section, "database"),
            schema=parser.get(section, "schema"),
        )
        return conn
    except Exception as e:
        print(f"  Failed to connect to Snowflake: {e}")
        return None


def fetch_drive_minutes(sf_conn, device_ids, start_date, end_date):
    """
    Fetch VALID_DRIVE_TIME_IN_MINUTES from Snowflake for the given devices and date range.
    Returns a dict: {device_id_str: total_drive_minutes}.
    """
    placeholders = ", ".join(["%s"] * len(device_ids))
    query = f"""
        SELECT
            DEVICE_ID,
            SUM(VALID_DRIVE_TIME_IN_MINUTES) AS total_drive_minutes
        FROM
            IDMS_DAILY_DEVICE_DRIVE_METRICS_BY_OTA_VERSION_VIEW
        WHERE
            DEVICE_ID IN ({placeholders})
            AND RECORD_DATE >= %s
            AND RECORD_DATE <= %s
        GROUP BY
            DEVICE_ID
    """
    cursor = sf_conn.cursor()
    cursor.execute(query, device_ids + [start_date, end_date])
    result = {}
    for row in cursor.fetchall():
        result[str(row[0])] = round(row[1], 1)
    cursor.close()
    return result


DEVICE_CRITICAL_EVENTS_TABLE = "device_critical_event"
_BATCH_SIZE = 10_000  # rows fetched per round-trip; tune up/down for speed vs memory

DEVICE_CRITICAL_EVENTS_QUERY = """
    SELECT
        DEVICE_ID,
        TIMESTAMP,
        PROCESS_NAME,
        CODE,
        CODE_AUX,
        COUNT,
        DESCRIPTION,
        DEVICE_VERSION,
        SYS_UPTIME,
        TENANT_ID,
        UPSERT_TIME
    FROM
        {table}
    WHERE
        DEVICE_ID IN ({{placeholders}})
        AND TIMESTAMP >= %s
        AND TIMESTAMP <= %s
    ORDER BY
        TIMESTAMP DESC
"""

UNIQUE_CODE_COMBINATIONS_QUERY = """
    SELECT
        CODE,
        CODE_AUX,
        DESCRIPTION,
        COUNT(*) AS occurrence_count
    FROM
        {table}
    WHERE
        TIMESTAMP >= %s
        AND TIMESTAMP < %s
        AND CONTAINS(DEVICE_VERSION, %s)
        {extra_filters}
    GROUP BY
        CODE, CODE_AUX, DESCRIPTION
    ORDER BY
        occurrence_count DESC
    LIMIT {limit}
"""


def get_table_clustering_info(sf_conn, table=None):
    """
    Print clustering key and partition scan stats for the critical events table.
    Run this once to understand why queries are slow before tuning.
    """
    table = table or DEVICE_CRITICAL_EVENTS_TABLE
    cursor = sf_conn.cursor()
    cursor.execute(f"SELECT SYSTEM$CLUSTERING_INFORMATION('{table}')")
    print("Clustering info:", cursor.fetchone()[0])
    cursor.execute(f"SELECT SYSTEM$CLUSTERING_DEPTH('{table}')")
    print("Clustering depth:", cursor.fetchone()[0])
    cursor.close()


def execute_snowflake_query(sf_conn, query, params=None):
    """
    Execute an arbitrary Snowflake query and return results as a list of dicts.

    Args:
        sf_conn: active Snowflake connection from connect_to_snowflake()
        query:   SQL string; use %s for positional parameters
        params:  tuple or list of parameter values, or None

    Returns:
        list of dicts mapping column name → value, empty list on failure
    """
    try:
        cursor = sf_conn.cursor()
        cursor.execute(query, params or ())
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception as e:
        print(f"Snowflake query failed: {e}")
        return []


def fetch_device_critical_events(sf_conn, device_ids, start_date, end_date):
    """
    Fetch rows from the critical events table for the given devices and date range.
    Returns a list of dicts with DEVICE_ID, TIMESTAMP, PROCESS_NAME, CODE, CODE_AUX,
    COUNT, DESCRIPTION, DEVICE_VERSION, SYS_UPTIME, TENANT_ID, UPSERT_TIME.
    """
    placeholders = ", ".join(["%s"] * len(device_ids))
    query = DEVICE_CRITICAL_EVENTS_QUERY.format(
        table=DEVICE_CRITICAL_EVENTS_TABLE,
        placeholders=placeholders,
    )
    return execute_snowflake_query(sf_conn, query, device_ids + [start_date, end_date])


def fetch_unique_code_combinations(
    sf_conn,
    device_version_substring,
    start_date,
    end_date,
    process_names=None,
    tenant_ids=None,
    limit=10000,
):
    """
    Return unique (CODE, CODE_AUX, DESCRIPTION) combinations from the critical events
    table filtered by TIMESTAMP range and DEVICE_VERSION substring.

    Args:
        sf_conn:                   active Snowflake connection
        device_version_substring:  plain substring to match (e.g. '6.15.rc.1')
        start_date:                inclusive lower bound, e.g. '2026-04-01'
        end_date:                  exclusive upper bound, e.g. '2026-05-01'
        process_names:             optional list of PROCESS_NAME values to filter on
        tenant_ids:                optional list of TENANT_ID values to filter on
        limit:                     max rows returned (default 10000)

    Returns:
        list of dicts: CODE, CODE_AUX, DESCRIPTION, occurrence_count
    """
    extra_clauses = []
    extra_params = []

    if process_names:
        ph = ", ".join(["%s"] * len(process_names))
        extra_clauses.append(f"AND PROCESS_NAME IN ({ph})")
        extra_params.extend(process_names)

    if tenant_ids:
        ph = ", ".join(["%s"] * len(tenant_ids))
        extra_clauses.append(f"AND TENANT_ID IN ({ph})")
        extra_params.extend(tenant_ids)

    query = UNIQUE_CODE_COMBINATIONS_QUERY.format(
        table=DEVICE_CRITICAL_EVENTS_TABLE,
        extra_filters="\n        ".join(extra_clauses),
        limit=limit,
    )
    params = (start_date, end_date, device_version_substring) + tuple(extra_params)
    rows = execute_snowflake_query(sf_conn, query, params)
    print(f"Total unique (CODE, CODE_AUX, DESCRIPTION) combinations: {len(rows)}")
    return rows


# No ORDER BY — sorting after DISTINCT on millions of rows is expensive.
# Deduplication and sorting happen in Python after parallel fetch.
_ALL_EVENTS_BY_VERSION_QUERY = """
    SELECT DISTINCT
        CODE,
        CODE_AUX
    FROM
        {table}
    WHERE
        TIMESTAMP >= %s
        AND TIMESTAMP < %s
        AND CONTAINS(DEVICE_VERSION, %s)
"""


def _fetch_one_day(args):
    """Fetch distinct CODE/CODE_AUX for a single day. Called from thread pool."""
    import snowflake.connector
    connect_kwargs, day_start, day_end, version_substring, table = args
    try:
        conn = snowflake.connector.connect(**connect_kwargs)
        cursor = conn.cursor()
        query = _ALL_EVENTS_BY_VERSION_QUERY.format(table=table)
        cursor.execute(query, (day_start, day_end, version_substring))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return set(rows)
    except Exception as e:
        print(f"\n  [warn] failed for {day_start}: {e}")
        return set()


def stream_critical_events_to_csv(
    sf_conn,
    device_version_substring,
    output_path,
    start_date=None,
    end_date=None,
    workers=5,
):
    """
    Fetch distinct (CODE, CODE_AUX) combinations for the given DEVICE_VERSION substring
    and date range, using parallel daily queries to maximise Snowflake partition pruning.

    Each day is queried independently in a thread pool, results are merged and
    deduplicated in Python, then written to CSV sorted by CODE, CODE_AUX.

    Args:
        sf_conn:                  active Snowflake connection (used only to extract credentials)
        device_version_substring: plain substring, e.g. '6.15.rc.1'
        output_path:              destination CSV file path
        start_date:               'YYYY-MM-DD' or None (defaults to today - 14 days)
        end_date:                 'YYYY-MM-DD' or None (defaults to today)
        workers:                  parallel Snowflake connections (default 5)

    Returns:
        (output_path, total_unique_combinations)
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    today = datetime.now(timezone.utc).date()
    start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else today - timedelta(days=14)
    end   = datetime.strptime(end_date,   "%Y-%m-%d").date() if end_date   else today

    # Extract connection kwargs from the live connection so each thread can open its own
    conn_kwargs = {
        "user":        sf_conn._user,
        "account":     sf_conn._account,
        "private_key": sf_conn._private_key,
        "role":        sf_conn._role,
        "warehouse":   sf_conn._warehouse,
        "database":    sf_conn._database,
        "schema":      sf_conn._schema,
    }

    # Build one task per day
    days = [(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    tasks = [
        (conn_kwargs, str(d), str(d + timedelta(days=1)), device_version_substring, DEVICE_CRITICAL_EVENTS_TABLE)
        for d in days
    ]

    print(f"Querying {DEVICE_CRITICAL_EVENTS_TABLE} | version~='{device_version_substring}' | {start} → {end} | {len(days)} days × {workers} workers")
    t_start = time.perf_counter()

    combined: set = set()
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one_day, t): t[1] for t in tasks}
        for future in as_completed(futures):
            combined.update(future.result())
            completed += 1
            print(f"  {completed}/{len(days)} days done — {len(combined):,} unique combinations so far...", end="\r")

    print()
    sorted_rows = sorted(combined)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["CODE", "CODE_AUX"])
        writer.writerows(sorted_rows)

    t_total = time.perf_counter() - t_start
    print(f"Done. {len(sorted_rows):,} unique combinations → {output_path}  (total: {t_total:.1f}s)")
    return output_path, len(sorted_rows)


def fetch_device_configs(conn, device_ids):
    """
    Fetch config JSON for each device using a JOIN on pushed_merge_config_version.
    Returns a list of dicts with device_id, config_version, config, ota_version,
    tenant_id, tenant_display_name, tenant_unique_name, and device_state.
    """
    placeholders = ", ".join(["%s"] * len(device_ids))
    query = f"""
        SELECT
            d.manufacturer_device_id AS device_id,
            d.pushed_merge_config_version AS config_version,
            c.config_json::TEXT AS config,
            d.c_p_version AS ota_version,
            COALESCE(v.tenant_id, 0) AS tenant_id,
            COALESCE(t.tenant_display_name, '') AS tenant_display_name,
            COALESCE(t.tenant_unique_name, '') AS tenant_unique_name,
            d.state AS device_state
        FROM
            nddevicemaster d
        JOIN
            nddynamicconfigurations c
            ON d.pushed_merge_config_version = c.version
        LEFT JOIN
            ndvehicledeviceconfigurations v
            ON v.device_id = d.manufacturer_device_id_seq
        LEFT JOIN
            ndtenantmaster t
            ON t.tenant_id = v.tenant_id
        WHERE
            d.manufacturer_device_id IN ({placeholders});
    """
    cursor = conn.cursor()
    cursor.execute(query, device_ids)
    return cursor.fetchall()


def config_json_to_ini(config_json_str):
    """
    Convert the IDMS config JSON (with sections array) to a ConfigParser INI object.

    Expected JSON structure:
    {
        "sections": [
            {"sectionName": "section1", "values": {"key1": "val1", ...}},
            ...
        ]
    }
    """
    try:
        config_data = json.loads(config_json_str)
    except (json.JSONDecodeError, TypeError):
        return None

    ini = configparser.ConfigParser()
    for section in config_data.get("sections", []):
        section_name = section.get("sectionName", "")
        if not section_name:
            continue
        if not ini.has_section(section_name):
            ini.add_section(section_name)
        for key, value in section.get("values", {}).items():
            ini.set(section_name, key, str(value))
    return ini


def save_device_config(device_id, config_version, ini_config, output_dir="device_data", environment="production"):
    """Save a device's config as an INI file in the output directory."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"device_{device_id}_config.ini"
    filepath = os.path.join(output_dir, filename)

    # Add a metadata section with device info
    if not ini_config.has_section("_metadata"):
        ini_config.add_section("_metadata")
    ini_config.set("_metadata", "device_id", str(device_id))
    ini_config.set("_metadata", "config_version", str(config_version))
    ini_config.set("_metadata", "environment", str(environment))

    with open(filepath, "w") as f:
        ini_config.write(f)

    print(f"Saved config for device {device_id} (version {config_version}) → {filepath}")
    return filepath


def save_device_configs_csv(rows, output_dir="device_data", drive_minutes_map=None):
    """
    Save all device configs as a single CSV file with one row per device.

    Columns: Device_ID, OTA_Version, Config_Version, tenant_id, tenant_display_name,
    tenant_unique_name, Config (full JSON), Device_State, Drive_Minutes,
    then one column per config section containing that section's values dict as JSON.
    """
    os.makedirs(output_dir, exist_ok=True)
    if drive_minutes_map is None:
        drive_minutes_map = {}

    # First pass: collect all config section names across all devices
    all_section_names = set()
    parsed_configs = {}  # device_id -> {section_name: values_dict}
    raw_configs = {}     # device_id -> raw config JSON string

    for row in rows:
        device_id = str(row["device_id"])
        config_json_str = row.get("config", "")
        raw_configs[device_id] = config_json_str or ""

        if not config_json_str:
            parsed_configs[device_id] = {}
            continue

        try:
            config_data = json.loads(config_json_str)
        except (json.JSONDecodeError, TypeError):
            parsed_configs[device_id] = {}
            continue

        section_map = {}
        for section in config_data.get("sections", []):
            section_name = section.get("sectionName", "")
            if section_name:
                section_map[section_name] = section.get("values", {})
                all_section_names.add(section_name)
        parsed_configs[device_id] = section_map

    # Sort section names for consistent column order
    sorted_sections = sorted(all_section_names)

    # Write CSV
    csv_path = os.path.join(output_dir, "device_list_config.csv")
    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        header = [
            "Device_ID", "OTA_Version", "Config_Version",
            "tenant_id", "tenant_display_name", "tenant_unique_name",
            "Config", "Device_State", "Drive_Minutes",
        ] + sorted_sections
        writer.writerow(header)

        for row in rows:
            device_id = str(row["device_id"])
            section_map = parsed_configs.get(device_id, {})

            # Build per-section columns (JSON string of values dict, or empty)
            section_cols = []
            for sec_name in sorted_sections:
                if sec_name in section_map:
                    section_cols.append(json.dumps(section_map[sec_name]))
                else:
                    section_cols.append("")

            writer.writerow([
                device_id,
                row.get("ota_version", ""),
                row.get("config_version", ""),
                row.get("tenant_id", ""),
                row.get("tenant_display_name", ""),
                row.get("tenant_unique_name", ""),
                raw_configs.get(device_id, ""),
                row.get("device_state", ""),
                drive_minutes_map.get(device_id, ""),
            ] + section_cols)

    print(f"Saved device config CSV → {csv_path}")
    return csv_path


def main():
    parser = argparse.ArgumentParser(
        description="Fetch device config from IDMS database and store as INI files"
    )
    parser.add_argument(
        "--device-ids",
        nargs="+",
        required=True,
        help="One or more device IDs to fetch config for",
    )
    parser.add_argument(
        "--env",
        choices=["prod", "production", "staging", "stag"],
        default="prod",
        help="Target environment: prod (production DB) or staging (staging DB). Default: prod",
    )
    parser.add_argument(
        "--db-config",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db_credentials.ini"),
        help="Path to the database credentials INI file (default: ../db_credentials.ini)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "device_data"),
        help="Directory to save device config INI files (default: ../device_data/)",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Start date for drive metrics query (YYYY-MM-DD). Required for drive_minutes.",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date for drive metrics query (YYYY-MM-DD). Required for drive_minutes.",
    )
    args = parser.parse_args()

    # Resolve environment to DB section
    db_section = ENV_TO_DB_SECTION[args.env]
    env_label = "production" if db_section == "PROD_DB" else "staging"

    # Read DB config
    print(f"Reading database configuration from {args.db_config} [{db_section}] (env: {env_label})")
    try:
        db_params = read_db_config(args.db_config, db_section)
    except Exception as e:
        print(f"Error reading DB config: {e}")
        sys.exit(1)

    # Connect
    print(f"Connecting to {db_params['database']} at {db_params['host']}...")
    conn = connect_to_db(db_params)
    if not conn:
        sys.exit(1)

    try:
        device_ids = args.device_ids
        print(f"Fetching config for {len(device_ids)} device(s)...")
        rows = fetch_device_configs(conn, device_ids)

        if not rows:
            print("No devices found with the given IDs (or no config version assigned).")
            sys.exit(1)

        # Report any missing devices
        found_ids = set(str(r["device_id"]) for r in rows)
        missing = set(device_ids) - found_ids
        if missing:
            print(f"Warning: Device(s) not found or no config in DB: {', '.join(missing)}")

        # Convert and save each device config as INI
        saved_files = []
        for row in rows:
            device_id = row["device_id"]
            config_version = row["config_version"]
            config_json_str = row["config"]
            print(f"  Device {device_id} → config version {config_version}")

            if config_json_str is None or config_json_str == "":
                print(f"  Warning: Empty config for device {device_id} (version {config_version}), skipping")
                continue

            ini_config = config_json_to_ini(config_json_str)
            if ini_config is None:
                print(f"  Warning: Invalid config JSON for device {device_id}, skipping")
                continue

            filepath = save_device_config(
                device_id, config_version, ini_config, args.output_dir, env_label
            )
            saved_files.append(filepath)

        # Save combined CSV with all devices and per-section columns
        # Optionally fetch drive minutes from Snowflake
        drive_minutes_map = {}
        if args.start_date and args.end_date:
            sf_section = ENV_TO_SNOWFLAKE_SECTION[args.env]
            print(f"\nFetching drive metrics from Snowflake [{sf_section}]...")
            print(f"  Date range: {args.start_date} to {args.end_date}")
            sf_conn = connect_to_snowflake(args.db_config, sf_section)
            if sf_conn:
                try:
                    drive_minutes_map = fetch_drive_minutes(
                        sf_conn, device_ids, args.start_date, args.end_date
                    )
                    for did, mins in drive_minutes_map.items():
                        print(f"  Device {did}: {mins} drive minutes")
                    missing_dm = set(device_ids) - set(drive_minutes_map.keys())
                    if missing_dm:
                        print(f"  Warning: No drive data for device(s): {', '.join(missing_dm)}")
                finally:
                    sf_conn.close()
            else:
                print("  Warning: Snowflake connection failed, drive_minutes will be empty")

        csv_path = save_device_configs_csv(rows, args.output_dir, drive_minutes_map)

        print(f"\nDone. Saved {len(saved_files)} INI file(s) and 1 CSV to {args.output_dir}/")

    finally:
        conn.close()


if __name__ == "__main__":
    sf_conn = connect_to_snowflake("/home/vishalpraveen/Documents/Pytest/device-automation/db_credentials.ini", "SNOWFLAKE_DB")
    # stream_critical_events_to_csv(sf_conn, "6.15.rc.1", "critical_events_data_version_code_6.15.rc.1.csv")
    import csv

    rows = execute_snowflake_query(sf_conn, "SELECT CODE, DESCRIPTION FROM device_critical_event WHERE TIMESTAMP >= %s AND TIMESTAMP < %s AND CONTAINS(device_version, %s) LIMIT 10000", ("2026-06-25", "2026-06-26", "6.15.rc.1"))

    with open("snowflake_output.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["CODE", "DESCRIPTION"])
        writer.writerows((row["CODE"], row["DESCRIPTION"]) for row in rows)

    print(f"Written {len(rows)} rows to snowflake_output.csv")
    sf_conn.close()