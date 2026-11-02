"""
Collect CFG_PRSR (config parser) log lines per device per day.

Scans OUTPUT/log/<device>/<date>/logs/ (recursively, so syslogs/ is
included; rebuilt in RAM from the day's archives when present, see
lib/log_archive.py) for every date in the range and writes
OUTPUT/config/<device>/<date>.json shaped as:

  {
    "svc": [
      {"pid": 5832, "first_ts": 1791635793615, "lines": [...]},
      {"pid": 5998, "first_ts": 1791639012345, "lines": [...]}
    ],
    ...
  }

Each entry in a file's list is one boot. A new boot starts when the pid
changes or when the process uptime (second field) goes backwards, which
also catches a reboot that happens to reuse the same pid. Some services put
the epoch in the second field; those values are ignored for restart detection. Within a boot,
only the first occurrence of each message is kept (the timestamp/uptime/
tid prefix is ignored when comparing). first_ts is the epoch-ms of the boot's
first line; lib/ota_report.py uses it to find device boots.

Lines are collected with one `grep -r -F <tag>` pass per day (GNU grep), so
only the matching lines ever reach Python.

Usage:
  python lib/cfg_parser_logs.py --devices 103182502310 103182502300 \
      --start 2026-10-01 --end 2026-10-19 [--tag CFG_PRSR]
"""

import argparse
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

import log_archive
import run_logging

log = logging.getLogger('cfg_parser_logs')

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Per environment (staging/production) caches; the Log Analyzer worker sets these env vars
LOG_BASE_DIR = os.environ.get('ATLAS_LOG_ROOT', os.path.join(REPO_ROOT, 'OUTPUT', 'log'))
OUTPUT_BASE_DIR = os.environ.get('ATLAS_CONFIG_JSON_ROOT', os.path.join(REPO_ROOT, 'OUTPUT', 'config'))

# 1791635793615: 722: CFG_PRSR: I: 5998: 5998: message
# Second-field values at or above this are epoch-ms timestamps, not uptimes
EPOCH_MS_MIN = 10 ** 12

NATIVE_LOG_RE = re.compile(
    r"^(?P<ts>\d+):\s*(?P<uptime>\d+):\s*(?P<tag>\w+):\s*(?P<level>[A-Z]):\s*"
    r"(?P<pid>\d+):\s*\d+:\s*(?P<message>.*)$"
)


def daterange(start, end):
    for i in range((end - start).days + 1):
        yield start + timedelta(days=i)


GREP = shutil.which('grep', path='/usr/bin:/bin') or 'grep'
GREP_ENV = dict(os.environ, LC_ALL='C')  # byte matching, ASCII-only -i: much faster


def grep_lines(args, paths):
    """Yield (path, line) for every line GNU grep matches, streamed.

    args are the grep options/patterns, e.g. ['-F', '-e', 'CFG_PRSR'] or
    ['-r', ...] with a directory in paths. Lines are decoded as latin-1
    (identical to iso8859-15 for ASCII, and never fails).
    """
    if not paths:
        return
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen([GREP, '-a', '-H', '--null', *args, '--', *map(str, paths)],
                                stdout=subprocess.PIPE, stderr=err, env=GREP_ENV)
        try:
            for raw in proc.stdout:
                path, _, line = raw.rstrip(b'\r\n').partition(b'\0')
                yield path.decode('latin-1'), line.decode('latin-1')
        finally:
            proc.stdout.close()
            proc.wait()
        if proc.returncode > 1:  # 1 = no match
            err.seek(0)
            raise RuntimeError(f'grep exited {proc.returncode}: {err.read().decode(errors="replace")[:500]}')


def extract_boots(lines, tag):
    """Group a file's tag lines (in file order) into per-boot entries."""
    boots = []
    prev_pid = prev_uptime = None
    seen = set()
    for line in lines:
        m = NATIVE_LOG_RE.match(line)
        if not m or m.group('tag') != tag:
            continue
        pid = int(m.group('pid'))
        uptime = int(m.group('uptime'))
        # Some services log the epoch in the uptime field; only real uptimes
        # can show a restart that reused the pid.
        if uptime >= EPOCH_MS_MIN:
            uptime = None
        restarted = uptime is not None and prev_uptime is not None and uptime < prev_uptime
        if pid != prev_pid or restarted:
            boots.append({'pid': pid, 'first_ts': int(m.group('ts')), 'lines': []})
            seen = set()
            prev_uptime = None
        key = (m.group('level'), m.group('message').strip())
        if key not in seen:
            seen.add(key)
            boots[-1]['lines'].append(line)
        prev_pid = pid
        if uptime is not None:
            prev_uptime = uptime
    return boots


def extract_day(logs_dir, tag):
    result = {}
    # grep -r emits each file's matches contiguously and in file order
    hits = grep_lines(['-r', '-F', '-e', tag], [logs_dir])
    for file_path, group in itertools.groupby(hits, key=lambda h: h[0]):
        boots = extract_boots((line for _, line in group), tag)
        if boots:
            key = os.path.relpath(file_path, logs_dir)
            if key.endswith('.log'):
                key = key[:-len('.log')]
            result[key] = boots
    return dict(sorted(result.items()))


def run(devices, start_date, end_date, tag='CFG_PRSR'):
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()

    for device in devices:
        for day in daterange(start, end):
            day_dir = os.path.join(LOG_BASE_DIR, str(device), day.isoformat())
            if not log_archive.has_logs(day_dir):
                log.warning('%s %s: no logs found at %s', device, day, day_dir)
                continue
            with log_archive.day_logs(day_dir) as logs_dir:
                write_day(device, day.isoformat(), extract_day(logs_dir, tag), tag)


def write_day(device, day, data, tag='CFG_PRSR'):
    """Write extract_day() output to <OUTPUT_BASE_DIR>/<device>/<day>.json."""
    if not data:
        log.warning('%s %s: no %s lines', device, day, tag)
        return None
    out_dir = os.path.join(OUTPUT_BASE_DIR, str(device))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{day}.json")
    with open(out_path, 'w') as f:
        json.dump(data, f, indent=2)
    line_count = sum(len(b['lines']) for boots in data.values() for b in boots)
    log.info('%s %s: %d %s lines from %d files -> %s', device, day, line_count, tag, len(data), out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--devices', nargs='+', required=True, help='One or more device IDs')
    parser.add_argument('--start', required=True, help='Start date, YYYY-MM-DD')
    parser.add_argument('--end', required=True, help='End date, YYYY-MM-DD (inclusive)')
    parser.add_argument('--tag', default='CFG_PRSR', help='Log tag/thread to collect (default: CFG_PRSR)')
    args = parser.parse_args()
    run_logging.configure()
    run(args.devices, args.start, args.end, args.tag)


if __name__ == '__main__':
    main()
