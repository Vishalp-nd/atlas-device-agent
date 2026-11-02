"""
Boot-wise config + test-case validation report from downloaded device logs.

For every device over the date range (all days are read together):
  1. Collect CFG_PRSR lines per service process with its first_ts
     (lib/cfg_parser_logs.py -> OUTPUT/config/<device>/<day>.json).
  2. Detect device boots: MIN_BOOT_SERVICES or more services starting fresh
     (first CFG_PRSR line within BOOT_START_UPTIME_MS of the process start)
     within BOOT_CLUSTER_MS of each other. Short-lived services that restart
     far more often than the rest (bhcopy, ...) are left out of this count.
     A boot's first_ts is the earliest of those process starts, and boot N
     covers [first_ts N, first_ts N+1); every grep hit is bound to a boot by
     its timestamp. Lines logged before the first detected boot form boot 0, a
     partial boot that started before the logs (kept only if it has matches).
  3. OTA of each boot: first "CurrentVersion X" in otacheck.log inside the boot,
     else the last one before it. Boots are grouped by OTA.
  4. Config coverage per boot: every key in INPUT/<ota>/bagheera_config.ini
     (lib/ota_config.py) must be mentioned in the CFG_PRSR lines of the first
     start of some service inside the boot, else the key FAILs.
  5. Test cases per boot: the search_log steps of every pytest device-validator
     test (lib/testcase_log_steps.py) must be found inside the boot in order
     (each step at or after the previous step's match). The logs are searched
     with one `grep -i -f <patterns>` pass per service over all days; Python only
     sorts the hit lines into patterns and boots. Verdicts: PASS, FAIL,
     NOT_TRIGGERED (none of the expected lines appear in the boot), NO_LOG_CHECKS
     (nothing resolvable to grep for; listed once per OTA, not per boot).
  6. UDID and drive / power-monitor metrics per boot (lib/drive_metrics.py,
     lib/DRIVE_METRICS_LOGIC.md). Boots are labelled by UDID.
  7. Cloud API calls per boot (lib/api_calls.py): calls of each API and the time between them.
  8. Features enabled per boot (lib/feature_status.py): feature_mapping.ini against
     the OTA's default config plus the keys CFG_PRSR shows as overridden. What a
     service normally reads is learned over every device on the OTA, so this runs
     once all devices are done, and then completes the device pages.

Devices are processed in parallel (--workers). Output per OTA:
  OUTPUT/ota_reports/<ota>/ota_report.{json,html}        summary over devices/boots
  OUTPUT/ota_reports/<ota>/devices/<device>.{json,html}  config + tests per boot
Step details are kept for FAIL only, to keep 500-device runs a manageable size.

Usage:
  python lib/ota_report.py --devices 103182502310 103182502300 \
      --start 2026-10-01 --end 2026-10-19 [--tests-dir ...] [--product bagheera3] [--workers 8]
"""

import argparse
import bisect
import contextlib
import fcntl
import gzip
import html
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from array import array
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

import api_calls
import cfg_parser_logs
import drive_metrics
import feature_status
import log_archive
import override_llm
import run_logging
import testcase_log_steps
from downloader import OTADownloader
from run_logging import timed

log = logging.getLogger('ota_report')

REPO_ROOT = Path(__file__).resolve().parent.parent
# Per environment (staging/production) caches; the Log Analyzer worker sets these env vars
LOG_BASE_DIR = Path(os.environ.get('ATLAS_LOG_ROOT', REPO_ROOT / 'OUTPUT' / 'log'))
CONFIG_JSON_DIR = Path(os.environ.get('ATLAS_CONFIG_JSON_ROOT', REPO_ROOT / 'OUTPUT' / 'config'))
REPORT_BASE_DIR = REPO_ROOT / 'OUTPUT' / 'ota_reports'

OTA_VERSION_RE = re.compile(r'CurrentVersion\s+([^\s,]+)')
EPOCH_MS_RE = re.compile(r'(\d{13})\b')
DATETIME_RE = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:[,.](\d{3}))?')
# Leading timestamp/uptime fields: their digits are zeroed when caching which patterns
# a line matches, so repeated messages share a cache entry
LINE_PREFIX_RE = re.compile(r'^(?:\d+:\s*\d+:|\d{4}-\d{2}-\d{2} [\d:,.]+)')
ZERO_DIGITS = str.maketrans('123456789', '000000000')
KEY_TOKEN_RE = re.compile(r'[\w.]+')

# A process whose first CFG_PRSR line is this close to its start started fresh
BOOT_START_UPTIME_MS = 60 * 1000
# Fresh starts of at least MIN_BOOT_SERVICES services this close together are one device boot
BOOT_CLUSTER_MS = 3 * 60 * 1000
MIN_BOOT_SERVICES = 5
# Services that fresh-start this many times more than the median service (short-lived
# processes like bhcopy) say nothing about device boots
CHATTY_START_FACTOR = 3

# Matched lines kept per pattern per boot for the report (all timestamps are kept)
KEEP_LINES = 100
# How far back to read for the timestamp of an untimed (continuation) line
BACKSCAN_BYTES = 64 * 1024
MATCH_LINE_CHARS = 240
CLASSIFY_CACHE_MAX = 200_000

# Shown in the reports; NOT_TRIGGERED (nothing expected was logged) and NOT_APPLICABLE
# (the test is for other device types) are only counted
SHOWN_VERDICTS = ['FAIL', 'PASS', 'NO_LOG_CHECKS']
LISTED_VERDICTS = {'FAIL', 'PASS'}

# OTA major version (or the number after ".sp.") -> device type, as the test framework
# derives it (pytest_device_validator conftest._derive_device_type_from_ota)
DEVICE_TYPE_BY_MAJOR = {'2': 'krait', '3': 'bagheera2', '4': 'krait2', '5': 'bagheera3', '7': 'octo',
                        '9': 'bagheera4'}

# Set in the parent before the worker pool forks
_TESTS = []
_EVAL_TESTS = []  # tests with log steps, evaluated per boot
_PATTERNS = {}
_FEATURES = []
_INI_CACHE = {}
_DEFAULTS_CACHE = {}

# Plain (non _c) logs a critical-only upload still has; any other plain log means the
# device uploads its detailed (non critical) logs
CRITICAL_UPLOAD_PLAIN_LOGS = {'bhcopy', 'timer', 'timer2'}
# Worker processes per CPU (OTA_REPORT_WORKERS_PER_CPU): the work is grep and
# decompression with I/O waits, so more processes than cores keeps the CPUs busy
WORKERS_PER_CPU = float(os.environ.get('OTA_REPORT_WORKERS_PER_CPU', '3'))
# Memory the run may use (OTA_REPORT_MEMORY_GB): worker processes (about
# WORKER_MEMORY_GB each) plus the days being read, rebuilt in log_archive.TMP_ROOT
# (/dev/shm is RAM). A day is only started when its estimated size fits.
MEMORY_BUDGET_BYTES = int(float(os.environ.get('OTA_REPORT_MEMORY_GB', '20')) * 1024 ** 3)
WORKER_MEMORY_BYTES = int(float(os.environ.get('OTA_REPORT_WORKER_MEMORY_GB', '0.35')) * 1024 ** 3)
TMP_SHARE = 0.9  # never fill the temp filesystem past this
# Parallel LLM calls per device (OVERRIDE_LLM_CONCURRENCY)
LLM_CONCURRENCY = int(os.environ.get('OVERRIDE_LLM_CONCURRENCY', '4'))


def device_type_for_ota(ota):
    """bagheera3 / octo / bagheera4 (D470/D475) / ... for an OTA version, or None."""
    if not ota:
        return None
    sp = re.search(r'\.sp\.(\d+)\.', ota)
    return DEVICE_TYPE_BY_MAJOR.get(sp.group(1) if sp else ota.split('.')[0])


def _fmt_ts(ts):
    if ts is None:
        return ''
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def line_timestamp(line):
    """Epoch-ms of a log line (epoch-ms or UTC datetime prefix), or None."""
    m = EPOCH_MS_RE.match(line)
    if m:
        return int(m.group(1))
    m = DATETIME_RE.match(line)
    if m:
        dt = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000) + int(m.group(2) or 0)
    return None


def device_days(device, start, end):
    """Days in [start, end] with downloaded logs (archives or an extracted logs/)."""
    start_d = datetime.strptime(start, '%Y-%m-%d').date()
    end_d = datetime.strptime(end, '%Y-%m-%d').date()
    return [day.isoformat() for day in cfg_parser_logs.daterange(start_d, end_d)
            if log_archive.has_logs(LOG_BASE_DIR / str(device) / day.isoformat())]


# ── boots ───────────────────────────────────────────────────────────────

def service_name(key):
    """CFG_PRSR json key -> service: 'apm' / 'apm_c' / 'apm.log.1' -> 'apm', 'syslogs/log_17...' -> 'syslogs'."""
    if '/' in key:
        return key.split('/', 1)[0]
    return re.sub(r'(_c)?(\.log)?(\.\d+)?$', '', key)


def _first_uptime(lines):
    for line in lines:
        m = cfg_parser_logs.NATIVE_LOG_RE.match(line)
        if m and int(m.group('uptime')) < cfg_parser_logs.EPOCH_MS_MIN:
            return int(m.group('uptime'))
    return None


def load_process_boots(device, days):
    """Every service process start in the CFG_PRSR jsons, sorted by first_ts.

    Days can overlap (a day's download also holds the end of the previous one),
    so the same process is only kept once.
    """
    seen, procs = set(), []
    for day in days:
        path = CONFIG_JSON_DIR / device / f'{day}.json'
        if not path.exists():
            continue
        with open(path) as f:
            data = json.load(f)
        for key, boots in data.items():
            service = service_name(key)
            for b in boots:
                if not b['lines']:
                    continue
                # Older jsons have no first_ts
                first_ts = b.get('first_ts') or line_timestamp(b['lines'][0])
                if first_ts is None or (service, b['pid'], first_ts) in seen:
                    continue
                seen.add((service, b['pid'], first_ts))
                procs.append({'service': service, 'pid': b['pid'], 'first_ts': first_ts,
                              'uptime': _first_uptime(b['lines']), 'lines': b['lines']})
    procs.sort(key=lambda p: p['first_ts'])
    return procs


def detect_boot_starts(procs):
    """Sorted epoch-ms starts of device boots (clusters of fresh service starts)."""
    fresh = [p for p in procs if p['uptime'] is not None and p['uptime'] <= BOOT_START_UPTIME_MS]
    counts = Counter(p['service'] for p in fresh)
    if not counts:
        return []
    median = sorted(counts.values())[len(counts) // 2]
    starts = sorted((p['first_ts'] - p['uptime'], p['service']) for p in fresh
                    if counts[p['service']] <= CHATTY_START_FACTOR * median)
    boots, i = [], 0
    while i < len(starts):
        j = i
        while j < len(starts) and starts[j][0] - starts[i][0] <= BOOT_CLUSTER_MS:
            j += 1
        if len({s for _, s in starts[i:j]}) >= MIN_BOOT_SERVICES:
            boots.append(starts[i][0])
            i = j
        else:
            i += 1
    return boots


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


LOG_SPAN_CHUNK = 64 * 1024
DAY_MS = 86_400_000
LOG_TS_MIN = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def log_span(logs_dir, day):
    """Earliest and latest timestamped log line of a day, over every log file:
    {'first': [ts, file, line], 'last': [ts, file, line]} (file relative to logs/).

    Only each file's head and tail are read (log files are written in time order). A day's
    upload can hold much older lines (a device that was offline), so those count; only
    impossible timestamps (before 2020, unsynced RTC, or after the folder's day) are ignored.
    """
    lo = LOG_TS_MIN
    hi = int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000) + 2 * DAY_MS
    first = last = None
    for path in sorted(p for p in Path(logs_dir).rglob('*') if p.is_file()):
        try:
            with open(path, 'rb') as f:
                head = f.read(LOG_SPAN_CHUNK)
                size = f.seek(0, os.SEEK_END)
                f.seek(max(0, size - LOG_SPAN_CHUNK))
                tail = f.read()
        except OSError:
            continue
        rel = path.relative_to(logs_dir).as_posix()
        for chunk, newest in ((head, False), (tail, True)):
            lines = chunk.decode('latin-1').splitlines()
            for line in (reversed(lines) if newest else lines):
                ts = line_timestamp(line)
                if ts is not None and lo <= ts < hi:
                    hit = [ts, rel, line[:MATCH_LINE_CHARS]]
                    if newest and (last is None or ts > last[0]):
                        last = hit
                    if not newest and (first is None or ts < first[0]):
                        first = hit
                    break
    return {k: v for k, v in (('first', first), ('last', last)) if v}


def boot_window(boot, span, drive=None):
    """Start/end of a boot as shown in the reports, each with where it came from. A boot
    detected in the logs starts at its service restarts and ends where the next boot starts;
    the boot before the first detected one starts before the logs (shown from the first
    log line), the last one ends at the last log line."""
    first, last = span.get('first'), span.get('last')
    w = {}
    if boot['start'] is not None:
        w['start'], w['start_note'] = boot['start'], 'boot start: services restarted'
    elif drive and drive.get('drive_start'):
        w['start'] = drive['drive_start']
        w['start_note'] = 'started before the logs; drive start from time_sync udid_node'
    elif first and (boot['end'] is None or first[0] < boot['end']):
        w['start'] = first[0]
        w['start_note'] = f'started before the logs; first log line in {first[1]}'
    if boot['end'] is not None:
        w['end'], w['end_note'] = boot['end'], 'next boot started'
    elif last and (boot['start'] is None or last[0] >= boot['start']):
        w['end'], w['end_note'] = last[0], f'end of logs; last log line in {last[1]}'
    w['start_fmt'], w['end_fmt'] = _fmt_ts(w.get('start')), _fmt_ts(w.get('end'))
    return w


