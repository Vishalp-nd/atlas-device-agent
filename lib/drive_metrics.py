"""
UDID, drive session and power-monitor metrics per device boot (see DRIVE_METRICS_LOGIC.md).

scan_day(logs_dir) greps the few lines these need, one `grep -F` per service:
  time_sync    udid_node.index ...       the previous drive's UDID / start / end / RTC jump
  power_mon    lowpower_wakeups, STARTING POWER MONITOR, Inside sync_all,
               crank voltage state changed, previous_shutdown_reason
  ndcentral    creating folder for session     (sessions created)
  circ_buff    Found no entry with NAME ...mp4 (sessions available)
  service_mon  Non-Alert ... Latest UDID: N    (UDID fallback for critical-only logs)
Days are merged with DriveEvents.add (overlapping downloads repeat lines, so events
are kept in sets), then boot_metrics() derives everything for one boot.

UDID of a boot: the udid_node line is written early in the *next* boot and
describes the drive that just ended, whose drive_start is the boot it belongs to.
So the boot gets the record whose drive_start is closest to its start. Logs with
only the critical (_c) files have no udid_node lines; there the boot's UDID is
the "Latest UDID" service_mon reports during it (the last finished drive) + 1.

Deviations from the md, on purpose:
  - Low power wakeup lines are matched by regex ("lowpower_wakeups N < ...max M")
    instead of the fixed 55/56 maximum and split()[10] token position.
  - Previous shutdown reasons are kept whole (e.g. DBSTATE_SHUTDOWN_CRANKOFF:SHUTDOWN)
    rather than cut at the last "_".
"""

import bisect
import logging
import re
from collections import Counter

import cfg_parser_logs

log = logging.getLogger('drive_metrics')

PATTERNS = {
    'time_sync': ['udid_node.index'],
    'power_mon': ['lowpower_wakeups', 'STARTING POWER MONITOR', 'Inside sync_all',
                  'crank voltage state changed', 'previous_shutdown_reason'],
    'ndcentral': ['creating folder for session'],
    'circ_buff': ['Found no entry with NAME'],
    'service_mon': ['Non-Alert Oldest UDID'],
}

LINE_RE = re.compile(r'^(\d{13}):\s*(\d+):')
UDID_RE = re.compile(r'udid_node\.udid (\d{1,6})')
UDID_FIELDS = {name: re.compile(rf'"{name}": (\d{{1,13}})')
               for name in ('drive_start', 'drive_end', 'rtc_jump_from', 'rtc_jump_to')}
LPW_RE = re.compile(r'POWER_MONITOR_ctx->lowpower_wakeups\s+(\d+)\s*<\s*POWER_MONITOR_ctx->max_lowpower_wakeups\s+(\d+)')
CRANK_RE = re.compile(r'crank voltage state changed ([01])')
SHUTDOWN_RE = re.compile(r'previous_shutdown_reason\s*:?\s*(\S+)')
SESSION_RE = re.compile(r'creating folder for session (\S+)')
AVAILABLE_RE = re.compile(r'Found no entry with NAME (0_trip\S*_y\.mp4)\.')
LATEST_UDID_RE = re.compile(r'Non-Alert Oldest UDID: \d+ SC: \d+ Latest UDID: (\d+)')

# udid_node drive_start (seconds precision) vs the detected boot start
UDID_MATCH_MS = 2 * 60 * 1000
# DRIVE_METRICS_LOGIC.md section 5 merge_asof tolerances
PM_START_TOLERANCE_MS = 30 * 1000
PM_STOP_TOLERANCE_MS = 30 * 1000
CRANK_LOW_TOLERANCE_MS = 950 * 1000
# Section 4 service duration buckets (power_mon uptime at the end of a service run)
SHORT_RUN_MS = 350000
IMPROPER_RUN_MS = (420000, 960000)
# Section 5 drive buckets (minutes)
SHORT_DRIVE_MIN = 6
LONG_DRIVE_MIN = 15
TAIL_BYTES = 64 * 1024


