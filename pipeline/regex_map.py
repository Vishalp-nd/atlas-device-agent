#!/usr/bin/env python3
"""Regex-based critical event type mappings loaded from unique patterns in pipeline/unique_cinfo_op.csv."""

from __future__ import annotations

import csv
import re
from pathlib import Path


CSV_PATH = Path(__file__).resolve().parent / "unique_cinfo_op.csv"


def _pattern_to_regex(pattern: str) -> str:
    escaped = re.escape(pattern.strip())
    escaped = escaped.replace(re.escape("<N>"), r".+?")
    return f"^{escaped}$"


def _load_regex_type_map() -> dict[str, str]:
    regex_type_map: dict[str, str] = {}
    with CSV_PATH.open(newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            pattern = (row.get("description_pattern") or "").strip()
            event_type = (row.get("TYPE") or "").strip()
            if not pattern or not event_type:
                continue
            # Keep only unique non-empty description patterns from the CSV.
            regex_type_map[_pattern_to_regex(pattern)] = event_type
    return regex_type_map


REGEX_TYPE_MAP = _load_regex_type_map()