def boot_ota(versions, start, end):
    """First version logged in [start, end), else the last one before start."""
    lo = 0 if start is None else bisect.bisect_left(versions, (start,))
    hi = len(versions) if end is None else bisect.bisect_left(versions, (end,))
    if lo < hi:
        return versions[lo][1]
    return versions[lo - 1][1] if lo > 0 else None


def make_boots(starts, versions):
    """Boot dicts for detected boot starts; boot 0 is the partial boot before the first start."""
    edges = [None, *starts, None]
    return [{
        'index': i,
        'partial': i == 0,
        'start': edges[i],
        'end': edges[i + 1],
        'ota': boot_ota(versions, edges[i], edges[i + 1]),
    } for i in range(len(edges) - 1)]


# ── config key coverage ─────────────────────────────────────────────────

def parse_ini_keys(path):
    """[(section, key)] in file order. Hand-parsed: the ini has duplicate keys/sections."""
    keys, section, seen = [], None, set()
    with open(path, 'r', encoding='iso8859-15') as f:
        for raw in f:
            line = raw.strip()
            if not line or line[0] in ';#':
                continue
            if line.startswith('[') and ']' in line:
                section = line[1:line.index(']')].strip()
                continue
            key = re.split(r'[=:]', line, maxsplit=1)[0].strip()
            if key and (section, key) not in seen:
                seen.add((section, key))
                keys.append((section, key))
    return keys


def boot_config_lines(procs, start, end):
    """{service: lines} of each service's first process start inside [start, end)."""
    result = {}
    for p in procs:
        if start <= p['first_ts'] < end and p['service'] not in result:
            result[p['service']] = p['lines']
    return result


def check_config_keys(ini_keys, lines_by_service):
    """A key passes if some service's lines mention it as a whole word (dots included)."""
    plain = {k.lower() for _, k in ini_keys if KEY_TOKEN_RE.fullmatch(k)}
    other = {k: re.compile(r'(?<![\w.])' + re.escape(k) + r'(?![\w.])', re.IGNORECASE)
             for _, k in ini_keys if not KEY_TOKEN_RE.fullmatch(k)}
    mentioned = defaultdict(set)
    for service, lines in lines_by_service.items():
        for line in lines:
            # A maximal [\w.]+ run equal to the key == the key with no word/dot on either side
            for token in KEY_TOKEN_RE.findall(line.lower()):
                if token in plain:
                    mentioned[token].add(service)
            for key, key_re in other.items():
                if key_re.search(line):
                    mentioned[key.lower()].add(service)
    results = []
    for section, key in ini_keys:
        services = sorted(mentioned.get(key.lower(), ()))
        results.append({'section': section, 'key': key, 'status': 'PASS' if services else 'FAIL',
                        'services': services})
    return results


# ── device log search ───────────────────────────────────────────────────

def required_literal(regex):
    """Longest run of plain characters every match of regex must contain, or None.

    Used to prefilter lines with a fast substring search before running the regex.
    """
    if re.search(r'(?<!\\)\|', regex):  # alternation: no single required literal
        return None
    runs, current, i = [], '', 0
    while i < len(regex):
        c = regex[i]
        if c == '\\' and i + 1 < len(regex):
            nxt = regex[i + 1]
            if nxt.isalnum():  # \d, \s, \w ... are classes, not literals
                runs.append(current)
                current = ''
            else:
                current += nxt
            i += 2
        elif c in '*?{':  # previous char is optional
            runs.append(current[:-1])
            current = ''
            i += 1
        elif c == '[':
            runs.append(current)
            current = ''
            i = regex.find(']', i + 2) + 1 or len(regex)
        elif c in '.+()^$':
            # x+ still needs one x, so keep the run before '+'
            runs.append(current)
            current = ''
            i += 1
        else:
            current += c
            i += 1
    runs.append(current)
    best = max(runs, key=len)
    return best if len(best) >= 3 else None


def _grep_ok(bres):
    with tempfile.NamedTemporaryFile('w', encoding='latin-1', suffix='.pat') as pf:
        pf.write('\n'.join(bres) + '\n')
        pf.flush()
        rc = subprocess.run([cfg_parser_logs.GREP, '-G', '-f', pf.name, os.devnull],
                            capture_output=True, env=cfg_parser_logs.GREP_ENV).returncode
    return rc < 2


def patterns_by_service(tests):
    """{service: [(bre, regex)]} of every step pattern, deduped by regex.

    A pattern grep rejects would fail the whole -f run, so it is searched as a
    literal instead.
    """
    result = defaultdict(dict)
    for t in tests:
        for step in t['steps']:
            for bre, regex in zip(step['greps'], step['regexes']):
                result[step['service']].setdefault(regex, bre)
    all_bres = [bre for pats in result.values() for bre in pats.values()]
    if not _grep_ok(all_bres):
        for pats in result.values():
            for regex, bre in pats.items():
                if not _grep_ok([bre]):
                    log.warning('grep rejects pattern %r, searching it literally', bre)
                    pats[regex] = testcase_log_steps._bre_escape(bre)
    return {service: [(bre, regex) for regex, bre in pats.items()] for service, pats in result.items()}


def service_files(logs_dirs, service):
    # Same lookup as the offline search_log: <service>*.log*, else <service>*
    files = []
    for logs_dir in logs_dirs:
        found = sorted(logs_dir.glob(f'{service}*.log*'))
        if not found:
            found = [f for f in sorted(logs_dir.glob(f'{service}*')) if f.is_file()]
        files.extend(found)
    return files


def _ts_before(f, start, end, line_start):
    """Timestamp of the last timestamped line in bytes [start, end) of f, or None.

    line_start: whether start is the beginning of a line (else the first piece is skipped).
    """
    f.seek(start)
    lines = f.read(end - start).decode('latin-1').split('\n')
    for line in reversed(lines if line_start else lines[1:]):
        ts = line_timestamp(line)
        if ts is not None:
            return ts
    return None


def iter_service_hits(files, patterns):
    """Yield (ts, [pattern index, ...], line) for each log line matching some pattern.

    One grep pass returns every line matching any pattern; Python then tells
    which patterns each hit matches. A hit without a timestamp (continuation
    line) takes the time of the closest timestamped line before it, read back
    from the hit's byte offset.
    """
    compiled = []
    for i, (_, regex) in enumerate(patterns):
        literal = required_literal(regex)
        compiled.append((i, re.compile(regex, re.IGNORECASE), literal.lower() if literal else None))
    cache, last = {}, {}  # last: path -> (end offset, ts) of the previous hit
    handles = {}
    try:
        with tempfile.NamedTemporaryFile('w', encoding='latin-1', suffix='.pat') as pf:
            pf.write('\n'.join(bre for bre, _ in patterns) + '\n')
            pf.flush()
            for path, line in cfg_parser_logs.grep_lines(['-b', '-i', '-G', '-f', pf.name], files):
                offset, _, line = line.partition(':')
                offset = int(offset)
                ts = line_timestamp(line)
                if ts is None:
                    prev_end, prev_ts = last.get(path, (0, None))
                    f = handles.get(path) or handles.setdefault(path, open(path, 'rb'))
                    # Only the bytes since the previous hit can hold a newer timestamp
                    start = max(prev_end, offset - BACKSCAN_BYTES)
                    ts = _ts_before(f, start, offset, start == prev_end) or prev_ts
                # latin-1 is one byte per char, so len(line) is the line's byte length
                last[path] = (offset + len(line) + 1, ts)
                if ts is None:
                    continue
                m = LINE_PREFIX_RE.match(line)
                message = line[:m.end()].translate(ZERO_DIGITS) + line[m.end():] if m else line
                matched = cache.get(message)
                if matched is None:
                    lower = message.lower()
                    matched = [i for i, rx, lit in compiled if (lit is None or lit in lower) and rx.search(message)]
                    if len(cache) >= CLASSIFY_CACHE_MAX:
                        cache.clear()
                    cache[message] = matched
                if matched:
                    yield ts, matched, line
    finally:
        for f in handles.values():
            f.close()


def spool_day_hits(logs_dir, spool):
    """Write the day's test-pattern hits to spool as 'service\tts\tidx,idx\tline' records.

    Boots are only known once every day has been read, so hits are spooled and
    assigned to boots afterwards (load_hits). Returns the services that had log files.
    """
    services = set()
    for service, patterns in _PATTERNS.items():
        files = service_files([logs_dir], service)
        if not files:
            continue
        services.add(service)
        for ts, matched, line in iter_service_hits(files, patterns):
            spool.write(f'{service}\t{ts}\t{",".join(map(str, matched))}\t{line[:400]}\n')
    return services


def load_hits(spool_paths, services, boot_starts):
    """{service: {regex: {boot: (sorted timestamps, {ts: line})}}} from hit spools (one per day).

    Services without any log file map to None (NO_LOG_FILE).
    """
    hits = {service: ({regex: {} for _, regex in patterns} if service in services else None)
            for service, patterns in _PATTERNS.items()}
    regexes = {service: [regex for _, regex in patterns] for service, patterns in _PATTERNS.items()}
    for spool_path in spool_paths:
        with gzip.open(spool_path, 'rt', encoding='latin-1', newline='\n') as spool:
            for record in spool:
                service, ts, matched, line = record.rstrip('\n').split('\t', 3)
                ts = int(ts)
                boot = bisect.bisect_right(boot_starts, ts)
                for i in matched.split(','):
                    by_boot = hits[service][regexes[service][int(i)]]
                    entry = by_boot.get(boot)
                    if entry is None:
                        entry = by_boot[boot] = (array('q'), {})
                    entry[0].append(ts)
                    if len(entry[1]) < KEEP_LINES:
                        entry[1].setdefault(ts, line)
    for service_hits in hits.values():
        for by_boot in (service_hits or {}).values():
            for boot, (stamps, kept) in by_boot.items():
                by_boot[boot] = (array('q', sorted(stamps)), kept)
    return hits


def _find(service_hits, regex, boot, cursor):
    """('FOUND', ts, line) / ('OUT_OF_ORDER', ts, line) / ('NOT_FOUND' | 'NO_LOG_FILE', None, None)."""
    if service_hits is None:
        return 'NO_LOG_FILE', None, None
    entry = service_hits[regex].get(boot)
    if not entry:
        return 'NOT_FOUND', None, None
    timestamps, kept = entry
    i = bisect.bisect_left(timestamps, cursor)
    if i < len(timestamps):
        return 'FOUND', timestamps[i], kept.get(timestamps[i], '')
    return 'OUT_OF_ORDER', timestamps[-1], kept.get(timestamps[-1], '')


def _applies(item, device_type):
    types = item.get('device_types')
    return not types or device_type is None or device_type in types


def evaluate_test(test, hits, boot, boot_start, device_type=None):
    steps = test['steps']
    if not steps:
        return {'verdict': 'NO_LOG_CHECKS', 'reason': 'No resolvable search_log steps', 'steps': []}
    # Product line: tests / steps the test code only runs on other device types
    applicable = [_applies(s, device_type) for s in steps]
    if not _applies(test, device_type) or not any(applicable):
        return {'verdict': 'NOT_APPLICABLE', 'reason': f'Not run on {device_type}', 'steps': []}

    has_required = any(s['expect'] == 'present' for s, ok in zip(steps, applicable) if ok)
    cursor = boot_start or 0
    results = []
    for step, ok in zip(steps, applicable):
        if not ok:
            results.append({'method': step['method'], 'service': step['service'], 'patterns': step['patterns'],
                            'expect': step['expect'], 'required': False, 'status': 'NOT_FOR_DEVICE',
                            'match_ts': None, 'match_line': ''})
            continue
        required = step['expect'] == 'present' or (step['expect'] == 'info' and not has_required)
        service_hits = hits.get(step['service'])
        outcomes = [_find(service_hits, regex, boot, cursor) for regex in step['regexes']]

        if step['expect'] == 'absent':
            found = [o for o in outcomes if o[0] == 'FOUND']
            status = 'FAIL' if found else 'PASS'
            match = found[0] if found else ('NOT_FOUND', None, None)
        else:
            # Any one alternative (e.g. per-device-type pattern) is enough unless
            # the test needs all of them (search_logs_until_found / a loop).
            ok = [o for o in outcomes if o[0] == 'FOUND']
            needs_all = step.get('mode') == 'all'
            if (needs_all and len(ok) == len(outcomes)) or (not needs_all and ok):
                status = 'PASS'
                match = max(ok, key=lambda o: o[1]) if needs_all else min(ok, key=lambda o: o[1])
                cursor = match[1]
            else:
                missing = [o for o in outcomes if o[0] != 'FOUND']
                match = missing[0]
                status = match[0] if required else f'INFO_{match[0]}'

        results.append({
            'method': step['method'],
            'service': step['service'],
            'patterns': step['patterns'],
            'expect': step['expect'],
            'required': required,
            'status': status,
            'match_ts': match[1],
            'match_line': match[2] or '',
        })

    required = [r for r in results if r['status'] != 'NOT_FOR_DEVICE' and (r['required'] or r['expect'] == 'absent')]
    positive = [r for r in required if r['expect'] != 'absent']
    failed = [r for r in required if r['status'] != 'PASS']
    if positive and all(r['status'] in ('NOT_FOUND', 'NO_LOG_FILE') for r in positive):
        verdict, reason = 'NOT_TRIGGERED', 'None of the expected log lines appear in this boot'
    elif not failed:
        verdict, reason = 'PASS', 'All expected log lines found in order'
    else:
        first = failed[0]
        what = {'NOT_FOUND': 'not found', 'NO_LOG_FILE': 'no log file for service',
                'OUT_OF_ORDER': 'only found before the previous step',
                'FAIL': 'found but expected absent'}.get(first['status'], first['status'])
        verdict = 'FAIL'
        reason = f"{first['service']}: '{first['patterns'][0]}' {what}"
    return {'verdict': verdict, 'reason': reason, 'steps': results}