def _line_ts(line):
    m = LINE_RE.match(line)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def _session_ts(name):
    # ..._<lat>_<long>_<speed>_<epoch ms>_y: the 7th '_' field, as in the md
    try:
        return int(name.split('_')[6])
    except (IndexError, ValueError):
        return None


def _service_files(logs_dir, service):
    return sorted(p for p in logs_dir.glob(f'{service}*.log*') if p.is_file())


def _last_timed_line(data):
    """(ts, uptime) of the last line with a timestamp in data (bytes), or (None, None)."""
    for line in reversed(data.decode('latin-1').split('\n')):
        ts, uptime = _line_ts(line)
        if ts is not None:
            return ts, uptime
    return None, None


class DriveEvents:
    """Everything scan_day found for one device, merged over days."""

    def __init__(self):
        self.udids = {}          # (udid, drive_start) -> record
        self.lpw = set()         # (ts, counter, max)
        self.pm_starts = set()   # ts
        self.pm_stops = set()
        self.cranks = set()      # (ts, state)
        self.shutdowns = set()   # (ts, reason)
        self.created = set()     # session ts
        self.available = set()
        self.latest_udid = set()  # (ts, udid)
        self.run_ends = set()    # (ts, uptime) of the last power_mon line before a restart / end of file

    def add(self, other):
        self.udids.update(other.udids)
        for name in ('lpw', 'pm_starts', 'pm_stops', 'cranks', 'shutdowns', 'created', 'available',
                     'latest_udid', 'run_ends'):
            getattr(self, name).update(getattr(other, name))

    def finish(self):
        """Sorted lists for the per-boot lookups."""
        self.udid_list = sorted(self.udids.values(), key=lambda r: r['drive_start'])
        self.udid_counts = Counter(r['udid'] for r in self.udid_list)
        for name in ('lpw', 'pm_starts', 'pm_stops', 'cranks', 'shutdowns', 'created', 'available',
                     'latest_udid', 'run_ends'):
            setattr(self, name + '_sorted', sorted(getattr(self, name)))
        self.crank_low = [ts for ts, state in self.cranks_sorted if state == 0]
        return self


def _scan_power_mon(files, events):
    """power_mon hits, plus the uptime at the end of each power monitor run.

    A run ends at the last line before the next STARTING POWER MONITOR (read back
    from grep's byte offset) or at the end of the file.
    """
    handles = {}
    try:
        for path, hit in cfg_parser_logs.grep_lines(['-b', '-F', *sum((['-e', p] for p in PATTERNS['power_mon']), [])],
                                                    files):
            offset, _, line = hit.partition(':')
            ts, _ = _line_ts(line)
            if ts is None:
                continue
            if 'STARTING POWER MONITOR' in line:
                events.pm_starts.add(ts)
                f = handles.get(path) or handles.setdefault(path, open(path, 'rb'))
                start = max(0, int(offset) - TAIL_BYTES)
                f.seek(start)
                end_ts, end_uptime = _last_timed_line(f.read(int(offset) - start))
                if end_ts is not None:
                    events.run_ends.add((end_ts, end_uptime))
            elif 'Inside sync_all' in line:
                events.pm_stops.add(ts)
            elif m := CRANK_RE.search(line):
                events.cranks.add((ts, int(m.group(1))))
            elif m := LPW_RE.search(line):
                events.lpw.add((ts, int(m.group(1)), int(m.group(2))))
            elif m := SHUTDOWN_RE.search(line):
                reason = m.group(1).strip(':')
                events.shutdowns.add((ts, 'NA' if reason in ('NA', '') else reason))
    finally:
        for f in handles.values():
            f.close()
    for path in files:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            end_ts, end_uptime = _last_timed_line(f.read())
        if end_ts is not None:
            events.run_ends.add((end_ts, end_uptime))


