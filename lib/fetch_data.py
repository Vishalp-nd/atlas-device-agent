import re
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from lib.logger import Logger


logger = Logger("fetch_data")


class regionUS:
    BUCKET = "idms-production"
    OTA_PREFIX = "ota_packages"

    FAMILY_CONFIG = {
        "krait": ("krait", "2."),
        "krait2": ("krait2", "4."),
        "bagheera2": ("bagheera2", "3."),
        "bagheera3": ("bagheera3", "5."),
        "octo": ("octo", "7."),
    }

    def __init__(self):
        self.s3 = boto3.client("s3")
        for attr, (hw, major_prefix) in self.FAMILY_CONFIG.items():
            setattr(self, attr, self._latest_version(hw, major_prefix))

    @staticmethod
    def _version_key(version: str):
        parts = re.findall(r"\d+|[A-Za-z]+", version)
        key = []
        for p in parts:
            if p.isdigit():
                key.append((0, int(p)))
            else:
                key.append((1, p.lower()))
        return key

    def _latest_version(self, hw: str, major_prefix: str) -> Optional[str]:
        prefix = f"{self.OTA_PREFIX}/{hw}/"
        paginator = self.s3.get_paginator("list_objects_v2")
        versions = set()

        try:
            for page in paginator.paginate(Bucket=self.BUCKET, Prefix=prefix, Delimiter="/"):
                for cp in page.get("CommonPrefixes", []):
                    full_prefix = cp.get("Prefix", "")
                    version = full_prefix.replace(prefix, "").strip("/")
                    if version.startswith(major_prefix):
                        versions.add(version)
        except ClientError as e:
            logger.log_error(f"Failed to list OTA versions for {hw}: {e}")
            return None

        if not versions:
            return None

        return sorted(versions, key=self._version_key)[-1]