def has_detailed_logs(logs_dir):
    """Whether a day's logs include non-critical logs (plain <service>.log files)."""
    for p in logs_dir.iterdir():
        if p.is_file() and '.log' in p.name and '_c.log' not in p.name:
            if p.name.split('.log')[0] not in CRITICAL_UPLOAD_PLAIN_LOGS:
                return True
    return False


def ota_defaults(ota):
    """{section: {key: value}} of the OTA's bagheera_config.ini (after ensure_ini), or None."""
    if ota not in _DEFAULTS_CACHE:
        path = Path(OTADownloader.INPUT_DIR) / ota / 'bagheera_config.ini'
        _DEFAULTS_CACHE[ota] = feature_status.parse_ini_values(path) if path.exists() else None
    return _DEFAULTS_CACHE[ota]


def remaining_conditions(plain, defaults):
    """[(section, key, default)] of the feature keys CFG_PRSR did not list as not overridden."""
    out = set()
    for f in _FEATURES:
        for section, key, _ in f['conditions']:
            if key not in plain:
                out.add((section, key, (defaults or {}).get(section, {}).get(key)))
    return sorted(out)


def read_override_values(device, jobs, timings):
    """LLM pass over boots: jobs [(detail, conditions, evidence)]; fills detail['override_values']."""
    if not jobs:
        return
    if not override_llm.enabled():
        for detail, _, _ in jobs:
            detail['override_llm'] = 'disabled'
        return

    def one(job):
        detail, conditions, evidence = job
        try:
            values, source = override_llm.extract_values(conditions, evidence)
            detail['override_values'] = {k: v for k, v in values.items() if v is not None}
            detail['override_llm'] = source
        except Exception as exc:  # the report goes on with defaults/overrides only
            log.warning('%s %s: override LLM failed: %s', device, detail['label'], exc)
            detail['override_llm'] = f'error: {type(exc).__name__}: {exc}'[:300]
        return detail['override_llm']

    with timed(log, 'override_llm', timings, device=device, boots=len(jobs)):
        with ThreadPoolExecutor(LLM_CONCURRENCY) as pool:
            sources = Counter(s.split(':')[0] for s in pool.map(one, jobs))
    log.info('%s: override values for %d boot(s): %s', device, len(jobs), dict(sources))


def scan_device_day(device, day):
    """Read one day of a device's logs once (rebuilt in RAM from its archives when present,
    lib/log_archive.py): CFG_PRSR json, otacheck versions, test-pattern hits (spooled to a
    gzip file the caller deletes), drive events, config value lines, detailed-logs flag.

    Days are independent, so run() reads them in parallel; finish_device() puts a device
    together once all its days are read.
    """
    timings = []
    events = drive_metrics.DriveEvents()
    evidence = override_llm.EvidenceCollector({k for f in _FEATURES for _, k, _ in f['conditions']})
    spool_file = tempfile.NamedTemporaryFile(prefix=f'hits_{device}_{day}_', suffix='.gz',
                                             dir=log_archive.TMP_ROOT, delete=False)
    spool_file.close()
    try:
        with timed(log, 'read_day', timings, device=device, day=day):
            with gzip.open(spool_file.name, 'wt', encoding='latin-1', newline='\n', compresslevel=1) as spool:
                with log_archive.day_logs(LOG_BASE_DIR / device / day, timings=timings) as logs_dir:
                    with timed(log, 'cfg_parse', timings, level=logging.DEBUG, device=device, day=day):
                        cfg_parser_logs.write_day(device, day, cfg_parser_logs.extract_day(logs_dir, 'CFG_PRSR'))
                    with timed(log, 'ota_lookup', timings, level=logging.DEBUG, device=device, day=day):
                        versions = ota_versions([logs_dir])
                    with timed(log, 'grep_tests', timings, level=logging.DEBUG, device=device, day=day):
                        services = spool_day_hits(logs_dir, spool)
                    with timed(log, 'drive_scan', timings, level=logging.DEBUG, device=device, day=day):
                        events.add(drive_metrics.scan_day(logs_dir))
                    with timed(log, 'api_grep', timings, level=logging.DEBUG, device=device, day=day):
                        calls = api_calls.scan_day(logs_dir, iter_service_hits)
                    with timed(log, 'evidence_grep', timings, level=logging.DEBUG, device=device, day=day):
                        evidence.scan_day(logs_dir, line_timestamp)
                    detailed = has_detailed_logs(logs_dir)
                    with timed(log, 'log_span', timings, level=logging.DEBUG, device=device, day=day):
                        span = log_span(logs_dir, day)
    except BaseException:
        os.unlink(spool_file.name)
        raise
    return {'day': day, 'versions': versions, 'services': services, 'events': events,
            'evidence': evidence.lines, 'detailed': detailed, 'spool': spool_file.name, 'timings': timings,
            'api_calls': calls, 'log_span': span}


def process_device(device, start, end, product=None, report_dir=None, otas=None):
    """Everything for one device, its days read one after another (run() reads them in parallel)."""
    return finish_device(device, [scan_device_day(device, day) for day in device_days(device, start, end)],
                         product, report_dir, otas)


def finish_device(device, day_results, product=None, report_dir=None, otas=None):
    """Boots, OTA per boot, config + tests + drive metrics + override values per boot, from
    the scan_device_day() results of every day. Writes <report_dir>/<ota>/devices/<device>.json
    for every OTA the device has boots on (only those in otas, if given); the HTML page
    follows once the features are known (complete_device_reports).

    Returns (device, {ota: summary}, timings, info), timings being one entry per step
    (see run_logging.timed), info {'days', 'detailed_days', 'otas'} for the report summary.
    Deletes the days' hit spools.
    """
    report_dir = Path(report_dir or REPORT_BASE_DIR)
    day_results = sorted(day_results, key=lambda r: r['day'])
    days = [r['day'] for r in day_results]
    timings = [t for r in day_results for t in r['timings']]
    device_start = time.perf_counter()
    info = {'days': len(days), 'detailed_days': sum(r['detailed'] for r in day_results), 'otas': []}
    if not days:
        return device, {}, timings, info
    versions, services, calls = set(), set(), set()
    firsts = [r['log_span']['first'] for r in day_results if r.get('log_span', {}).get('first')]
    lasts = [r['log_span']['last'] for r in day_results if r.get('log_span', {}).get('last')]
    span = {k: v for k, v in (('first', min(firsts, default=None)), ('last', max(lasts, default=None))) if v}
    events = drive_metrics.DriveEvents()
    evidence = override_llm.EvidenceCollector({k for f in _FEATURES for _, k, _ in f['conditions']})
    try:
        for r in day_results:
            versions.update(r['versions'])
            services.update(r['services'])
            events.add(r['events'])
            calls.update(r['api_calls'])
            for norm, hits_ in r['evidence'].items():
                evidence.lines.setdefault(norm, []).extend(hits_)
        with timed(log, 'detect_boots', timings, level=logging.DEBUG, device=device):
            procs = load_process_boots(device, days)
            starts = detect_boot_starts(procs)
            boots = make_boots(starts, sorted(versions))
            events.finish()
            evidence.finish()
            udid_records = drive_metrics.assign_udids(events, boots)
        found_otas = sorted({b['ota'] for b in boots if b['ota']})
        info['otas'] = found_otas
        log.info('%s: %d boot(s) over %d day(s), OTA %s', device, len(starts), len(days), found_otas)
        with timed(log, 'load_hits', timings, level=logging.DEBUG, device=device):
            hits = load_hits([r['spool'] for r in day_results], services, starts)
    finally:
        for r in day_results:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(r['spool'])

    if otas:
        skipped = [o for o in found_otas if o not in otas]
        if skipped:
            log.info('%s: ignoring boots on OTA %s (not requested)', device, skipped)
        boots = [b for b in boots if b['ota'] in otas]
    eval_start = time.perf_counter()
    calls_by_api = api_calls.by_api(calls)
    inis = {ota: ensure_ini(ota, product) for ota in sorted({b['ota'] for b in boots if b['ota']})}
    ini_keys_by_ota = {ota: keys for ota, (keys, _) in inis.items() if keys is not None}
    # Boot 0 (before the first boot in the logs) is noise unless something matched in it
    partial_used = any(0 in by_boot for sh in hits.values() if sh for by_boot in sh.values())
    detail_by_ota = defaultdict(list)
    profiles = defaultdict(dict)  # ota -> {boot index: {service: not-overridden keys}}, for the features
    llm_jobs = []
    for boot in boots:
        if boot['ota'] is None or (boot['partial'] and not partial_used):
            continue
        detail = dict(boot, start_fmt=_fmt_ts(boot['start']), end_fmt=_fmt_ts(boot['end']))
        detail.update(drive_metrics.boot_metrics(events, boot['start'], boot['end'], udid_records.get(boot['index'])))
        detail['window'] = boot_window(boot, span, detail['drive'])
        detail['label'] = boot_name(detail)
        detail['api_calls'] = api_calls.boot_calls(calls_by_api, boot['start'], boot['end'])
        ini_keys = ini_keys_by_ota.get(boot['ota'])
        lines = (boot_config_lines(procs, boot['start'], boot['end'] or float('inf'))
                 if not boot['partial'] else {})
        if lines:
            profiles[boot['ota']][boot['index']] = feature_status.boot_profile(lines)
        # Feature keys CFG_PRSR did not list as not overridden: their effective value may be
        # in the services' own startup lines (read by the LLM after this loop)
        profile = profiles[boot['ota']].get(boot['index'], {})
        conditions = remaining_conditions(set().union(*profile.values()) if profile else set(),
                                          ota_defaults(boot['ota']))
        boot_evidence = evidence.boot_evidence(boot['start'], {k for _, k, _ in conditions})
        detail['override_evidence_lines'] = len(boot_evidence)
        if conditions and boot_evidence:
            llm_jobs.append((detail, conditions, boot_evidence))
        if boot['partial']:
            detail['config'] = {'error': 'Boot started before the logs, no startup CFG_PRSR lines'}
        elif ini_keys is None:
            detail['config'] = {'error': 'bagheera_config.ini not available for this OTA'}
        else:
            if not lines:
                detail['config'] = {'error': 'No CFG_PRSR lines in this boot'}
            else:
                keys = check_config_keys(ini_keys, lines)
                detail['config'] = {
                    'services': sorted(lines),
                    'summary': dict(Counter(k['status'] for k in keys)),
                    # indexes into the device doc's config_keys (the ini's keys in file order)
                    'failed_keys': [i for i, k in enumerate(keys) if k['status'] == 'FAIL'],
                }
        # Tests are referenced by index into _EVAL_TESTS (the device doc's "tests"), whose
        # static info is stored once per device; a FAIL or PASS keeps [status, match_ts, line] per step.
        detail['device_type'] = device_type_for_ota(boot['ota'])
        verdicts, failures, passes = defaultdict(list), [], []
        for i, test in enumerate(_EVAL_TESTS):
            result = evaluate_test(test, hits, boot['index'], boot['start'], detail['device_type'])
            verdicts[result['verdict']].append(i)
            if result['verdict'] == 'FAIL':
                failures.append({'test': i, 'reason': result['reason'], 'steps': _compact_steps(result['steps'])})
            elif result['verdict'] == 'PASS':
                passes.append({'test': i, 'steps': _pass_steps(result['steps'])})
        # Counts for every verdict; test lists only for the ones the report shows
        detail['summary'] = {v: len(ids) for v, ids in verdicts.items()}
        detail['verdicts'] = {v: ids for v, ids in verdicts.items() if v in LISTED_VERDICTS}
        detail['failures'] = failures
        detail['passes'] = passes
        detail_by_ota[boot['ota']].append(detail)

    timings.append({'step': 'evaluate', 'device': device, 'status': 'ok',
                    'duration_s': round(time.perf_counter() - eval_start, 3)})
    read_override_values(device, llm_jobs, timings)
    summaries = {}
    for ota, boot_details in detail_by_ota.items():
        out_dir = report_dir / ota / 'devices'
        out_dir.mkdir(parents=True, exist_ok=True)
        ini_keys = ini_keys_by_ota.get(ota)
        doc = device_doc(device, ota, days, boot_details, ini_keys)
        # The HTML page is written once the features are known (complete_device_reports)
        with open(out_dir / f'{device}.json', 'w') as f:
            json.dump(doc, f, separators=(',', ':'))
        summaries[ota] = summarize_boots(device, boot_details, ini_keys)
        summaries[ota]['config_keys'] = len(ini_keys or [])
        summaries[ota]['config_error'] = inis[ota][1]
        summaries[ota]['drive'] = doc['drive_summary']
        summaries[ota]['api'] = doc['api_summary']
        summaries[ota]['profiles'] = profiles.get(ota, {})
    total = round(time.perf_counter() - device_start, 3)
    timings.append({'step': 'device_total', 'device': device, 'status': 'ok', 'duration_s': total})
    log.info('%s: evaluated %d boot(s), device took %.2fs', device,
             sum(len(v) for v in detail_by_ota.values()), total)
    return device, summaries, timings, info


def _compact_steps(steps):
    """[status, match_ts, match_line] per step. Matched lines are kept only where they
    explain the failure: non-PASS steps and the last PASS step before the first failure."""
    first_bad = next((i for i, s in enumerate(steps) if s['status'] != 'PASS'), len(steps))
    anchor = max((i for i in range(first_bad) if steps[i]['match_ts']), default=None)
    return [[s['status'], s['match_ts'],
             s['match_line'][:MATCH_LINE_CHARS] if s['status'] != 'PASS' or i == anchor else '']
            for i, s in enumerate(steps)]


def _pass_steps(steps):
    """[status, match_ts, match_line] per step of a passing test; every matched line is kept
    (it is the evidence for the PASS)."""
    return [[s['status'], s['match_ts'], (s['match_line'] or '')[:MATCH_LINE_CHARS]] for s in steps]


