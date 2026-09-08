from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from lib.s3_manager import S3Manager


ENV_PATH = REPO_ROOT / ".env"
ENV_KEY = "ALLOWED_OTA_VERSIONS"
BUCKET = "idms-production"
OTA_PREFIX = "ota_packages"

PRODUCT_PREFIXES = {
    "krait": "2.",
    "krait_global": "2.",
    "krait2": "4.",
    "krait2_global": "4.",
    "bagheera2": "3.",
    "bagheera2_global": "3.",
    "bagheera3": "5.",
    "bagheera3_global": "5.",
    "octo": "7.",
}

GLOBAL_PRODUCTS = {
    "krait_global",
    "krait2_global",
    "bagheera2_global",
    "bagheera3_global",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update .env ALLOWED_OTA_VERSIONS with latest US/global OTA versions.",
    )
    parser.add_argument(
        "--env-file",
        default=str(ENV_PATH),
        help="Path to the .env file to update.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merged OTA list without writing the .env file.",
    )
    return parser.parse_args()


def version_key(version: str) -> list[object]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", version)]


def parse_env_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def read_allowed_ota_versions(env_path: Path) -> list[str]:
    load_dotenv(env_path, override=False)
    if not env_path.exists():
        return []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == ENV_KEY:
            return parse_env_list(value)
    return []


def write_allowed_ota_versions(env_path: Path, ota_versions: list[str]) -> None:
    new_line = f"{ENV_KEY}={','.join(ota_versions)}"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    rewritten: list[str] = []
    updated = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _value = stripped.split("=", 1)
            if key.strip() == ENV_KEY:
                rewritten.append(new_line)
                updated = True
                continue
        rewritten.append(line)
    if not updated:
        rewritten.append(new_line)
    env_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def list_versions_for_product(s3_client, product: str, prefix: str) -> list[str]:
    paginator = s3_client.get_paginator("list_objects_v2")
    versions: set[str] = set()
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{OTA_PREFIX}/{product}/"):
        for obj in page.get("Contents", []):
            key = obj.get("Key", "")
            parts = key.split("/")
            if len(parts) < 3:
                continue
            version = parts[2].strip()
            if version.startswith(prefix):
                if product in GLOBAL_PRODUCTS and ".IN." in version:
                    continue
                versions.add(version)
    return sorted(versions, key=version_key)


def discover_latest_versions() -> list[str]:
    s3_client = S3Manager().get_s3_client()
    latest_versions: list[str] = []
    for product, prefix in PRODUCT_PREFIXES.items():
        versions = list_versions_for_product(s3_client, product, prefix)
        if versions:
            latest_versions.append(versions[-1])
    return latest_versions


def merge_versions(existing: list[str], discovered: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for version in [*existing, *discovered]:
        normalized = version.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def main() -> int:
    args = parse_args()
    env_path = Path(args.env_file).resolve()
    existing = read_allowed_ota_versions(env_path)
    discovered = discover_latest_versions()
    merged = merge_versions(existing, discovered)

    print("Existing:", ", ".join(existing) if existing else "<empty>")
    print("Discovered latest:", ", ".join(discovered) if discovered else "<none>")
    print("Merged:", ", ".join(merged) if merged else "<empty>")

    if args.dry_run:
        return 0

    write_allowed_ota_versions(env_path, merged)
    print(f"Updated {env_path} with {len(merged)} OTA versions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())