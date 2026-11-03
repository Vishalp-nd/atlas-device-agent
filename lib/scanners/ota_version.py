"""OTA version over time, from otacheck.log CurrentVersion lines."""

import re

import cfg_parser_logs
from log_search import line_timestamp

STEP = 'ota_lookup'
OTA_VERSION_RE = re.compile(r'CurrentVersion\s+([^\s,]+)')


def ota_versions(logs_dirs):
    """Sorted [(ts, version)] of the CurrentVersion lines in otacheck.log."""
    files = [p for d in logs_dirs for p in sorted(d.glob('otacheck*.log*'))]
    versions = set()
    for _, line in cfg_parser_logs.grep_lines(['-F', '-e', 'CurrentVersion'], files):
        m = OTA_VERSION_RE.search(line)
        ts = line_timestamp(line)
        if m and ts is not None:
            versions.add((ts, m.group(1)))
    return sorted(versions)


def scan(ctx):
    return {'versions': ota_versions([ctx.logs_dir])}
