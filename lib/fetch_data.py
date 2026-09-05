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

    FAMILY_SP_CONFIG = {
        "krait": ("krait", ".sp.2"),
        "krait2": ("krait2", ".sp.4"),
        "bagheera2": ("bagheera2", ".sp.3"),
        "bagheera3": ("bagheera3", ".sp.5"),
        "octo": ("octo", ".sp.7"),
    }

    @classmethod
    def family_patterns(cls, family: str):
        family_entry = cls.FAMILY_CONFIG.get(family)
        sp_entry = cls.FAMILY_SP_CONFIG.get(family)
        return (
            family_entry[1] if family_entry else None,
            sp_entry[1] if sp_entry else None,
        )

    @classmethod
    def version_matches_family(cls, family: str, version: str) -> bool:
        family_prefix, sp_pattern = cls.family_patterns(family)
        if family_prefix and version.startswith(family_prefix):
            return True
        if sp_pattern and sp_pattern in version:
            return True
        return False

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

    def _list_versions(self, hw: str, major_prefix: str):
        prefix = f"{self.OTA_PREFIX}/{hw}/"
        paginator = self.s3.get_paginator("list_objects_v2")
        versions = set()
        family = next(
            (name for name, (family_hw, _) in self.FAMILY_CONFIG.items() if family_hw == hw),
            None,
        )

        try:
            for page in paginator.paginate(Bucket=self.BUCKET, Prefix=prefix, Delimiter="/"):
                for cp in page.get("CommonPrefixes", []):
                    full_prefix = cp.get("Prefix", "")
                    version = full_prefix.replace(prefix, "").strip("/")
                    if family and self.version_matches_family(family, version):
                        versions.add(version)
                    elif version.startswith(major_prefix):
                        versions.add(version)
        except ClientError as e:
            logger.log_error(f"Failed to list OTA versions for {hw}: {e}")
            return []

        return sorted(versions, key=self._version_key)

    def _latest_version(self, hw: str, major_prefix: str) -> Optional[str]:
        versions = self._list_versions(hw, major_prefix)
        if not versions:
            return None
        return versions[-1]

    def all_versions_by_family(self):
        versions_by_family = {}
        for family, (hw, major_prefix) in self.FAMILY_CONFIG.items():
            versions = self._list_versions(hw, major_prefix)
            if versions:
                versions_by_family[family] = versions
            else:
                versions_by_family[family] = []
        return versions_by_family
