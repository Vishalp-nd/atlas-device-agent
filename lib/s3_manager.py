import os
from typing import Dict, List
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.credentials import InstanceMetadataProvider, InstanceMetadataFetcher
from botocore.session import Session as BotocoreSession

from lib.date_range import subtract_date_range_main
from lib.logger import Logger


logger = Logger("s3_manager")


class S3Manager:
    def __init__(self):
        self._session = self._build_session()

    def _build_session(self):
        # On EC2, prefer IMDS-backed instance-role credentials explicitly so ambient
        # profile resolution cannot accidentally override them. Off EC2, fall back
        # to boto3's normal credential chain for local development.
        if self._is_running_on_ec2():
            session = self._build_ec2_session()
            if session is not None:
                logger.log_info("Using EC2 instance-role credentials for S3 access")
                return session
            logger.log_warning(
                "EC2 environment detected but IMDS credentials were unavailable; "
                "falling back to default boto3 credential chain"
            )

        logger.log_info("Using default boto3 credential chain for S3 access")
        return boto3.Session()

    def _is_running_on_ec2(self) -> bool:
        return os.path.exists('/sys/hypervisor/uuid') or os.path.exists('/sys/devices/virtual/dmi/id/product_uuid')

    def _build_ec2_session(self):
        try:
            botocore_session = BotocoreSession()
            fetcher = InstanceMetadataFetcher(
                timeout=float(os.getenv("AWS_METADATA_SERVICE_TIMEOUT", "1")),
                num_attempts=int(os.getenv("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "2")),
            )
            provider = InstanceMetadataProvider(
                iam_role_fetcher=fetcher,
            )
            creds = provider.load()
            if creds is None:
                return None

            frozen = creds.get_frozen_credentials()
            return boto3.Session(
                aws_access_key_id=frozen.access_key,
                aws_secret_access_key=frozen.secret_key,
                aws_session_token=frozen.token,
                region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-west-1",
            )
        except Exception as exc:
            logger.log_warning(f"Failed to initialize EC2 IMDS credentials: {exc}")
            return None

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
            logger.log_warning(
                f"Skipping row with missing fields: device_id={device_id}, start_date={start_date}, end_date={end_date}"
            )
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

            if not rows:
                logger.log_info(f"No OBS rows found for pending ranges for device {device_id}")
                return {}

            urls = []
            for row_item in rows:
                url = row_item[0] if isinstance(row_item, (tuple, list)) else row_item.get("s3_path")
                if not url:
                    continue

                parsed = urlparse(url)
                if parsed.scheme in {"s3", "https"}:
                    urls.append(url)

            if not urls:
                logger.log_info(f"No valid S3 URLs after filtering for device {device_id}")
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
