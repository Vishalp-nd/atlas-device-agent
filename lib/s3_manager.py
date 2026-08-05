import os
from typing import Dict, List
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from lib.date_range import subtract_date_range_main
from lib.logger import Logger


logger = Logger("s3_manager")


class S3Manager:
    def __init__(self):
        # EC2 instance role is used by default credential chain.
        self._session = boto3.Session()

    def get_s3_client(self):
        return self._session.client(
            "s3",
            config=Config(
                retries={"max_attempts": 10, "mode": "adaptive"},
                max_pool_connections=int(os.getenv("S3_MAX_POOL_CONNECTIONS", "80")),
            ),
        )

    def fetch_s3_path(self, row, obs_conn_pool, local_conn_pool) -> Dict[str, List]:
        device_id = str(row.get("Device_ID") or row.get("device_id") or row.get("deviceId") or "").strip()
        start_date = row.get("start_date")
        end_date = row.get("end_date")

        if not device_id or not start_date or not end_date:
            return {}

        loc_conn = None
        obs_conn = None

        try:
            loc_conn = local_conn_pool.getconn()
            pending_ranges = subtract_date_range_main(
                "extracteddata_registry", device_id, loc_conn, str(start_date), str(end_date)
            )

            if not pending_ranges:
                logger.log_info(f"No pending ranges for {device_id}; skipping")
                return {}

            obs_conn = obs_conn_pool.getconn()
            clauses = []
            params = [device_id]
            for item in pending_ranges:
                clauses.append("(n.start_time >= %s AND n.end_time <= %s)")
                params.extend([item["sd"], item["ed"]])

            query = f"""
                SELECT DISTINCT n.s3_zip_file_path AS s3_path
                FROM nddeduplication n
                WHERE n.device_id = %s
                  AND ({' OR '.join(clauses)})
            """

            with obs_conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()

            urls = []
            for row_item in rows:
                url = row_item[0] if isinstance(row_item, (tuple, list)) else row_item.get("s3_path")
                if not url:
                    continue

                parsed = urlparse(url)
                if parsed.scheme in {"s3", "https"}:
                    urls.append(url)

            if not urls:
                return {}

            return {device_id: [urls, pending_ranges]}

        except Exception as e:
            logger.log_error(f"Failed to fetch S3 paths for {device_id}: {e}")
            return {}
        finally:
            if obs_conn is not None:
                obs_conn_pool.putconn(obs_conn)
            if loc_conn is not None:
                local_conn_pool.putconn(loc_conn)