def _required(step, steps):
    has_required = any(s['expect'] == 'present' for s in steps)
    return step['expect'] == 'present' or (step['expect'] == 'info' and not has_required)


def device_doc(device, ota, days, boot_details, ini_keys):
    """Per-device JSON. Boots refer to "tests" and "config_keys" by index; step
    details are only included for tests that failed or passed in some boot."""
    with_steps = {r['test'] for b in boot_details for r in b['failures'] + b.get('passes', [])}
    tests = []
    for i, t in enumerate(_EVAL_TESTS):
        info = {'key': t['key'], 'tc_id': t['tc_id'], 'feature': t['feature'], 'functionality': t['functionality'],
                'device_types': t.get('device_types')}
        if i in with_steps:
            info['steps'] = [{'method': s['method'], 'service': s['service'], 'patterns': s['patterns'],
                              'expect': s['expect'], 'required': _required(s, t['steps']),
                              'device_types': s.get('device_types')} for s in t['steps']]
        tests.append(info)
    return {'device': device, 'ota_version': ota, 'days': days, 'config_keys': ini_keys,
            'drive_summary': drive_metrics.summarize(boot_details),
            'api_summary': api_calls.summarize(boot_details),
            'tests': tests, 'boots': boot_details}


def summarize_boots(device, boot_details, ini_keys):
    """What the OTA summary needs from one device: boot rows and per-TC/key counts."""
    tc_counts, tc_fail_boots, key_fails = defaultdict(Counter), defaultdict(list), Counter()
    boots_checked = 0
    for b in boot_details:
        if 'failed_keys' in b['config']:
            boots_checked += 1
            key_fails.update(tuple(ini_keys[i]) for i in b['config']['failed_keys'])
        for verdict, ids in b['verdicts'].items():
            for i in ids:
                key = _EVAL_TESTS[i]['key']
                tc_counts[key][verdict] += 1
                if verdict == 'FAIL':
                    tc_fail_boots[key].append([device, b['index'], b['label']])
    return {
        'boots': [{'index': b['index'], 'partial': b['partial'], 'start': b['start_fmt'], 'end': b['end_fmt'],
                   'window': b.get('window'),
                   'label': b['label'], 'udid': b['udid'], 'udid_source': b['udid_source'],
                   'duration_min': (b['drive'] or {}).get('duration_min'),
                   'config': {k: v for k, v in b['config'].items() if k in ('summary', 'error')},
                   'summary': b['summary']} for b in boot_details],
        'tc_counts': tc_counts,
        'tc_fail_boots': tc_fail_boots,
        'key_fails': key_fails,
        'config_boots_checked': boots_checked,
    }


# ── report ──────────────────────────────────────────────────────────────

def ensure_ini(ota, product=None):
    """(ini keys or None, error or None) for an OTA, downloading the ini if needed.

    Workers call this concurrently, so the download is serialized per OTA with a
    file lock, and the result is cached per process.
    """
    if ota not in _INI_CACHE:
        ini_path = Path(OTADownloader.INPUT_DIR) / ota / 'bagheera_config.ini'
        ini_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ini_path.parent / '.fetch.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            error = None
            if not ini_path.exists():
                try:
                    with timed(log, 'fetch_ini', ota=ota):
                        OTADownloader(ota, product).fetch_files(['bagheera_config.ini'])
                except (FileNotFoundError, ValueError) as e:
                    error = str(e)
            if ini_path.exists():
                _INI_CACHE[ota] = (parse_ini_keys(ini_path), None)
            else:
                _INI_CACHE[ota] = (None, error or f'{ini_path} not found')
            if _INI_CACHE[ota][1]:
                log.warning('%s: bagheera_config.ini unavailable: %s', ota, _INI_CACHE[ota][1])
    return _INI_CACHE[ota]


# ── features (after every device, see feature_status) ───────────────────

def update_key_reference(ota, device_summaries):
    """This run's CFG_PRSR key counts added to INPUT/<ota>/cfg_key_reference.json.

    Returns the merged counts. Only shares (key seen / service starts) are used, so a
    device counted again by a later run does not skew them much.
    """
    counts = {}
    for summary in device_summaries.values():
        for profile in summary['profiles'].values():
            feature_status.add_to_reference_counts(counts, profile)
    path = Path(OTADownloader.INPUT_DIR) / ota / 'cfg_key_reference.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / '.fetch.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stored = {}
        if path.exists():
            try:
                stored = json.loads(path.read_text())
            except ValueError:
                log.warning('%s unreadable, starting a new one', path)
        merged = feature_status.merge_reference_counts(stored, counts)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(merged, separators=(',', ':'), sort_keys=True))
        tmp.replace(path)
    return merged


def exclude_overridden_keys(boot, config_keys, overridden):
    """Drop overridden keys from a boot's "not mentioned" config keys: a key the boot
    overrides (feature_status.boot_overrides) or whose value the LLM read from its logs
    (override_values) gets no CFG_PRSR line, so it is not a coverage gap."""
    cfg = boot['config']
    if not cfg.get('failed_keys'):
        return
    by_llm = set(boot.get('override_values') or ())
    kept = [i for i in cfg['failed_keys']
            if config_keys[i][1] not in overridden and f'{config_keys[i][0]}.{config_keys[i][1]}' not in by_llm]
    n = len(cfg['failed_keys']) - len(kept)
    if n:
        cfg['failed_keys'] = kept
        cfg['summary']['FAIL'] -= n
        if not cfg['summary']['FAIL']:
            del cfg['summary']['FAIL']
        cfg['summary']['OVERRIDDEN'] = n


def complete_device_reports(ota, device_summaries, report_dir, features):
    """Features per boot for every device on the OTA, then the device pages.

    Adds 'features' ([ON, OFF, OVERRIDDEN, NA] boot counts per feature) to each
    device summary and returns the OTA's feature rollup.
    """
    ini_path = Path(OTADownloader.INPUT_DIR) / ota / 'bagheera_config.ini'
    defaults = feature_status.parse_ini_values(ini_path) if ini_path.exists() else None
    ref = feature_status.reference(update_key_reference(ota, device_summaries))
    feature_info = [{'name': f['name'],
                     'conditions': [[sec, key, exp, (defaults or {}).get(sec, {}).get(key)]
                                    for sec, key, exp in f['conditions']]} for f in features]
    totals = [Counter() for _ in features]
    devices_on = [0] * len(features)
    for device, summary in device_summaries.items():
        path = report_dir / ota / 'devices' / f'{device}.json'
        with open(path) as f:
            doc = json.load(f)
        per_feature = [Counter() for _ in features]
        latest_set = None
        key_fails = Counter()
        for b in doc['boots']:
            profile = summary['profiles'].get(b['index'])
            values = b.get('override_values')
            exclude_overridden_keys(b, doc['config_keys'], feature_status.boot_overrides(profile, ref) if profile else set())
            key_fails.update(tuple(doc['config_keys'][i]) for i in b['config'].get('failed_keys', ()))
            if defaults is None or not (profile or values):
                b['features'] = b['overridden_keys'] = None
                continue
            overridden = feature_status.boot_overrides(profile, ref) if profile else set()
            statuses = [status for status, _ in feature_status.evaluate(features, defaults, overridden, values)]
            b['features'] = [feature_status.STATUSES.index(st) for st in statuses]
            b['overridden_keys'] = sorted(overridden)
            latest_set = [features[i]['name'] for i, st in enumerate(statuses) if st == 'ON']
            for i, st in enumerate(statuses):
                per_feature[i][st] += 1
        doc['features'] = feature_info
        doc['features_error'] = None if defaults is not None else f'{ini_path.name} not available for this OTA'
        with open(path, 'w') as f:
            json.dump(doc, f, separators=(',', ':'))
        with open(path.with_suffix('.html'), 'w') as f:
            f.write(render_device_html(doc))
        summary['key_fails'] = key_fails
        configs = {b['index']: b['config'] for b in doc['boots']}
        for row in summary['boots']:
            if 'summary' in row['config']:
                row['config']['summary'] = configs[row['index']]['summary']
        summary['features'] = [dict(c) for c in per_feature]
        # The device's feature set: what was ON in its latest evaluated boot
        summary['feature_set'] = latest_set
        for i, c in enumerate(per_feature):
            totals[i].update(c)
            devices_on[i] += bool(c.get('ON'))
        summary.pop('profiles')
    return {
        'error': None if defaults is not None else f'{ini_path.name} not available for this OTA',
        'features': [dict(info, boots=dict(totals[i]), devices_on=devices_on[i])
                     for i, info in enumerate(feature_info)],
        'reference_services': len(ref),
    }


def report_summary(ota, device_summaries, given, device_info, features):
    """The report's top section: devices given / active / detailed logs, drives, feature sets.

    given: the devices the run was asked for; device_info: process_device info per device.
    """
    given = list(dict.fromkeys(given))
    no_logs = [d for d in given if not device_info.get(d, {}).get('days')]
    on_ota = sorted(device_summaries)
    other_ota = [d for d in given if d not in device_summaries and d not in no_logs]
    detailed = [d for d in on_ota if device_info.get(d, {}).get('detailed_days')]
    minutes = {d: device_summaries[d]['drive']['drive_minutes_total'] for d in on_ota}
    boots = {d: len(device_summaries[d]['boots']) for d in on_ota}
    estimated = {d for d in on_ota if device_summaries[d]['drive']['drive_minutes_estimated']}
    low_power = [d for d in on_ota if device_summaries[d]['drive']['low_power_boots']]
    active_all = [d for d in given if d not in no_logs]
    api_by_device = {d: {int(api): st['calls'] for api, st in (device_summaries[d].get('api') or {}).get('apis', {}).items()}
                     for d in on_ota}

    sets = Counter()
    with_set = [d for d in on_ota if device_summaries[d].get('feature_set') is not None]
    for d in with_set:
        sets[tuple(device_summaries[d]['feature_set'])] += 1
    all_names = [f['name'] for f in features]
    coverage = [{'name': name, 'devices_on': sum(1 for d in with_set if name in device_summaries[d]['feature_set'])}
                for name in all_names]
    return {
        'devices_given': len(given),
        'devices_without_logs': no_logs,
        'devices_active': len(given) - len(no_logs),
        'devices_on_ota': len(on_ota),
        'devices_other_ota_only': other_ota,
        'devices_detailed_logs': detailed,
        'devices_critical_only': [d for d in on_ota if d not in detailed],
        'drive_minutes_total': sum(minutes.values()),
        'drive_minutes_by_device': minutes,
        'boots_by_device': boots,
        'devices_estimated_minutes': sorted(estimated),
        'devices_under_6h': sorted(d for d, m in minutes.items() if m <= 6 * 60),
        'devices_over_6h': sorted(d for d, m in minutes.items() if m > 6 * 60),
        'logs_detailed': [d for d in active_all if device_info.get(d, {}).get('detailed_days')],
        'logs_critical_only': [d for d in active_all if not device_info.get(d, {}).get('detailed_days')],
        'api_calls_by_device': api_by_device,
        'devices_over_24h': sorted(d for d, m in minutes.items() if m > 24 * 60),
        'devices_low_power': low_power,
        'feature_devices': len(with_set),
        'feature_coverage': coverage,
        'feature_sets': [{'features': list(names), 'devices': n,
                          'missing': [x for x in all_names if x not in names]}
                         for names, n in sets.most_common()],
    }


def build_ota_summary(ota, device_summaries, tests):
    """OTA-level rollup over every device boot on this OTA (device_summaries from process_device)."""
    first = next(iter(device_summaries.values()))
    report = {
        'ota_version': ota,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        'config_error': first['config_error'],
        'config_keys': first['config_keys'],
        'devices': {},
        'config_failures': [],
        'features': {},
        'no_log_checks': [],
    }
    report['drive'] = drive_metrics.merge_summaries([s['drive'] for s in device_summaries.values()])
    report['api_calls'] = api_calls.merge_summaries([s['api'] for s in device_summaries.values()])
    key_fails = Counter()
    tc_counts = defaultdict(Counter)
    tc_fail_boots = defaultdict(list)
    boots_checked = 0
    for device, summary in sorted(device_summaries.items()):
        report['devices'][device] = summary['boots']
        boots_checked += summary['config_boots_checked']
        key_fails.update(summary['key_fails'])
        for key, counts in summary['tc_counts'].items():
            tc_counts[key].update(counts)
        for key, refs in summary['tc_fail_boots'].items():
            tc_fail_boots[key].extend(refs)
    report['config_boots_checked'] = boots_checked
    report['config_failures'] = [{'section': s, 'key': k, 'failed_boots': n}
                                 for (s, k), n in key_fails.most_common()]

    features = defaultdict(list)
    for t in tests:
        if not t['steps']:
            report['no_log_checks'].append({'tc_id': t['tc_id'], 'feature': t['feature'], 'name': t['name']})
            continue
        features[t['feature']].append({
            'tc_id': t['tc_id'], 'name': t['name'], 'functionality': t['functionality'],
            'unresolved_steps': len(t['unresolved']),
            'boots': dict(tc_counts[t['key']]),
            'failed_in': tc_fail_boots[t['key']],
        })
    for feature, tcs in sorted(features.items()):
        summary = Counter()
        for tc in tcs:
            summary.update(tc['boots'])
        report['features'][feature] = {'summary': dict(summary), 'test_cases': tcs}
    return report


def _esc(text):
    return html.escape(str(text)) if text is not None else ''


def _chip(status):
    return f'<span class="chip {_esc(status).lower()}">{_esc(status)}</span>'


def _summary_chips(summary, order=SHOWN_VERDICTS):
    return ' '.join(f'{_chip(k)} {summary[k]}' for k in order if summary.get(k))


def boot_name(b):
    """UDID 323, or UDID Unknown when no time_sync udid_node line gave it (the service_mon
    inference is not exact, so it is not shown)."""
    if b.get('udid') is None or b.get('udid_source') == 'service_mon':
        return 'UDID Unknown'
    return f'UDID {b["udid"]}'


