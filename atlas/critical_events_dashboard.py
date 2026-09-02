from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _load_env(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _parse_env_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def configured_ota_versions(repo_root: Path) -> list[str]:
    _load_env(repo_root)
    for key in ("CINFO_REPORT", "CINFO_OTA_VERSIONS", "OTA_VERSIONS"):
        value = os.getenv(key, "").strip()
        if value:
            return _parse_env_list(value)
    return []
