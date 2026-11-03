"""What the day's upload holds: detailed (non critical) logs or not, and the earliest and
latest timestamped line over every file."""

import os
from datetime import datetime, timezone
from pathlib import Path

from log_search import MATCH_LINE_CHARS, line_timestamp

STEP = 'log_span'

# Plain (non _c) logs a critical-only upload still has; any other plain log means the
# device uploads its detailed (non critical) logs
CRITICAL_UPLOAD_PLAIN_LOGS = {'bhcopy', 'timer', 'timer2'}
LOG_SPAN_CHUNK = 64 * 1024
DAY_MS = 86_400_000
LOG_TS_MIN = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def has_detailed_logs(logs_dir):
    """Whether a day's logs include non-critical logs (plain <service>.log files)."""
    for p in logs_dir.iterdir():
        if p.is_file() and '.log' in p.name and '_c.log' not in p.name:
            if p.name.split('.log')[0] not in CRITICAL_UPLOAD_PLAIN_LOGS:
                return True
    return False


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


def scan(ctx):
    return {'detailed': has_detailed_logs(ctx.logs_dir), 'log_span': log_span(ctx.logs_dir, ctx.day)}