def _boot_label(b):
    name = b.get('label') or boot_name(b)
    w = b.get('window') or {}
    if w:
        start = (f'started before the logs ({w["start_fmt"]})' if b['partial'] and w.get('start')
                 else w.get('start_fmt') or 'before the logs')
        end = w.get('end_fmt') or 'end of logs'
        if not b['partial'] and b.get('end') is None and w.get('end'):
            end += ' (last log line)'
        return f'{name} · {start} → {end}'
    if b['partial']:
        return f'{name} · partial, until {b["end"] or "end of logs"}'
    return f'{name} · {b["start"]} → {b["end"] or "end of logs"}'


def _fmt_minutes(ms):
    if ms is None:
        return ''
    minutes = ms / 60000
    return f'{minutes:.0f} min' if minutes < 120 else f'{minutes / 60:.1f} h'


def _drive_overview(d):
    """KPI tiles + shutdown reasons from a drive_metrics.summarize()/merge_summaries() dict."""
    if not d:
        return ''
    na = lambda v: '—' if v is None else v
    tiles = [
        ('Drives (UDID)', d['drives']), ('Drive time', _fmt_minutes(d['drive_minutes'] * 60000) or '0 min'),
        ('Drives < 6 min', d['drives_short']), ('Drives 6–15 min', d['drives_medium']),
        ('Drives with low power wakeup', d['drives_with_low_power_wakeup']),
        ('Max low power wakeup', na(d['max_low_power_wakeup'])),
        ('Sessions created / available', f'{d["sessions_created"]} / {d["sessions_available"]}'),
        ('Drives with fewer sessions than minutes', d['session_issues']),
        ('Repeated UDIDs', d['repeated_udids']),
        ('Power monitor starts', d['service_starts']), ('Crank-ons', d['crank_ons']),
        ('Runs < 350 s', d['runs_short']), ('Runs 7–16 min (improper shutdown)', d['runs_improper']),
    ]
    out = ['<div class="tiles">' + ''.join(_tile(v, k) for k, v in tiles) + '</div>']
    if d['previous_shutdown_reasons']:
        out.append('<details><summary>Previous shutdown reasons</summary><div class="body"><table>'
                   '<tr><th>Reason</th><th class="num">Boots</th></tr>')
        out += [f'<tr><td><code>{_esc(r)}</code></td><td class="num">{n}</td></tr>'
                for r, n in d['previous_shutdown_reasons'].items()]
        out.append('</table></div></details>')
    return '\n'.join(out)


def _condition_text(cond):
    section, key, expected, default = cond
    exp = json.dumps(expected) if not isinstance(expected, str) else expected
    dflt = 'not in default config' if default is None else f'default {default}'
    return f'<code>{_esc(section)}.{_esc(key)} = {_esc(exp)}</code> <span class="muted">({_esc(dflt)})</span>'


API_NOTE = ('Calls found by the line each service logs when it makes the request (lib/api_calls.py). '
            'Gaps are the time between consecutive calls of an API inside one boot. Devices that upload '
            'critical logs only show few of them (otacheck and power_mon calls).')


def _fmt_gap(ms):
    if ms is None:
        return '—'
    seconds = ms / 1000
    if seconds < 120:
        return f'{seconds:.0f} s'
    return f'{seconds / 60:.1f} min' if seconds < 7200 else f'{seconds / 3600:.1f} h'


def _api_section(summary, devices=None):
    """API calls table from api_calls.summarize() (device) or merge_summaries() (OTA, devices given)."""
    if not summary:
        return ''
    boots, apis = summary['boots'], summary['apis']
    head = ['ID', 'API', 'Type', '#Calls'] + (['#Devices'] if devices else []) + [
        '#Boots with calls', '#Calls / boot', '#Min gap', '#Avg gap', '#Max gap']
    rows = []
    for api in api_calls.WITH_PATTERN:
        name, kind = api_calls.APIS[api]
        st = apis.get(str(api))
        if not st:
            rows.append(f'<tr class="muted"><td>{api}</td><td>{_esc(name)}</td><td>{_esc(kind)}</td>'
                       f'<td class="num">0</td>{"<td></td>" if devices else ""}<td></td><td></td><td></td><td></td><td></td></tr>')
            continue
        avg = st['gap_sum'] / st['gaps'] if st['gaps'] else None
        dev = f'<td class="num">{st["devices"]} / {devices}</td>' if devices else ''
        rows.append(f'<tr><td>{api}</td><td>{_esc(name)}</td><td>{_esc(kind)}</td><td class="num">{st["calls"]}</td>{dev}'
                   f'<td class="num">{st["boots"]} / {boots}</td><td class="num">{st["calls"] / boots:.1f}</td>'
                   f'<td class="num">{_fmt_gap(st["gap_min"])}</td><td class="num">{_fmt_gap(avg)}</td>'
                   f'<td class="num">{_fmt_gap(st["gap_max"])}</td></tr>')
    out = [f'<p class="muted">{_esc(API_NOTE)}</p>', _table(head, rows, limit=len(rows))]
    missing = [f'{api} {api_calls.APIS[api][0]}' for api in api_calls.APIS if api not in api_calls.WITH_PATTERN]
    out.append(f'<p class="muted">No call line found in the device logs for: {_esc(", ".join(missing))}</p>')
    return '\n'.join(out)


def _features_table(features, counts_of, extra_header=(), extra_cell=lambda i: ''):
    """Feature rows sorted ON first; counts_of(i) -> {status: boots}; extra_header: column names for extra_cell."""
    order = sorted(range(len(features)), key=lambda i: (-counts_of(i).get('ON', 0), -counts_of(i).get('OVERRIDDEN', 0),
                                                       features[i]['name'].lower()))
    rows = []
    for i in order:
        f = features[i]
        needs = '<br>'.join(_condition_text(c) for c in f['conditions'][:4])
        if len(f['conditions']) > 4:
            needs = (f'<details><summary class="muted">{len(f["conditions"])} keys</summary><div class="body">'
                     + '<br>'.join(_condition_text(c) for c in f['conditions']) + '</div></details>')
        rows.append(f'<tr><td>{_esc(f["name"])}</td><td>{_summary_chips(counts_of(i), feature_status.STATUSES)}</td>'
                    f'{extra_cell(i)}<td class="wrap">{needs}</td></tr>')
    return _table(['Feature', 'Boots', *extra_header, 'Needs'], rows, limit=15)


def _device_list(title, devices, limit=200):
    if not devices:
        return ''
    shown = ', '.join(f'<a href="devices/{_esc(d)}.html">{_esc(d)}</a>' for d in devices[:limit])
    more = f' <span class="muted">+{len(devices) - limit} more</span>' if len(devices) > limit else ''
    return f'<details><summary>{_esc(title)} ({len(devices)})</summary><div class="body">{shown}{more}</div></details>'


def _plain_list(title, devices):
    if not devices:
        return ''
    return (f'<details><summary>{_esc(title)} ({len(devices)})</summary><div class="body">'
            f'{_esc(", ".join(devices))}</div></details>')


def _pct(n, total):
    return f'{100 * n / total:.0f}%' if total else '—'


FEATURES_NOTE = ('ON / OFF from the value each needed key had in that boot: the value the service logged at '
                 'startup when found (read by the LLM), else the OTA default (bagheera_config.ini) for keys CFG_PRSR '
                 'shows were not overridden. OVERRIDDEN: a key the feature needs was overridden in that boot; the value is not '
                 'in the logs. NA: a key it needs is not in this OTA\'s default config.')


DEVICE_STYLE = """
.muted{color:var(--muted)}p.muted{font-size:12.5px;margin:0 0 12px}
section h3{font-size:13.5px;margin:18px 0 8px}
details{border:1px solid var(--line);border-radius:10px;margin:10px 0;background:var(--card)}
details>summary{cursor:pointer;padding:10px 14px;font-weight:600}
details details{border-radius:8px}details details>summary{font-weight:500;padding:6px 10px}
.body{padding:0 14px 12px;overflow-x:auto}
.body table th{position:static;cursor:default}.body table td{white-space:normal;vertical-align:top}
.body table{border:1px solid var(--line)}
td details{margin:0;border:0;background:none}td details>summary{padding:0;font-weight:400}td .body{padding:4px 0 0}
code,.line{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:var(--head);
border-radius:4px;padding:1px 4px;word-break:break-all}
.chip{display:inline-block;border-radius:10px;padding:0 8px;font-size:11px;font-weight:600;white-space:nowrap}
.chip.pass,.chip.on{color:var(--pass);background:var(--pass-bg)}
.chip.fail,.chip.out_of_order,.chip.not_found,.chip.no_log_file{color:var(--fail);background:var(--fail-bg)}
.chip.not_triggered,.chip.overridden{color:var(--nt);background:var(--nt-bg)}
.chip.no_log_checks,[class*="chip info_"],.chip.na,.chip.not_for_device,.chip.off{color:var(--muted);background:var(--none-bg)}
.side a.back{color:var(--accent);margin-bottom:12px}
"""


def _shell(title, brand_small, brand, nav, body, js, style=''):
    """Sidebar + content layout shared by the OTA report and the device pages."""
    return (f'<!doctype html><html lang="en"><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_esc(title)}</title>'
            f'<style>{REPORT_STYLE}{style}</style><div class="layout"><nav class="side"><div class="brand">'
            f'<small>{_esc(brand_small)}</small><b>{_esc(brand)}</b></div>{nav}</nav>'
            f'<main class="content">{body}</main></div>{js}</html>')


def _config_cell(cfg):
    if 'error' in cfg:
        return f'<span class="muted">{_esc(cfg["error"])}</span>'
    return _summary_chips(cfg['summary'], ['FAIL', 'PASS'])


