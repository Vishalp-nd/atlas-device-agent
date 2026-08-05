import csv
import os
import time
from typing import Iterable, Optional

from db_login.db_login import connect_to_db, read_db_config


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


def fetch_device_list(
    trigger_hash: str,
    output_dir: str,
    ota_versions: Optional[Iterable[str]] = None,
    device_ids: Optional[Iterable[str]] = None,
    section: str = "PROD_DB",
) -> str:
    start_time = time.time()
    params = read_db_config(section)
    conn = connect_to_db(params)
    if conn is None:
        raise RuntimeError(f"Failed to connect to database section {section}")

    all_results = []
    try:
        if ota_versions:
            for ota_version in ota_versions:
                query = f'''
                SELECT
                    n.manufacturer_device_id AS device_id,
                    n.state_id,
                    n.pushed_merge_config_version AS config
                FROM nddevicemaster n
                WHERE n.c_p_version = '{ota_version.strip()}'
                LIMIT 500
                '''
                results = _execute_query(conn, query)
                for row in results or []:
                    device_id = row["device_id"]
                    state_id = int(row["state_id"])
                    config = row["config"]
                    state_name = STATE_MAP.get(state_id, str(state_id))
                    all_results.append([device_id, ota_version, config, "", "", "", state_name])

        if device_ids:
            device_ids_str = ", ".join(f"'{device_id}'" for device_id in device_ids)
            query = f'''
            SELECT
                n.manufacturer_device_id AS device_id,
                n.c_p_version AS ota_version,
                n.state_id,
                n.pushed_merge_config_version AS config
            FROM nddevicemaster n
            WHERE n.manufacturer_device_id IN ({device_ids_str})
            '''
            results = _execute_query(conn, query)
            for row in results or []:
                device_id = row["device_id"]
                ota_version = row["ota_version"]
                state_id = int(row["state_id"])
                config = row["config"]
                state_name = STATE_MAP.get(state_id, str(state_id))
                all_results.append([device_id, ota_version, config, "", "", "", state_name])

        if all_results:
            found_device_ids = [row[0] for row in all_results]
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

            for result in all_results:
                device_id = result[0]
                if device_id in tenant_dict:
                    tenant_id, tenant_display_name, tenant_unique_name = tenant_dict[device_id]
                    result[3] = tenant_id
                    result[4] = tenant_display_name
                    result[5] = tenant_unique_name

        trigger_output_dir = os.path.join(output_dir, f"trigger_{trigger_hash}")
        os.makedirs(trigger_output_dir, exist_ok=True)
        output_csv_file = os.path.join(trigger_output_dir, "device_list.csv")
        with open(output_csv_file, "w", newline="") as csvfile:
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
            writer.writerows(all_results)

        _ = time.time() - start_time
        return output_csv_file
    finally:
        conn.close()