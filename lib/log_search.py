"""Shared helpers for searching a day's log files: line timestamps, a service's files, and
pattern hits classified per line. Used by the day scanners (lib/scanners/) and ota_report.
"""

import re
import tempfile
from datetime import datetime, timezone

import cfg_parser_logs

EPOCH_MS_RE = re.compile(r'(\d{13})\b')
DATETIME_RE = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:[,.](\d{3}))?')
# Leading timestamp/uptime fields: their digits are zeroed when caching which patterns
# a line matches, so repeated messages share a cache entry
LINE_PREFIX_RE = re.compile(r'^(?:\d+:\s*\d+:|\d{4}-\d{2}-\d{2} [\d:,.]+)')
ZERO_DIGITS = str.maketrans('123456789', '000000000')

# How far back to read for the timestamp of an untimed (continuation) line
BACKSCAN_BYTES = 64 * 1024
MATCH_LINE_CHARS = 240
CLASSIFY_CACHE_MAX = 200_000


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