def scan_day(logs_dir):
    """DriveEvents for one day's logs directory."""
    events = DriveEvents()
    for service, patterns in PATTERNS.items():
        files = _service_files(logs_dir, service)
        if not files:
            continue
        if service == 'power_mon':
            _scan_power_mon(files, events)
            continue
        args = ['-F', *sum((['-e', p] for p in patterns), [])]
        for _, line in cfg_parser_logs.grep_lines(args, files):
            ts, _ = _line_ts(line)
            if ts is None:
                continue
            if service == 'time_sync':
                m = UDID_RE.search(line)
                fields = {name: rx.search(line) for name, rx in UDID_FIELDS.items()}
                if not m or not fields['drive_start'] or not fields['drive_end']:
                    continue
                rec = {'udid': int(m.group(1)), 'logged_at': ts,
                       **{name: int(f.group(1)) if f else 0 for name, f in fields.items()}}
                # No time sync during the drive: both jump points are the drive end
                if rec['rtc_jump_to'] == 0:
                    rec['rtc_jump_to'] = rec['drive_end']
                if rec['rtc_jump_from'] == 0:
                    rec['rtc_jump_from'] = rec['drive_end']
                events.udids[(rec['udid'], rec['drive_start'])] = rec
            elif service == 'ndcentral':
                m = SESSION_RE.search(line)
                session_ts = _session_ts(m.group(1)) if m else None
                if session_ts:
                    events.created.add(session_ts)
            elif service == 'circ_buff':
                m = AVAILABLE_RE.search(line)
                session_ts = _session_ts(m.group(1)) if m else None
                if session_ts:
                    events.available.add(session_ts)
            elif service == 'service_mon':
                m = LATEST_UDID_RE.search(line)
                if m:
                    events.latest_udid.add((ts, int(m.group(1))))
    return events


# ── per boot ─────────────────────────────────────────────────────────────

def _between(sorted_list, lo, hi, key=lambda x: x):
    """Items of a sorted list with lo <= key(item) <= hi (lists of tuples sort by ts)."""
    if lo is None:
        lo = float('-inf')
    if hi is None:
        hi = float('inf')
    keys = [key(x) for x in sorted_list] if key is not None else sorted_list
    return sorted_list[bisect.bisect_left(keys, lo):bisect.bisect_right(keys, hi)]


def _count_in(sorted_ts, lo, hi):
    return bisect.bisect_right(sorted_ts, hi) - bisect.bisect_left(sorted_ts, lo)


def _nearest(sorted_ts, target, tolerance):
    i = bisect.bisect_left(sorted_ts, target)
    best = min((sorted_ts[j] for j in (i - 1, i) if 0 <= j < len(sorted_ts)),
               key=lambda t: abs(t - target), default=None)
    return best if best is not None and abs(best - target) <= tolerance else None


def _last_before(sorted_ts, target, tolerance):
    i = bisect.bisect_right(sorted_ts, target)
    return sorted_ts[i - 1] if i and target - sorted_ts[i - 1] <= tolerance else None


def assign_udids(events, boots):
    """{boot index: udid_node record}: each record goes to the one boot whose start is
    closest to its drive_start (within UDID_MATCH_MS), each boot gets at most one record."""
    starts = [(b['start'], b['index']) for b in boots if b['start'] is not None]
    pairs = []
    for rec in events.udid_list:
        for start, index in starts:
            gap = abs(rec['drive_start'] - start)
            if gap <= UDID_MATCH_MS:
                pairs.append((gap, index, rec['udid'], rec['drive_start']))
    assigned, used = {}, set()
    for gap, index, udid, drive_start in sorted(pairs):
        if index not in assigned and (udid, drive_start) not in used:
            assigned[index] = events.udids[(udid, drive_start)]
            used.add((udid, drive_start))
    # The boot before the first detected one started before the logs, so its start can't be
    # matched; it gets the latest unused drive that started before its end and ended with it
    for b in boots:
        if b['start'] is None and b['end'] is not None and b['index'] not in assigned:
            inside = [r for r in events.udid_list if (r['udid'], r['drive_start']) not in used
                      and r['drive_start'] < b['end'] and r['drive_end'] <= b['end'] + UDID_MATCH_MS]
            if inside:
                rec = max(inside, key=lambda r: r['drive_start'])
                assigned[b['index']] = events.udids[(rec['udid'], rec['drive_start'])]
                used.add((rec['udid'], rec['drive_start']))
    return assigned