REPORT_STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171c;--muted:#6b7280;--line:#e3e6eb;--head:#f1f3f6;--hover:#f7f9fc;
--accent:#2f6fde;--accent-bg:#e8f0fd;--pass:#17864a;--pass-bg:#e3f6ea;--fail:#d03b3b;--fail-bg:#fde9e9;
--nt:#a46a00;--nt-bg:#fdf3dc;--none:#9aa3af;--none-bg:#eef0f3}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#0e1116;--card:#161b22;
--fg:#e6edf3;--muted:#8b949e;--line:#2a313b;--head:#1b212a;--hover:#1c232c;--accent:#5b9bff;--accent-bg:#16253d;
--pass:#3fb970;--pass-bg:#11291b;--fail:#f26d6d;--fail-bg:#311616;--nt:#e0a42e;--nt-bg:#2e2410;--none:#6e7681;--none-bg:#21262d}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#0e1116;--card:#161b22;--fg:#e6edf3;--muted:#8b949e;--line:#2a313b;
--head:#1b212a;--hover:#1c232c;--accent:#5b9bff;--accent-bg:#16253d;--pass:#3fb970;--pass-bg:#11291b;--fail:#f26d6d;
--fail-bg:#311616;--nt:#e0a42e;--nt-bg:#2e2410;--none:#6e7681;--none-bg:#21262d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.layout{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:100vh;background:linear-gradient(to right,var(--card) 229px,var(--line) 229px 230px,transparent 230px)}
.side{position:sticky;top:0;height:100vh;padding:28px 14px;background:var(--card);overflow-y:auto}
.brand{padding:0 10px 22px}.brand small{display:block;color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.brand b{display:block;font-size:19px;margin-top:2px;word-break:break-all}
.side a{display:block;padding:8px 10px;margin:2px 0;border-radius:8px;color:var(--muted);font-weight:500}
.side a:hover{background:var(--hover);color:var(--fg);text-decoration:none}
.side a.active{background:var(--accent-bg);color:var(--accent)}
.content{padding:28px 32px 80px;width:100%}
.top{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px}
.top h1{font-size:26px;margin:0;letter-spacing:-.01em}.top span{color:var(--muted);font-size:13px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-bottom:20px;scroll-margin-top:20px}
section h2{font-size:16px;margin:0 0 16px;letter-spacing:-.005em}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.tile{border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:var(--card)}
.tile b{display:block;font-size:26px;line-height:1.15;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.tile span{color:var(--muted);font-size:12.5px}.tile em{float:right;font-style:normal;color:var(--muted);font-size:12px}
.tile.pick{cursor:pointer;transition:border-color .15s,background .15s}
.tile.pick:hover{border-color:var(--accent);background:var(--accent-bg)}
dialog.devs{width:min(560px,calc(100vw - 32px));max-height:min(640px,calc(100vh - 64px));padding:0;border:1px solid var(--line);
border-radius:14px;background:var(--card);color:var(--fg);box-shadow:0 12px 40px rgba(0,0,0,.35)}
dialog.devs[open]{display:flex;flex-direction:column}dialog.devs::backdrop{background:rgba(0,0,0,.5)}
.devs header{display:flex;align-items:center;gap:10px;padding:16px 20px;border-bottom:1px solid var(--line)}
.devs header h3{margin:0;font-size:15px;flex:1}.devs header span{color:var(--muted);font-size:13px}
.devs button{border:1px solid var(--line);background:var(--head);color:var(--fg);border-radius:8px;padding:6px 12px;
font:inherit;font-size:13px;cursor:pointer}.devs button:hover{border-color:var(--accent)}
.devs button.copy{background:var(--accent);border-color:var(--accent);color:#fff}
.devs ul{list-style:none;margin:0;padding:8px 20px 16px;overflow-y:auto;columns:3 140px;column-gap:16px;
font-variant-numeric:tabular-nums;font-size:13px;line-height:2}
.devs li{break-inside:avoid}.devs p{margin:0;padding:16px 20px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.c-pass{background:var(--pass)}.c-fail{background:var(--fail)}.c-nt{background:var(--nt)}.c-none{background:var(--none)}.c-acc{background:var(--accent)}
.tbl{border:1px solid var(--line);border-radius:10px;overflow:auto;margin-top:16px}
.tbl.clip{max-height:430px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--line);padding:8px 12px;text-align:left;white-space:nowrap}
tr>:first-child{border-left:0}tr>:last-child{border-right:0}thead tr:first-child th{border-top:0}tbody tr:last-child td{border-bottom:0}
th{position:sticky;top:0;background:var(--head);font-weight:600;font-size:12px;color:var(--muted);cursor:pointer;user-select:none;z-index:1}
th:hover{color:var(--fg)}th.asc::after{content:" ▲";font-size:9px}th.desc::after{content:" ▼";font-size:9px}
tbody tr:hover td{background:var(--hover)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.wrap{white-space:normal;min-width:320px;color:var(--muted);font-size:12.5px}
td.zero{color:var(--none)}
tfoot td{font-weight:600;background:var(--head);position:sticky;bottom:0}
.more{display:block;width:100%;border:0;border-top:1px solid var(--line);background:var(--head);color:var(--accent);
padding:8px;font:inherit;font-size:13px;cursor:pointer;position:sticky;bottom:0}
.n-pass{color:var(--pass);font-weight:600}.n-fail{color:var(--fail);font-weight:600}.n-nt{color:var(--nt)}
.mini{display:flex;height:6px;width:120px;border-radius:4px;overflow:hidden;background:var(--none-bg)}
tr.feat{cursor:pointer}tr.feat td:first-child::before{content:"▸";display:inline-block;width:14px;color:var(--muted);transition:transform .15s}
tr.feat.open td:first-child::before{transform:rotate(90deg)}
tr.tcs>td{padding:0;background:var(--bg)}tr.tcs table{font-size:12.5px}tr.tcs th{position:static}
tr.tcs td,tr.tcs th{padding:6px 12px}tr.tcs td.wrap{min-width:260px}
.filter{float:right;font-size:12px;color:var(--muted);font-weight:400}.filter a{margin-left:8px;cursor:pointer}
@media (max-width:820px){.layout{display:block}.side{position:sticky;height:auto;display:flex;gap:4px;overflow-x:auto;
padding:8px 16px;border-bottom:1px solid var(--line);z-index:5}.brand{display:none}.side a{white-space:nowrap;margin:0}
.content{padding:16px}section{padding:16px}}
"""

REPORT_JS = r"""
// Sortable tables: click a header
document.querySelectorAll('table.sort').forEach(t => {
  t.querySelectorAll('thead th').forEach((th, col) => th.addEventListener('click', () => {
    const body = t.tBodies[0], dir = th.classList.contains('asc') ? -1 : 1;
    t.querySelectorAll('thead th').forEach(h => h.classList.remove('asc', 'desc'));
    th.classList.add(dir === 1 ? 'asc' : 'desc');
    const val = r => { const c = r.cells[col]; const v = c.dataset.v ?? c.textContent.trim();
                       return v !== '' && !isNaN(v) ? +v : v.toLowerCase(); };
    const groups = [...body.rows].filter(r => !r.classList.contains('tcs'))
      .map(r => [r, r.nextElementSibling && r.nextElementSibling.classList.contains('tcs') ? r.nextElementSibling : null]);
    groups.sort((a, b) => { const x = val(a[0]), y = val(b[0]); return (x > y ? 1 : x < y ? -1 : 0) * dir; });
    groups.forEach(([r, d]) => { body.appendChild(r); if (d) body.appendChild(d); });
  }));
});
// Long tables: show the first rows, expand on demand
document.querySelectorAll('.tbl[data-limit]').forEach(box => {
  const limit = +box.dataset.limit, rows = [...box.querySelectorAll(':scope > table > tbody > tr:not(.tcs)')];
  if (rows.length <= limit) return;
  const btn = document.createElement('button'); btn.className = 'more';
  const set = open => {
    box.classList.toggle('clip', open);
    rows.forEach((r, i) => {
      r.hidden = !open && i >= limit;
      const d = r.nextElementSibling;  // a feature's test cases stay closed with it
      if (d && d.classList.contains('tcs') && r.hidden) { d.hidden = true; r.classList.remove('open'); }
    });
    btn.textContent = open ? 'Show less' : `Show all ${rows.length}`;
  };
  btn.addEventListener('click', () => set(btn.textContent !== 'Show less'));
  box.appendChild(btn); set(false);
});
// Feature rows expand into their test cases
document.querySelectorAll('tr.feat').forEach(r => r.addEventListener('click', e => {
  if (e.target.closest('a')) return;
  r.classList.toggle('open'); r.nextElementSibling.hidden = !r.classList.contains('open');
}));
// Device-count tiles open a dialog listing their devices, with copy to clipboard
const paged = new Set(JSON.parse(document.getElementById('paged')?.textContent || '[]'));
const dlg = document.createElement('dialog'); dlg.className = 'devs';
dlg.innerHTML = '<header><h3></h3><span></span><button class="copy">Copy</button><button class="close">Close</button></header><ul></ul>';
document.body.appendChild(dlg);
let shown = [];
dlg.querySelector('.close').addEventListener('click', () => dlg.close());
dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });  // click on the backdrop
dlg.querySelector('.copy').addEventListener('click', async e => {
  const text = shown.join('\n'), btn = e.currentTarget;
  try { await navigator.clipboard.writeText(text); }
  catch { const t = document.createElement('textarea'); t.value = text; dlg.appendChild(t); t.select();
          document.execCommand('copy'); t.remove(); }
  btn.textContent = 'Copied'; setTimeout(() => btn.textContent = 'Copy', 1500);
});
document.querySelectorAll('.tile.pick').forEach(tile => tile.addEventListener('click', () => {
  shown = tile.dataset.devices.split(' ').filter(Boolean);
  dlg.querySelector('h3').textContent = tile.dataset.title;
  dlg.querySelector('header span').textContent = `${shown.length} device${shown.length === 1 ? '' : 's'}`;
  const link = d => paged.has(d) ? `<a href="devices/${d}.html">${d}</a>` : d;
  dlg.querySelector('ul').innerHTML = shown.map(d => `<li>${link(d)}</li>`).join('');
  dlg.showModal();
}));
// Left navigation follows the scroll
const links = [...document.querySelectorAll('.side a')];
const spy = new IntersectionObserver(es => es.forEach(e => {
  if (e.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + e.target.id));
}), { rootMargin: '-10% 0px -80% 0px' });
document.querySelectorAll('section[id]').forEach(s => spy.observe(s));
"""

NAV = [('devices', 'Device Summary'), ('logs', 'Log Upload Summary'), ('drive', 'Drive Summary'),
       ('feature-sets', 'Feature Set Summary'), ('api', 'API Call Summary'), ('features', 'Feature Level Summary')]


def _n(v):
    return f'{v:,}' if isinstance(v, (int, float)) else _esc(v)


def _tile(value, label, pct=None, dot=None, devices=None):
    """A KPI tile; given devices (non-empty), clicking it opens a dialog listing them."""
    dot_html = f'<i class="dot {dot}"></i>' if dot else ''
    pct_html = f'<em>{_esc(pct)}</em>' if pct else ''
    attrs = (f' class="tile pick" data-title="{_esc(label)}" data-devices="{_esc(" ".join(devices))}"' if devices
             else ' class="tile"')
    return f'<div{attrs}>{pct_html}<b>{_n(value)}</b><span>{dot_html}{_esc(label)}</span></div>'


def _table(head, rows, limit=12, foot='', box_id=''):
    """Bordered, sortable table; more than `limit` rows collapse behind "Show all"."""
    ths = ''.join(f'<th class="num">{_esc(h[1:])}</th>' if h.startswith('#') else f'<th>{_esc(h)}</th>' for h in head)
    ident = f' id="{box_id}"' if box_id else ''
    return (f'<div class="tbl"{ident} data-limit="{limit}"><table class="sort"><thead><tr>{ths}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>{foot}</table></div>')


def _dev_link(d):
    return f'<a href="devices/{_esc(d)}.html">{_esc(d)}</a>'


def _devices_section(sm):
    given, inactive = sm['devices_given'], sm['devices_without_logs']
    active = sm['logs_detailed'] + sm['logs_critical_only']
    return ('<section id="devices"><h2>Device Summary</h2><div class="tiles">'
            + _tile(given, 'Devices', devices=active + inactive)
            + _tile(sm['devices_active'], 'Active', _pct(sm['devices_active'], given), 'c-pass', devices=active)
            + _tile(len(inactive), 'Inactive', _pct(len(inactive), given), 'c-none', devices=inactive)
            + '</div></section>')


def _logs_section(sm):
    given = sm['devices_given']
    detailed, critical, none = sm['logs_detailed'], sm['logs_critical_only'], sm['devices_without_logs']
    return ('<section id="logs"><h2>Log Upload Summary</h2><div class="tiles">'
            + _tile(len(detailed), 'Detailed logs uploaded', _pct(len(detailed), given), 'c-acc', devices=detailed)
            + _tile(len(critical), 'Critical logs uploaded', _pct(len(critical), given), 'c-nt', devices=critical)
            + _tile(len(none), 'No logs uploaded', _pct(len(none), given), 'c-none', devices=none)
            + '</div></section>')


def _drive_section(sm):
    minutes, boots = sm['drive_minutes_by_device'], sm.get('boots_by_device', {})
    low_power, total = sm['devices_low_power'], sm['drive_minutes_total']
    by_time = [d for d, _ in sorted(minutes.items(), key=lambda kv: -kv[1])]
    rows = [f'<tr><td>{_dev_link(d)}</td><td class="num{" zero" if not minutes[d] else ""}">{minutes[d]:,}</td>'
            f'<td class="num" data-v="{minutes[d] / 60:.2f}">{minutes[d] / 60:,.1f}</td>'
            f'<td class="num">{boots.get(d, "")}</td></tr>' for d in by_time]
    return ('<section id="drive"><h2>Drive Summary</h2><div class="tiles">'
            + _tile(sm['devices_on_ota'], 'Active devices', devices=by_time)
            + _tile(f'{total / 60:,.1f} h', 'Total drive time')
            + _tile(len(sm['devices_under_6h']), 'Running < 6 h', devices=sm['devices_under_6h'])
            + _tile(len(sm['devices_over_6h']), 'Running > 6 h', devices=sm['devices_over_6h'])
            + _tile(len(sm['devices_over_24h']), 'Running > 24 h', devices=sm['devices_over_24h'])
            + _tile(len(low_power), 'Low power mode', devices=low_power)
            + '</div>'
            + _table(['Device', '#Drive min', '#Hours', '#Boots on this OTA'], rows)
            + '</section>')


def _feature_sets_section(sm):
    coverage = sm.get('feature_coverage') or []
    enabled = sum(1 for f in coverage if f['devices_on'])
    return ('<section id="feature-sets"><h2>Feature Set Summary</h2><div class="tiles">'
            + _tile(len(coverage), 'Total features')
            + _tile(enabled, 'Enabled', _pct(enabled, len(coverage)), 'c-pass')
            + _tile(len(coverage) - enabled, 'Disabled', _pct(len(coverage) - enabled, len(coverage)), 'c-none')
            + '</div></section>')


def _api_calls_section(sm):
    by_device, minutes = sm.get('api_calls_by_device') or {}, sm['drive_minutes_by_device']
    apis = [a for a in api_calls.WITH_PATTERN if any(c.get(a) for c in by_device.values())]
    rows, totals = [], Counter()
    for d in sorted(by_device, key=lambda d: -minutes.get(d, 0)):
        calls = by_device[d]
        totals.update(calls)
        cells = ''.join(f'<td class="num{"" if calls.get(a) else " zero"}">{calls.get(a, 0):,}</td>' for a in apis)
        rows.append(f'<tr><td>{_dev_link(d)}</td><td class="num">{minutes.get(d, 0):,}</td>{cells}</tr>')
    foot = ('<tfoot><tr><td>Total</td><td class="num">' + f'{sum(minutes.get(d, 0) for d in by_device):,}</td>'
            + ''.join(f'<td class="num">{totals.get(a, 0):,}</td>' for a in apis) + '</tr></tfoot>')
    head = ['Device ID', '#Total drive min'] + [f'#{api_calls.APIS[a][0]}' for a in apis]
    return f'<section id="api"><h2>API Call Summary</h2>{_table(head, rows, foot=foot)}</section>'


def _feature_level_section(report):
    rows = []
    for feature, feat in report['features'].items():
        tcs = feat['test_cases']
        triggered = [tc for tc in tcs if tc['boots'].get('PASS') or tc['boots'].get('FAIL')]
        fail = [tc for tc in triggered if tc['boots'].get('FAIL')]
        npass, nfail, nt = len(triggered) - len(fail), len(fail), len(tcs) - len(triggered)
        bar = (f'<div class="mini">' + ''.join(f'<i class="{c}" style="width:{100 * n / (len(tcs) or 1):.1f}%"></i>'
                                              for n, c in ((npass, 'c-pass'), (nfail, 'c-fail'), (nt, 'c-nt')) if n)
               + '</div>')
        rows.append(f'<tr class="feat"><td>{_esc(feature)}</td><td class="num">{len(tcs)}</td>'
                    f'<td class="num">{len(triggered)}</td><td class="num{" n-pass" if npass else " zero"}">{npass}</td>'
                    f'<td class="num{" n-fail" if nfail else " zero"}">{nfail}</td><td class="num{" n-nt" if nt else " zero"}">{nt}</td>'
                    f'<td data-v="{npass / (len(tcs) or 1):.3f}">{bar}</td></tr>')
        detail = []
        for tc in sorted(tcs, key=lambda tc: (-tc['boots'].get('FAIL', 0), -tc['boots'].get('PASS', 0), str(tc['tc_id']))):
            b = tc['boots']
            failed = ' '.join(f'{_dev_link(dev)}<span class="n-fail"> ×{n}</span>'
                              for dev, n in Counter(ref[0] for ref in tc['failed_in']).items())
            detail.append(f'<tr><td>{_esc(tc["tc_id"])}</td><td class="wrap">{_esc(tc["functionality"])}</td>'
                          f'<td class="num n-pass">{b.get("PASS", 0) or ""}</td>'
                          f'<td class="num n-fail">{b.get("FAIL", 0) or ""}</td><td>{failed}</td></tr>')
        rows.append('<tr class="tcs" hidden><td colspan="7"><table><thead><tr><th>TC</th><th>Functionality</th>'
                    '<th class="num">Pass boots</th><th class="num">Fail boots</th><th>Failed on</th></tr></thead>'
                    f'<tbody>{"".join(detail)}</tbody></table></td></tr>')
    head = ['Feature Name', '#Total Testcases', '#Total Triggered', '#Pass', '#Fail', '#Not Triggered', '']
    return f'<section id="features"><h2>Feature Level Summary</h2>{_table(head, rows, limit=15)}</section>'


def render_html(report):
    ota, sm = report['ota_version'], report.get('summary') or {}
    nav = ''.join(f'<a href="#{i}">{_esc(t)}</a>' for i, t in NAV)
    body = [f'<div class="top"><h1>{_esc(ota)}</h1><span>{_esc(report["generated_at"])}</span></div>']
    if sm:
        body += [_devices_section(sm), _logs_section(sm), _drive_section(sm), _feature_sets_section(sm),
                 _api_calls_section(sm)]
    body.append(_feature_level_section(report))
    paged = json.dumps(sorted(sm.get('drive_minutes_by_device', {})))
    return _shell(f'OTA {ota}', 'OTA report', ota, nav, ''.join(body),
                  f'<script type="application/json" id="paged">{paged}</script><script>{REPORT_JS}</script>')


DEVICE_JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ESC[c]);
const chip = s => `<span class="chip ${esc(s).toLowerCase()}">${esc(s)}</span>`;
const fmt = ms => ms == null ? '' : new Date(ms).toISOString().replace('T', ' ').replace('Z', '');
const byTc = (a, b) => (D.tests[a].feature || '').localeCompare(D.tests[b].feature || '')
  || String(D.tests[a].tc_id).localeCompare(String(D.tests[b].tc_id));

function stepsTable(t, f) {
  const out = ['<table><tr><th>Step</th><th>Service</th><th>Expect</th><th>Pattern</th>'
    + '<th>Status</th><th>Matched line</th></tr>'];
  t.steps.forEach((s, j) => {
    const [status, ts, line] = f.steps[j];
    const expect = s.expect + (s.required || s.expect === 'absent' ? '' : ' (optional)')
      + (s.device_types ? ` · only ${s.device_types.join('/')}` : '');
    out.push(`<tr><td>${esc(s.method)}</td><td>${esc(s.service)}</td><td>${esc(expect)}</td>`
      + `<td>${s.patterns.map(p => `<code>${esc(p)}</code>`).join('<br>')}</td><td>${chip(status)}</td>`
      + `<td>${esc(fmt(ts))}${line ? `<br><span class="line">${esc(line)}</span>` : ''}</td></tr>`);
  });
  out.push('</table>');
  return out.join('');
}

const STATUSES = ['ON', 'OFF', 'OVERRIDDEN', 'NA'];

function renderDrive(b) {
  const d = b.drive, p = b.power || {};
  const rows = [];
  if (b.device_type) rows.push(['Device type', `${b.device_type} (from OTA ${b.ota}); test cases for other device types are skipped`]);
  rows.push(['UDID', b.udid != null && b.udid_source !== 'service_mon' ? `${b.udid}` : 'Unknown']);
  if (d) {
    rows.push(['Drive', `${fmt(d.drive_start)} → ${fmt(d.drive_end)} · ${d.duration_min} min`]);
    if (d.rtc_jump_from !== d.drive_end) rows.push(['RTC jump', `${fmt(d.rtc_jump_from)} → ${fmt(d.rtc_jump_to)}`]);
    rows.push(['Low power wakeup', d.low_power_wakeup]);
    rows.push(['Sessions created / available', `${d.sessions_created} / ${d.sessions_available}`]);
    rows.push(['Power monitor start / stop', `${fmt(d.power_mon_start) || '—'} / ${fmt(d.power_mon_stop) || '—'}`]);
    rows.push(['Crank low', fmt(d.crank_low_at) || '—']);
  } else {
    rows.push(['Drive', 'No time_sync udid_node line for this boot (critical logs only, or the next boot is not in the range)']);
  }
  rows.push(['Power monitor starts / crank-ons', `${p.service_starts ?? 0} / ${p.crank_ons ?? 0}`]);
  const reasons = Object.keys(p.previous_shutdown_reasons || {});
  if (reasons.length) rows.push(['Previous shutdown reason', reasons.join(', ')]);
  return '<h3>Drive</h3><table>' + rows.map(([k, v]) => `<tr><th style="width:240px">${esc(k)}</th><td>${esc(v)}</td></tr>`).join('') + '</table>';
}

function renderFeatures(b) {
  if (!D.features || !D.features.length) return '';
  if (!b.features) return '<h3>Features</h3><p class="muted">No CFG_PRSR startup lines in this boot, features not evaluated</p>';
  const groups = {ON: [], OVERRIDDEN: [], OFF: [], NA: []};
  b.features.forEach((st, i) => groups[STATUSES[st]].push(D.features[i].name));
  const out = ['<h3>Features</h3>'];
  for (const st of ['ON', 'OVERRIDDEN', 'OFF', 'NA']) {
    if (groups[st].length) out.push(`<p>${chip(st)} ${groups[st].map(esc).join(', ')}</p>`);
  }
  const values = Object.entries(b.override_values || {});
  if (values.length) {
    out.push(`<details open><summary>${values.length} value(s) read from this boot's logs (LLM)</summary><div class="body"><table>`
      + '<tr><th>Key</th><th>Value</th><th>Log line</th></tr>'
      + values.map(([k, v]) => `<tr><td><code>${esc(k)}</code></td><td>${esc(JSON.stringify(v.value))}</td>`
        + `<td><span class="line">${esc(v.evidence)}</span></td></tr>`).join('') + '</table></div></details>');
  } else if (b.override_llm && b.override_llm !== 'no_evidence') {
    out.push(`<p class="muted">Override values: ${esc(b.override_llm)}</p>`);
  }
  if (b.overridden_keys && b.overridden_keys.length) {
    out.push(`<details><summary>${b.overridden_keys.length} key(s) overridden in this boot</summary><div class="body">`
      + b.overridden_keys.map(k => `<code>${esc(k)}</code>`).join(' ') + '</div></details>');
  }
  return out.join('');
}

function fmtGap(ms) {
  if (ms == null) return '';
  const s = ms / 1000;
  return s < 120 ? `${s.toFixed(0)} s` : s < 7200 ? `${(s / 60).toFixed(1)} min` : `${(s / 3600).toFixed(1)} h`;
}

function renderApiCalls(b) {
  const apis = Object.keys(b.api_calls || {}).sort((x, y) => x - y);
  if (!apis.length) return '<h3>API calls</h3><p class="muted">No API call lines in this boot</p>';
  const out = ['<h3>API calls</h3><p class="muted">Gap: time since the previous call of the same API in this boot</p>'
    + '<table><tr><th>ID</th><th>API</th><th class="num">Calls</th><th class="num">Min gap</th>'
    + '<th class="num">Avg gap</th><th class="num">Max gap</th><th>Calls (time · gap)</th></tr>'];
  for (const api of apis) {
    const ts = b.api_calls[api];
    const gaps = ts.slice(1).map((t, i) => t - ts[i]);
    const avg = gaps.length ? gaps.reduce((a, g) => a + g, 0) / gaps.length : null;
    const list = ts.map((t, i) => `${esc(fmt(t))}${i ? ` · ${fmtGap(gaps[i - 1])}` : ''}`).join('<br>');
    out.push(`<tr><td>${esc(api)}</td><td>${esc((D.api_names[api] || [api])[0])}</td><td class="num">${ts.length}</td>`
      + `<td class="num">${gaps.length ? fmtGap(Math.min(...gaps)) : '—'}</td><td class="num">${avg == null ? '—' : fmtGap(avg)}</td>`
      + `<td class="num">${gaps.length ? fmtGap(Math.max(...gaps)) : '—'}</td>`
      + `<td><details><summary class="muted">${ts.length} call(s)</summary><div class="body"><code>${list}</code></div></details></td></tr>`);
  }
  out.push('</table>');
  return out.join('');
}

function renderBoot(b) {
  const out = [renderDrive(b), renderFeatures(b), renderApiCalls(b), '<h3>Config coverage</h3>'];
  const cfg = b.config;
  if (cfg.error) {
    out.push(`<p class="muted">${esc(cfg.error)}</p>`);
  } else {
    out.push(`<p class="muted">First start of ${cfg.services.length} service(s) in this boot: ${esc(cfg.services.join(', '))}</p>`);
    if (cfg.failed_keys.length) {
      out.push(`<details><summary>${cfg.failed_keys.length} key(s) not mentioned in any service's CFG_PRSR lines`
        + '</summary><div class="body"><table><tr><th>Section</th><th>Key</th></tr>');
      for (const i of cfg.failed_keys) {
        const [section, key] = D.config_keys[i];
        out.push(`<tr><td>${esc(section)}</td><td><code>${esc(key)}</code></td></tr>`);
      }
      out.push('</table></div></details>');
    }
  }
  out.push('<h3>Test cases</h3>');
  const results = { FAIL: new Map(b.failures.map(f => [f.test, f])),
                    PASS: new Map((b.passes || []).map(p => [p.test, p])) };
  for (const v of ['FAIL', 'PASS']) {
    const ids = (b.verdicts[v] || []).slice().sort(byTc);
    if (!ids.length) continue;
    out.push(`<details${v === 'FAIL' ? ' open' : ''}><summary>${chip(v)} ${ids.length}</summary><div class="body">`);
    // Reports made before PASS steps were recorded have no b.passes: plain table for those
    if (v === 'FAIL' || b.passes) {
      for (const i of ids) {
        const t = D.tests[i], r = results[v].get(i);
        out.push(`<details><summary>${esc(t.feature)} · ${esc(t.tc_id)} — ${esc(t.functionality)}</summary>`
          + `<div class="body">${r.reason ? `<p>${esc(r.reason)}</p>` : ''}${stepsTable(t, r)}</div></details>`);
      }
    } else {
      out.push('<table><tr><th>Feature</th><th>TC</th><th>Functionality</th></tr>');
      for (const i of ids) {
        const t = D.tests[i];
        out.push(`<tr><td>${esc(t.feature)}</td><td>${esc(t.tc_id)}</td><td>${esc(t.functionality)}</td></tr>`);
      }
      out.push('</table>');
    }
    out.push('</div></details>');
  }
  return out.join('');
}

// Boot details are rendered from the embedded data when first opened
document.querySelectorAll('details[data-boot]').forEach(el => el.addEventListener('toggle', () => {
  const body = el.querySelector('.boot-body');
  if (el.open && !body.dataset.done) {
    body.innerHTML = renderBoot(D.boots[+el.dataset.boot]);
    body.dataset.done = '1';
  }
}));
function openHash() {
  const el = location.hash && document.getElementById(location.hash.slice(1));
  if (el && el.tagName === 'DETAILS') { el.open = true; el.scrollIntoView(); }
}
window.addEventListener('hashchange', openHash);
openHash();
"""


def _drive_rows(boots):
    rows = []
    for b in boots:
        d, power = b.get('drive') or {}, b.get('power') or {}
        notes = []
        if b.get('udid_source') == 'service_mon':
            notes.append('UDID unknown: no time_sync udid_node line')
        elif b.get('udid') is None:
            notes.append('No UDID in the logs')
        if d.get('session_issue'):
            notes.append('fewer sessions than drive minutes')
        if d.get('repeated_udid'):
            notes.append('UDID repeated')
        reasons = ', '.join(power.get('previous_shutdown_reasons', {}))
        sessions = f'{d["sessions_created"]} / {d["sessions_available"]}' if d else ''
        rows.append(
            f'<tr><td><a href="#boot-{b["index"]}">{_esc(b["label"])}</a></td>'
            f'<td>{_esc(_fmt_ts(d.get("drive_start"))[:19])}</td><td>{_esc(_fmt_ts(d.get("drive_end"))[:19])}</td>'
            f'<td class="num">{_esc(d.get("duration_min", ""))}</td><td class="num">{_esc(d.get("low_power_wakeup", ""))}</td>'
            f'<td class="num">{sessions}</td>'
            f'<td>{_esc(_fmt_ts(d.get("power_mon_start"))[11:19])}</td><td>{_esc(_fmt_ts(d.get("crank_low_at"))[11:19])}</td>'
            f'<td class="num">{_esc(_fmt_minutes(d.get("shutdown_after_ms")))}</td>'
            f'<td>{f"<code>{_esc(reasons)}</code>" if reasons else ""}</td><td class="muted">{_esc("; ".join(notes))}</td></tr>')
    head = ['Boot', 'Drive start', 'Drive end', '#Minutes', '#Low power wakeup', '#Sessions created / available',
            'Power monitor start', 'Crank low', '#Shutdown after crank low', 'Previous shutdown reason', 'Notes']
    return _table(head, rows)


DEVICE_NAV = [('drives', 'Drives'), ('features', 'Features enabled'), ('api', 'API calls'),
              ('boot-summary', 'Boot summary'), ('boots', 'Boots')]


def render_device_html(doc):
    device, ota = doc['device'], doc['ota_version']
    boots = doc['boots']
    out = [f'<div class="top"><h1>{_esc(device)}</h1><span>OTA {_esc(ota)} · {len(boots)} boot(s) · '
           f'days {_esc(", ".join(doc["days"]))}</span></div>',
           '<section id="drives"><h2>Drives and power monitor</h2>', _drive_overview(doc.get('drive_summary')),
           _drive_rows(boots), '</section>']

    features = doc.get('features') or []
    out.append('<section id="features"><h2>Features enabled</h2>')
    if doc.get('features_error'):
        out.append(f'<p class="muted">{_esc(doc["features_error"])}</p>')
    elif features:
        counts = [Counter() for _ in features]
        for b in boots:
            for i, st in enumerate(b.get('features') or []):
                counts[i][feature_status.STATUSES[st]] += 1
        out.append(f'<p class="muted">{_esc(FEATURES_NOTE)}</p>')
        out.append(_features_table(features, lambda i: counts[i]))
    else:
        out.append('<p class="muted">No features evaluated</p>')
    out.append('</section>')

    nav = DEVICE_NAV
    if doc.get('api_summary'):
        out.append(f'<section id="api"><h2>API calls</h2>{_api_section(doc["api_summary"])}</section>')
    else:
        nav = [n for n in nav if n[0] != 'api']

    rows = []
    labels = [_boot_label(dict(b, start=b['start_fmt'], end=b['end'] and b['end_fmt'])) for b in boots]
    for b, label in zip(boots, labels):
        on = '' if b.get('features') is None else sum(1 for st in b['features'] if st == 0)
        n_calls = sum(len(v) for v in (b.get('api_calls') or {}).values())
        rows.append(f'<tr><td><a href="#boot-{b["index"]}">{_esc(label)}</a></td><td class="num">{on}</td>'
                    f'<td class="num">{n_calls}</td><td>{_config_cell(b["config"])}</td><td>{_summary_chips(b["summary"])}</td></tr>')
    out.append('<section id="boot-summary"><h2>Boot summary</h2>'
               + _table(['Boot', '#Features ON', '#API calls', 'Config keys', 'Test cases'], rows) + '</section>')
    out.append('<section id="boots"><h2>Boots</h2><p class="muted">Boots are named by UDID · open a boot for its drive, '
               'features, config coverage and test cases</p>')
    for pos, (b, label) in enumerate(zip(boots, labels)):
        out.append(f'<details id="boot-{b["index"]}" data-boot="{pos}"><summary>{_esc(label)} · '
                   f'{_summary_chips(b["summary"])}</summary><div class="body boot-body"></div></details>')
    out.append('</section>')
    doc = dict(doc, api_names={str(k): v for k, v in api_calls.APIS.items()})
    data = json.dumps(doc, separators=(',', ':')).replace('</', '<\\/')
    side = (f'<a class="back" href="../ota_report.html">← OTA {_esc(ota)}</a>'
            + ''.join(f'<a href="#{i}">{_esc(t)}</a>' for i, t in nav))
    js = f'<script type="application/json" id="data">{data}</script><script>{REPORT_JS}{DEVICE_JS}</script>'
    return _shell(f'{device} {ota}', 'Device', device, side, '\n'.join(out), js, DEVICE_STYLE)


def load_tests(tests_dir, snapshot=None):
    """Test cases with their log steps (see testcase_log_steps.extract_all_cached)."""
    return [dict(t, key=f'{t["folder"]}/{t["name"]}')
            for t in testcase_log_steps.extract_all_cached(tests_dir, snapshot) if 'error' not in t]


def _day_bytes(day_dir):
    """Size of a day's downloaded logs (archives, else the extracted logs/), to order the reads."""
    archives = log_archive.archives(day_dir)
    if archives:
        # 7z/zip archives hold about 17x their size in logs
        return 17 * sum(p.stat().st_size for p in archives)
    return sum(p.stat().st_size for p in (Path(day_dir) / 'logs').rglob('*') if p.is_file())


RUN_TMP_PREFIX = 'ota_report_'


def _sweep_stale_tmp():
    """Remove temp folders of earlier runs whose process is gone (killed, crashed)."""
    for d in Path(log_archive.TMP_ROOT).glob(f'{RUN_TMP_PREFIX}*'):
        try:
            pid = int(d.name[len(RUN_TMP_PREFIX):].split('_')[0])
            os.kill(pid, 0)
        except ProcessLookupError:
            log.warning('removing %s, left by a run that is gone', d)
            shutil.rmtree(d, ignore_errors=True)
        except (ValueError, PermissionError):
            continue


def _stop_pool(pool):
    """Stop a pool now: drop queued jobs and kill the workers (they may hold temp files
    or be blocked on a shared queue lock; a graceful shutdown can hang)."""
    for proc in list(getattr(pool, '_processes', {}).values()):
        with contextlib.suppress(Exception):
            proc.kill()
    pool.shutdown(wait=False, cancel_futures=True)


def default_workers():
    return max(1, int((os.cpu_count() or 1) * WORKERS_PER_CPU))


def day_budget(workers):
    """Bytes the days being read at once may take: what the memory budget leaves after the
    workers, and at most TMP_SHARE of the temp filesystem's free space."""
    budget = MEMORY_BUDGET_BYTES - workers * WORKER_MEMORY_BYTES
    try:
        budget = min(budget, int(shutil.disk_usage(log_archive.TMP_ROOT).free * TMP_SHARE))
    except OSError:
        pass
    return max(budget, 0)


def run(devices, start, end, tests_dir, product=None, workers=None, report_dir=None, otas=None,
        tests_snapshot=None, on_progress=None):
    """Build the reports for devices over [start, end].

    report_dir: where <ota>/ota_report.* and <ota>/devices/ go (default OUTPUT/ota_reports).
    otas: only report boots on these OTA versions (default: every OTA found).
    on_progress(done, total, device, error): called in this process as each device finishes.
    Returns {'reports': {ota: {...kpis, 'path'}}, 'timings': [...], 'device_errors': {device: error}}.
    """
    global _TESTS, _EVAL_TESTS, _PATTERNS, _FEATURES
    devices = [str(d) for d in devices]
    workers = workers or default_workers()
    report_dir = Path(report_dir or REPORT_BASE_DIR)
    otas = set(otas) if otas else None
    timings, device_errors = [], {}
    run_start = time.perf_counter()

    with timed(log, 'load_tests', timings):
        _TESTS = load_tests(tests_dir, tests_snapshot)
        _EVAL_TESTS = [t for t in _TESTS if t['steps']]
        _PATTERNS = patterns_by_service(_TESTS)
    log.info('Extracted %d log steps from %d test files, %d patterns over %d services',
             sum(len(t['steps']) for t in _TESTS), len(_TESTS),
             sum(len(p) for p in _PATTERNS.values()), len(_PATTERNS))

    by_ota = defaultdict(dict)
    device_info = {}
    features = _FEATURES = feature_status.load_features()
    log.info('Override values by LLM: %s', 'on' if override_llm.enabled() else 'off (OVERRIDE_LLM / credentials)')
    # One job per device-day (read the logs), then one per device (put it together) as soon as
    # its last day is read. Day jobs are fed a few at a time so finishing jobs (with their LLM
    # calls) do not wait behind every remaining day. Workers fork after _TESTS/_PATTERNS/_FEATURES
    # are set, so they inherit them.
    device_day_list = {d: device_days(d, start, end) for d in devices}
    # Biggest days first, so the slowest reads don't run alone at the end
    day_jobs = sorted(((d, day, _day_bytes(LOG_BASE_DIR / d / day)) for d in devices for day in device_day_list[d]),
                      key=lambda job: -job[2])
    n_days = len(day_jobs)
    budget = day_budget(workers)
    log.info('Processing %d device(s), %d device-day(s) with %d worker(s), %s → %s; days in flight up to %.1f GB in %s',
             len(devices), n_days, workers, start, end, budget / 1024 ** 3, log_archive.TMP_ROOT)
    done = 0

    def device_done(device, error=None):
        nonlocal done
        done += 1
        if error:
            device_errors[device] = error
        log.info('Progress: %d/%d device(s) done', done, len(devices))
        if on_progress:
            on_progress(done, len(devices), device, error)

    # Everything the workers write (rebuilt days, hit spools) goes in one folder per run,
    # removed however the run ends; folders of runs that died without cleaning up are swept
    _sweep_stale_tmp()
    tmp_root = log_archive.TMP_ROOT
    run_tmp = tempfile.mkdtemp(prefix=f'{RUN_TMP_PREFIX}{os.getpid()}_', dir=tmp_root)
    log_archive.TMP_ROOT = run_tmp  # workers fork after this, so they use it
    for device, days in device_day_list.items():
        if not days:
            log.warning('%s: no logs between %s and %s', device, start, end)
            device_info[device] = {'days': 0, 'detailed_days': 0, 'otas': []}
            device_done(device)
    with timed(log, 'process_devices', timings, devices=len(devices), device_days=n_days):
        pool = ProcessPoolExecutor(workers)
        try:
            pending, results = {}, defaultdict(list)
            days_left = {d: len(v) for d, v in device_day_list.items() if v}
            in_flight = {}  # day future -> estimated bytes of its rebuilt logs

            def feed():
                # Keep the workers busy, but only start a day whose rebuilt logs fit next to the
                # ones being read (the biggest that fits; always one when nothing is running)
                while day_jobs and len(in_flight) < workers:
                    room = budget - sum(in_flight.values())
                    pick = next((i for i, job in enumerate(day_jobs) if job[2] <= room), None)
                    if pick is None:
                        if in_flight:
                            return
                        pick = 0
                    device, day, size = day_jobs.pop(pick)
                    future = pool.submit(scan_device_day, device, day)
                    pending[future] = ('day', device)
                    in_flight[future] = size

            feed()
            try:
                while pending:
                    finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in finished:
                        kind, device = pending.pop(future)
                        in_flight.pop(future, None)
                        try:
                            result = future.result()
                        except Exception as exc:  # one broken device must not sink the run
                            log.exception('%s: %s failed: %s', device, 'reading a day' if kind == 'day' else 'finishing', exc)
                            error = f'{type(exc).__name__}: {exc}'
                            if kind == 'day':
                                device_errors.setdefault(device, error)
                            else:
                                device_done(device, error)
                            result = None
                        if kind == 'day':
                            if result is not None:
                                results[device].append(result)
                            days_left[device] -= 1
                            if days_left[device] == 0:
                                day_results = results.pop(device, [])
                                if device in device_errors:
                                    for r in day_results:
                                        with contextlib.suppress(FileNotFoundError):
                                            os.unlink(r['spool'])
                                    device_done(device, device_errors[device])
                                else:
                                    pending[pool.submit(finish_device, device, day_results, product,
                                                        report_dir, otas)] = ('finish', device)
                        elif result is not None:
                            _, summaries, device_timings, device_info[device] = result
                            timings.extend(device_timings)
                            for ota, summary in summaries.items():
                                by_ota[ota][device] = summary
                            device_done(device)
                    feed()
            except BaseException:
                # Stopped part way (SIGTERM / cancel, error): stop the workers now; the run's
                # temp folder (rebuilt days, hit spools) goes in the outer finally
                _stop_pool(pool)
                raise
            pool.shutdown(wait=True)
        finally:
            log_archive.TMP_ROOT = tmp_root
            shutil.rmtree(run_tmp, ignore_errors=True)
    if not by_ota:
        log.warning('No device boots with an OTA version found in the range')

    reports = {}
    for ota, device_summaries in sorted(by_ota.items()):
        with timed(log, 'features', timings, ota=ota, devices=len(device_summaries)):
            config_features = complete_device_reports(ota, device_summaries, report_dir, features)
        with timed(log, 'write_ota_report', timings, ota=ota):
            report = build_ota_summary(ota, device_summaries, _TESTS)
            report['config_features'] = config_features
            report['summary'] = report_summary(ota, device_summaries, devices, device_info, features)
            out_dir = report_dir / ota
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / 'ota_report.json', 'w') as f:
                json.dump(report, f, separators=(',', ':'))
            with open(out_dir / 'ota_report.html', 'w') as f:
                f.write(render_html(report))
        verdicts = Counter()
        for feat in report['features'].values():
            verdicts.update(feat['summary'])
        reports[ota] = {
            'path': str(out_dir / 'ota_report.html'),
            'devices': len(report['devices']),
            'boots': sum(len(b) for b in report['devices'].values()),
            'verdicts': dict(verdicts),
            'config_keys': report['config_keys'],
            'config_keys_failing': len(report['config_failures']),
            'config_error': report['config_error'],
            'summary': {k: (len(v) if isinstance(v, list) else v) for k, v in report['summary'].items()
                        if k not in ('drive_minutes_by_device', 'boots_by_device', 'api_calls_by_device',
                                      'feature_coverage', 'feature_sets')},
        }
        log.info('%s: report written to %s/ota_report.html', ota, out_dir)
    log.info('Run finished in %.2fs: %d report(s), %d device error(s)',
             time.perf_counter() - run_start, len(reports), len(device_errors))
    return {'reports': reports, 'timings': timings, 'device_errors': device_errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--devices', nargs='+', required=True, help='One or more device IDs')
    parser.add_argument('--start', required=True, help='Start date, YYYY-MM-DD')
    parser.add_argument('--end', required=True, help='End date, YYYY-MM-DD (inclusive)')
    parser.add_argument('--tests-dir', default=str(testcase_log_steps.DEFAULT_TESTS_DIR),
                        help='pytest_device_validator tests directory')
    parser.add_argument('--product', help='OTA product folder override (default: inferred from version)')
    parser.add_argument('--workers', type=int, help='Devices processed in parallel (default: CPU count)')
    parser.add_argument('--ota', nargs='+', help='Only report boots on these OTA versions')
    parser.add_argument('--report-dir', help='Output folder (default OUTPUT/ota_reports)')
    args = parser.parse_args()
    run_logging.configure([REPO_ROOT / 'logs' / 'ota_report.log'])
    # SIGTERM unwinds like Ctrl-C, so temp files are cleaned up
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    run(args.devices, args.start, args.end, args.tests_dir, args.product, args.workers,
        args.report_dir, args.ota)


if __name__ == '__main__':
    main()
