import argparse
import csv
import json
import os
import sys
from typing import Iterable


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, os.path.pardir))

sys.path.append(REPO_ROOT)

from db_login.db_login import connect_to_db, read_db_config
from lib.fetch_data import regionUS
from lib.logger import Logger


logging = Logger("fetch_device_list_by_product_line")

STATE_MAP = {
    -1: "SHIPPED_FROM_VENDOR",
    1: "NEW",
    2: "SHIPPED_ACTIVE",
    3: "SHIPPED_SPARE",
    4: "INSTALLED",
    5: "RMA_INITIATED",
    6: "RETURN_INITIATED_NON_RMA",
    7: "RETURNED_RMA",
    8: "RETURNED_NON_RMA",
    9: "DEAD",
    10: "RMA_PENDING",
    11: "NON_RMA_PENDING",
    12: "NF_OOW",
}


def _execute_query(conn, query: str):
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        cursor.close()


def _parse_csv_arg(value: str | None):
    if not value:
        return []
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _resolve_product_lines(product_lines: Iterable[str]):
    family_config = getattr(regionUS, "FAMILY_CONFIG", {})
    requested = list(product_lines)
    invalid = [name for name in requested if name not in family_config]
    if invalid:
        valid_values = ", ".join(sorted(family_config.keys()))
        raise ValueError(
            f"Invalid product line(s): {', '.join(invalid)}. Allowed values: {valid_values}"
        )
    return requested


def _resolve_ota_versions(product_line: str, ota_versions: list[str]):
    if ota_versions:
        invalid = [
            ota for ota in ota_versions
            if not regionUS.version_matches_family(product_line, ota)
        ]
        if invalid:
            raise ValueError(
                f"OTA version(s) {', '.join(invalid)} do not belong to product line {product_line}"
            )
        return ota_versions

    versions_by_family = regionUS().all_versions_by_family()
    return versions_by_family.get(product_line, [])


def _fetch_rows_for_ota(conn, ota_version: str):
    query = f'''
    SELECT
        n.manufacturer_device_id AS device_id,
        n.state_id,
        n.pushed_merge_config_version AS config
    FROM nddevicemaster n
    WHERE n.c_p_version = '{ota_version.strip()}'
    '''
    results = _execute_query(conn, query)
    rows = []
    for row in results or []:
        state_id = int(row["state_id"])
        rows.append([
            row["device_id"],
            ota_version,
            row["config"],
            "",
            "",
            "",
            STATE_MAP.get(state_id, str(state_id)),
        ])
    return rows


def _enrich_tenant_details(conn, rows):
    if not rows:
        return rows

    found_device_ids = [row[0] for row in rows]
    device_ids_str = ", ".join(f"'{device_id}'" for device_id in found_device_ids)
    tenant_query = f'''
    SELECT
        n.tenant_id,
        d.manufacturer_device_id,
        t.tenant_display_name,
        t.tenant_unique_name
    FROM nddevicemaster d
    INNER JOIN ndvehicledeviceconfigurations n
        ON n.device_id = d.manufacturer_device_id_seq
    INNER JOIN ndtenantmaster t
        ON t.tenant_id = n.tenant_id
    WHERE d.manufacturer_device_id IN ({device_ids_str})
    '''
    tenant_results = _execute_query(conn, tenant_query)
    tenant_dict = {}
    for row in tenant_results or []:
        tenant_dict[row["manufacturer_device_id"]] = (
            row["tenant_id"],
            row["tenant_display_name"],
            row["tenant_unique_name"],
        )

    for result in rows:
        device_id = result[0]
        if device_id in tenant_dict:
            tenant_id, tenant_display_name, tenant_unique_name = tenant_dict[device_id]
            result[3] = tenant_id
            result[4] = tenant_display_name
            result[5] = tenant_unique_name

    return rows


def _write_device_list_csv(output_root: str, product_line: str, ota_version: str, rows):
    output_dir = os.path.join(output_root, "device_list", product_line, ota_version)
    os.makedirs(output_dir, exist_ok=True)
    output_csv = os.path.join(output_dir, "device_list.csv")

    with open(output_csv, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "Device_ID",
            "OTA_Version",
            "Config_Version",
            "Tenant_ID",
            "Tenant_Display_Name",
            "Tenant_Unique_Name",
            "Device_State",
        ])
        writer.writerows(rows)

    return output_csv


def _write_device_list_json(output_root: str, product_line: str, device_ids_by_ota: dict[str, list[str]]):
    output_dir = os.path.join(output_root, "device_list", product_line)
    os.makedirs(output_dir, exist_ok=True)
    output_json = os.path.join(output_dir, "device_list.json")

    with open(output_json, "w") as jsonfile:
        json.dump(device_ids_by_ota, jsonfile, indent=2, sort_keys=True)

    return output_json


def _device_ids_by_state(rows):
    device_ids_by_state = {}
    for row in rows:
        device_id = row[0]
        device_state = row[6]
        device_ids_by_state.setdefault(device_state, []).append(device_id)
    return device_ids_by_state


def fetch_device_lists_by_product_line(product_lines, ota_versions, output_root, db_section):
    params = read_db_config(db_section)
    conn = connect_to_db(params)
    if conn is None:
        raise RuntimeError(f"Failed to connect to database section {db_section}")

    written_files = []
    written_json_files = []
    try:
        for product_line in product_lines:
            selected_ota_versions = _resolve_ota_versions(product_line, ota_versions)
            device_ids_by_ota = {}
            logging.log_info(
                f"Resolved {len(selected_ota_versions)} OTA version(s) for product line {product_line}"
            )

            for ota_version in selected_ota_versions:
                logging.log_info(
                    f"Fetching device list for product line {product_line}, OTA {ota_version}"
                )
                rows = _fetch_rows_for_ota(conn, ota_version)
                rows = _enrich_tenant_details(conn, rows)
                device_ids_by_ota[ota_version] = _device_ids_by_state(rows)
                output_csv = _write_device_list_csv(output_root, product_line, ota_version, rows)
                written_files.append(output_csv)
                logging.log_info(f"Wrote device list CSV: {output_csv}")

            output_json = _write_device_list_json(output_root, product_line, device_ids_by_ota)
            written_json_files.append(output_json)
            logging.log_info(f"Wrote device list JSON: {output_json}")
    finally:
        conn.close()

    return written_files, written_json_files


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch device_list CSV files by product line into OUTPUT/device_list/<product_line>/<ota_version>/device_list.csv"
    )
    parser.add_argument(
        "--product-lines",
        required=True,
        help="Comma-separated product lines, for example octo or krait,octo",
    )
    parser.add_argument(
        "--ota-versions",
        help="Optional comma-separated OTA versions. If omitted, all discovered OTA versions for each product line are used.",
    )
    parser.add_argument(
        "--output-root",
        default=os.path.join(REPO_ROOT, "OUTPUT"),
        help="Root output directory. Defaults to REPO_ROOT/OUTPUT",
    )
    parser.add_argument(
        "--db-section",
        default="PROD_DB",
        help="Database credentials section from db_credentials.ini",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    product_lines = _resolve_product_lines(_parse_csv_arg(args.product_lines))
    ota_versions = [item.strip() for item in (args.ota_versions or "").split(",") if item.strip()]
    written_files, written_json_files = fetch_device_lists_by_product_line(
        product_lines=product_lines,
        ota_versions=ota_versions,
        output_root=args.output_root,
        db_section=args.db_section,
    )
    for path in written_files:
        print(path)
    for path in written_json_files:
        print(path)