def drive_window_metrics(events, rec):
    """Sections 1-3 and 5 of the md for one udid_node drive record."""
    start, end = rec['drive_start'], rec['drive_end']
    jump_from, jump_to = rec['rtc_jump_from'], rec['rtc_jump_to']
    windows = [(start, jump_from), (jump_to, end)]
    # Section 2: the counter of the first wakeup line before the time sync, else 0
    first = next((c for ts, c, _ in events.lpw_sorted if start <= ts <= jump_from), None)
    created = sum(_count_in(events.created_sorted, lo, hi) for lo, hi in windows)
    available = sum(_count_in(events.available_sorted, lo, hi) for lo, hi in windows)
    minutes = (end - start) // 1000 // 60
    pm_start = _nearest(events.pm_starts_sorted, start, PM_START_TOLERANCE_MS)
    pm_stop = _nearest(events.pm_stops_sorted, end, PM_STOP_TOLERANCE_MS)
    crank_low = _last_before(events.crank_low, end, CRANK_LOW_TOLERANCE_MS)
    return {
        'drive_start': start, 'drive_end': end,
        'rtc_jump_from': jump_from, 'rtc_jump_to': jump_to,
        'duration_min': minutes,
        'low_power_wakeup': first or 0,
        'sessions_created': created,
        'sessions_available': available,
        # Roughly one session per minute of driving
        'session_issue': minutes > created,
        'repeated_udid': events.udid_counts[rec['udid']] > 1,
        'power_mon_start': pm_start,
        'power_mon_stop': pm_stop,
        'crank_low_at': crank_low,
        # The md fills a missing side with 0, which gives a meaningless number; leave it empty instead
        'shutdown_after_ms': pm_stop - crank_low if pm_stop and crank_low else None,
    }


def boot_metrics(events, start, end, rec=None):
    """UDID and drive/power-monitor numbers for the boot [start, end).

    rec: the boot's udid_node record (assign_udids), if any.
    """
    hi = (end - 1) if end is not None else None
    if rec:
        udid, source = rec['udid'], 'udid_node'
    else:
        latest = _between(events.latest_udid_sorted, start, hi, key=lambda x: x[0])
        udid, source = (latest[0][1] + 1, 'service_mon') if latest else (None, None)

    starts = _between(events.pm_starts_sorted, start, hi, key=None)
    run_ends = events.run_ends_sorted
    durations = []
    all_starts = events.pm_starts_sorted
    for s in starts:
        i = bisect.bisect_right(all_starts, s)
        nxt = all_starts[i] if i < len(all_starts) else float('inf')
        ends = _between(run_ends, s, nxt - 1, key=lambda x: x[0])
        if ends:
            end_ts, end_uptime = ends[-1]
            # Critical-only logs miss some restarts, so the last line can belong to an older
            # process: a run can't have lasted longer than the time since its start line
            durations.append(min(end_uptime, end_ts - s + 60 * 1000))
    lpw = _between(events.lpw_sorted, start, hi, key=lambda x: x[0])
    drive = drive_window_metrics(events, rec) if rec else None
    # Drive time: the udid_node drive when logged, else the power monitor runs that were
    # not low power wakeups (critical-only logs have no udid_node lines)
    if drive:
        drive_minutes, minutes_source = drive['duration_min'], 'udid'
    else:
        drive_minutes = sum(d for d in durations if d >= SHORT_RUN_MS) // 60000
        minutes_source = 'power_mon' if durations else None
    max_lpw = max((c for _, c, _ in lpw), default=None)
    shutdowns = _between(events.shutdowns_sorted, start, hi, key=lambda x: x[0])
    cranks = _between(events.cranks_sorted, start, hi, key=lambda x: x[0])
    return {
        'udid': udid,
        # udid_node: logged by the device; service_mon: "Latest UDID" + 1 (critical logs only)
        'udid_source': source,
        'drive': drive,
        'drive_minutes': drive_minutes,
        'drive_minutes_source': minutes_source,
        # Low power mode: the wakeup counter moved, or the power monitor ran briefly (a low
        # power wakeup, section 4 of the md)
        'low_power': bool((max_lpw or 0) > 0 or (drive and drive['low_power_wakeup'])
                          or any(d < SHORT_RUN_MS for d in durations)),
        'power': {
            'service_starts': len(starts),
            'crank_ons': sum(1 for _, state in cranks if state == 1),
            'low_power_wakeup_lines': len(lpw),
            'max_low_power_wakeups': max_lpw,
            'previous_shutdown_reasons': dict(Counter(r for _, r in shutdowns)),
            'runs_short': sum(1 for d in durations if d < SHORT_RUN_MS),
            'runs_improper': sum(1 for d in durations if IMPROPER_RUN_MS[0] < d < IMPROPER_RUN_MS[1]),
        },
    }


def summarize(boot_metric_list):
    """Device/OTA rollup (sections 2, 4 and 5 aggregates) over boot_metrics() results."""
    drives = [m['drive'] for m in boot_metric_list if m.get('drive')]
    reasons = Counter()
    for m in boot_metric_list:
        reasons.update(m['power']['previous_shutdown_reasons'])
    lpw = [d['low_power_wakeup'] for d in drives]
    max_lpw = [m['power']['max_low_power_wakeups'] for m in boot_metric_list
               if m['power']['max_low_power_wakeups'] is not None]
    return {
        'boots': len(boot_metric_list),
        'drive_minutes_total': sum(m.get('drive_minutes') or 0 for m in boot_metric_list),
        'drive_minutes_estimated': sum(m.get('drive_minutes') or 0 for m in boot_metric_list
                                       if m.get('drive_minutes_source') == 'power_mon'),
        'low_power_boots': sum(1 for m in boot_metric_list if m.get('low_power')),
        'boots_with_udid': sum(1 for m in boot_metric_list if m.get('udid') is not None),
        'drives': len(drives),
        'drive_minutes': sum(d['duration_min'] for d in drives),
        'drives_short': sum(1 for d in drives if d['duration_min'] < SHORT_DRIVE_MIN),
        'drives_medium': sum(1 for d in drives if SHORT_DRIVE_MIN < d['duration_min'] < LONG_DRIVE_MIN),
        'drives_with_low_power_wakeup': sum(1 for v in lpw if v),
        'max_low_power_wakeup': max(lpw, default=None),
        'session_issues': sum(1 for d in drives if d['session_issue']),
        'repeated_udids': sum(1 for d in drives if d['repeated_udid']),
        'sessions_created': sum(d['sessions_created'] for d in drives),
        'sessions_available': sum(d['sessions_available'] for d in drives),
        'service_starts': sum(m['power']['service_starts'] for m in boot_metric_list),
        'crank_ons': sum(m['power']['crank_ons'] for m in boot_metric_list),
        'low_power_wakeup_lines': sum(m['power']['low_power_wakeup_lines'] for m in boot_metric_list),
        'max_low_power_wakeups': max(max_lpw, default=None),
        'runs_short': sum(m['power']['runs_short'] for m in boot_metric_list),
        'runs_improper': sum(m['power']['runs_improper'] for m in boot_metric_list),
        'previous_shutdown_reasons': dict(reasons.most_common()),
    }


def merge_summaries(summaries):
    """Add up summarize() results (devices -> OTA)."""
    total = Counter()
    reasons = Counter()
    maxima = {'max_low_power_wakeup': None, 'max_low_power_wakeups': None}
    for s in summaries:
        for k, v in s.items():
            if k in maxima:
                if v is not None:
                    maxima[k] = v if maxima[k] is None else max(maxima[k], v)
            elif k == 'previous_shutdown_reasons':
                reasons.update(v)
            else:
                total[k] += v
    return {**total, **maxima, 'previous_shutdown_reasons': dict(reasons.most_common())}
