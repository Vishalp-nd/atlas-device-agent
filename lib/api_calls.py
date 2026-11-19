"""
Cloud API calls per device boot: how many calls of each API and the time between them.

The message ID of a call is not logged, so each API is found by the line its service
writes when it makes the request (one line per call, with the endpoint):
  keep_alive_manager  wget -O .../keepaliveresponse.txt               KEEP_ALIVE
  power_mon           keepalive command :: ... / --<date>--  .../keep-alive/   KEEP_ALIVE (low power)
  otacheck            Executing command: wget ... versioncheckresponse  VERSION_CHECK
                      wget -O .nddevice/<ver>/<ver>.txt (download/module/cp/version)  DOWNLOAD_OTA
                      wget -O .../downloadresponse.txt, curl ... /download/version/   DOWNLOAD_STATUS
  unifieduploader     Calling service: .../upload/<eventdata|logs|video|observations|lla>
  circ_buff           URL @@ .../upload/videolist                     UPLOAD_VIDEOLIST
  health              curl -X POST ... /devices/<id>/<type>/status    UPLOAD_DEVICE_STATUS
  awsiot              send_keepalive: POST .../upload/pingdata        UPLOAD_PING_DATA
  btfv (nd_bt_man)    Final Curl URL : .../upload/driverLogin          DRIVER_LOGIN
                      Final Curl URL : .../devices/driverLoginStatus/  READ_DRIVER_LOGIN_STATUS
  health_central      Uploading component health to .../upload/component-health  HEALTH_CENTRAL
                      (not in the API list, so it has its own id, 28)
The "command: wget ..." line otacheck and keep_alive_manager print after a call echoes the
same request, so it is not counted. The DIAG REQ_CURL_HEALTH_REPORT / CURL_HC lines are not
used: they are missing for most calls when only critical (_c) logs are uploaded.
APIs without a pattern were not seen being called in the logs these were taken from.

Results (successful / failed calls), found in the same grep pass (RESULTS):
  health              newMessage: {... 'endpoint': ..., 'curl_code': N, 'http_code': N, 'ts': ms ...}
                      the curl stats every service reports (keep_alive_manager, otacheck, health,
                      unifieduploader, circ_buff ...), one per finished call, timed by its 'ts'
  unifieduploader,    CURL_HC: curl_health: svc=.. endpoint=.. curl=N http=N  (same calls: used
  circ_buff           only where health has no record of them, e.g. health.log not uploaded)
  power_mon           response_string :...: reached_cloud 1 / 0  for its low power keep-alives
Success: curl 0 and HTTP 2xx (curl 0 alone when no HTTP code is given); a failure keeps its
reason (curl code, HTTP status, power_mon "reached_cloud 0"). A power_mon keep-alive with no
response line before its next one is sent right before the device shuts down: it is
"unconfirmed", counted neither way (on a device with network such calls did reach the server,
checked against the API audit DB, but nothing on the device tells).
Calls without any result line (critical logs only, ...) are attempts only, so successful counts
can be lower than the server's when the result lines are missing.

Python service logs (keep_alive_manager, health, btfv, ...) have no _c copy with these
lines, so devices that upload critical logs only show little more than otacheck's calls.
"""

import bisect
import re
from collections import defaultdict

# message id -> (name, kind); kind as given with the API list (Periodic / NP / '')
APIS = {
    1: ('KEEP_ALIVE', 'NP / Periodic'),
    2: ('VERSION_CHECK', 'Periodic'),
    3: ('UPLOAD_EVENTDATA', 'NP'),
    4: ('UPLOAD_ALLDATA', ''),
    5: ('DOWNLOAD_OTA', 'Periodic'),
    6: ('DOWNLOAD_STATUS', ''),
    7: ('UPLOAD_LOGS', 'Periodic'),
    8: ('UPLOAD_VIDEOLIST', 'Periodic'),
    9: ('UPLOAD_VIDEO', 'NP'),
    10: ('UPLOAD_DEVICE_STATUS', 'Periodic'),
    11: ('UPLOAD_PING_DATA', ''),
    12: ('UPLOAD_FV_VIDEO', ''),
    13: ('DRIVER_LOGIN', ''),
    14: ('UPLOAD_VEHICLEDATA', ''),
    15: ('DEVICE_REGISTER', 'NP'),
    16: ('UPLOAD_OBSERVATIONS', 'Periodic'),
    19: ('UPLOAD_LLA', ''),
    20: ('SECRET_REGISTER', ''),
    21: ('UPLOAD_CAN_DIAGNOSTICS', ''),
    22: ('UPLOAD_DTC_REPORT', ''),
    23: ('UPLOAD_DEVICE_ROOT_PASSWORD_CHANGE_API_EVENT_INFO', ''),
    24: ('HERE_OAUTH_TOKEN', ''),
    25: ('PID_DOWNLOAD_STATUS', ''),
    26: ('UPLOAD_CALIBRATION_VIDEO', ''),
    27: ('READ_DRIVER_LOGIN_STATUS', 'NP'),
    28: ('HEALTH_CENTRAL', 'Periodic'),  # upload/component-health (no message id in the API list)
}

# (message id, service log, grep BRE prefilter, regex): one line per call
PATTERNS = [
    (1, 'keep_alive_manager', 'keepaliveresponse', r'INFO - wget\s+-O \S*/keepaliveresponse\.txt'),
    (1, 'power_mon', 'keepalive command ::', r'^keepalive command ::'),
    (1, 'power_mon', '/keep-alive/', r'^--\d{4}-\d\d-\d\d \d\d:\d\d:\d\d--\s+https?://\S+/keep-alive/'),
    (2, 'otacheck', 'Executing command: wget', r'Executing command: wget .*versioncheckresponse'),
    (5, 'otacheck', 'INFO - wget -O ', r'INFO - wget -O \S*/\.nddevice/\d[^/\s]*/[^/\s]+\.txt '),
    (6, 'otacheck', 'INFO - wget -O ', r'INFO - wget -O \S*/downloadresponse\.txt'),
    (6, 'otacheck', 'INFO - curl ', r'INFO - curl .*/download/version/'),
    (3, 'unifieduploader', 'Calling service: ', r'Calling service: \S+/upload/eventdata\b'),
    (7, 'unifieduploader', 'Calling service: ', r'Calling service: \S+/upload/logs\b'),
    (9, 'unifieduploader', 'Calling service: ', r'Calling service: \S+/upload/video/'),
    (16, 'unifieduploader', 'Calling service: ', r'Calling service: \S+/upload/observations\b'),
    (19, 'unifieduploader', 'Calling service: ', r'Calling service: \S+/upload/lla\b'),
    (8, 'circ_buff', 'URL @@ ', r'URL @@ \S+/upload/videolist'),
    (10, 'health', 'curl -X POST', r'curl -X POST .*/devices/\d+/\w+/status'),
    (11, 'awsiot', 'send_keepalive: POST', r'send_keepalive: POST \S+/upload/pingdata'),
    (13, 'btfv', 'Final Curl URL', r'Final Curl URL : \S+/upload/driverLogin\b'),
    (27, 'btfv', 'Final Curl URL', r'Final Curl URL : \S+/devices/driverLoginStatus/'),
    (28, 'health_central', 'Uploading component health to', r'Uploading component health to \S+/upload/component-health'),
]
WITH_PATTERN = sorted({api for api, *_ in PATTERNS})

# (source, service log, grep BRE prefilter, regex): one line per finished call (see the docstring)
RESULTS = [
    ('hs', 'health', 'newMessage: ', r"newMessage: \{.*'curl_code'"),
    ('hc', 'unifieduploader', 'curl_health: ', r'curl_health: svc=\S+ endpoint=\S+ curl=\d+ http=\d+'),
    ('hc', 'circ_buff', 'curl_health: ', r'curl_health: svc=\S+ endpoint=\S+ curl=\d+ http=\d+'),
    ('pm', 'power_mon', 'reached_cloud', r'response_string :.*: reached_cloud [01]'),
]
# Endpoint of a result -> message id (first match; videolist before video/)
ENDPOINT_APIS = [(re.compile(rx), api) for rx, api in (
    (r'/keep-alive/', 1),
    (r'versioncheck', 2),
    (r'upload/eventdata\b', 3),
    (r'download/module/', 5),
    (r'download/version/', 6),
    (r'upload/logs\b', 7),
    (r'upload/videolist', 8),
    (r'upload/video/', 9),
    (r'devices/\d+/\w+/status', 10),
    (r'upload/pingdata', 11),
    (r'devices/driverLoginStatus', 27),
    (r'upload/driverLogin\b', 13),
    (r'upload/observations', 16),
    (r'upload/lla\b', 19),
    (r'upload/component-health', 28),
)]
_STATS_FIELD = re.compile(r"'(endpoint|curl_code|http_code|ts)': '?([^',}]*)")
_CURL_HEALTH = re.compile(r'endpoint=(\S+) curl=(\d+) http=(\d+)')
_REACHED = re.compile(r'reached_cloud ([01])')
# Failure reasons: curl exit codes seen in the logs (143: wget killed, e.g. by power_mon's "pkill wget")
CURL_ERRORS = {6: 'no network: could not resolve host', 7: 'could not connect', 28: 'timeout',
               35: 'TLS error', 52: 'empty reply', 55: 'send error', 56: 'connection reset', 143: 'killed'}
PM_FAILED = 'not reached (power_mon reached_cloud 0)'
# A curl_health line is the same call as a health stats record this close to it
SAME_RESULT_MS = 5000
# A power_mon response line from another copy of the log this soon after a keep-alive "sent" is its result
PM_RESPONSE_MS = 120000

# Order the API tables list them in; APIs not named here follow by id
DISPLAY_ORDER = [
    1,   # Keep Alive
    2,   # Version Check
    16,  # Upload Observation data
    8,   # Upload Video list
    7,   # Upload Logs
    5,   # Download OTA
    6,   # Download Status
    3,   # Upload Event
    9,   # Upload Video
    10,  # Upload Device Status
    28,  # Health Central
    19,  # Upload LLA
    21,  # Can Diagnostics
    13,  # Driver Login
]
DISPLAY_ORDER += [api for api in sorted(APIS) if api not in DISPLAY_ORDER]
# The APIs with a call pattern, in display order
SHOWN = [api for api in DISPLAY_ORDER if api in WITH_PATTERN]

# Calls of one API this close together are one call logged twice (a day's plain and _c
# log, or power_mon's command and wget output lines)
DUP_MS = 1000


def _service_files(logs_dir, service):
    """<service>.log* and <service>_c.log* (exact name: 'health' is not health_central)."""
    name = re.compile(rf'{re.escape(service)}(?:_c)?\.log')
    return sorted(p for p in logs_dir.glob(f'{service}*.log*') if p.is_file() and name.match(p.name))


def endpoint_api(endpoint):
    for rx, api in ENDPOINT_APIS:
        if rx.search(endpoint):
            return api
    return None


def failure_reason(curl, http):
    if curl:
        return f'curl {curl} ({CURL_ERRORS[curl]})' if curl in CURL_ERRORS else f'curl {curl}'
    return f'HTTP {http}'


def _result(source, line, ts):
    """(api id, ts, '<source>:ok' / '<source>:fail:<reason>') of a RESULTS line, None when it
    names no known API."""
    if source == 'pm':
        m = _REACHED.search(line)
        return m and (1, ts, 'pm:ok' if m.group(1) == '1' else f'pm:fail:{PM_FAILED}')
    if source == 'hs':
        f = dict(_STATS_FIELD.findall(line))
        endpoint, curl, http = f.get('endpoint', ''), f.get('curl_code', ''), f.get('http_code', '')
        with_ts = f.get('ts', '')
        if with_ts.replace('.', '', 1).isdigit():
            ts = int(float(with_ts))
    else:
        m = _CURL_HEALTH.search(line)
        if not m:
            return None
        endpoint, curl, http = m.groups()
    api = endpoint_api(endpoint)
    if api is None or not curl.isdigit():
        return None
    curl, http = int(curl), int(http) if http.isdigit() else None
    if curl == 0 and (http is None or 200 <= http < 300):
        return api, ts, f'{source}:ok'
    return api, ts, f'{source}:fail:{failure_reason(curl, http)}'


def scan_day(logs_dir, iter_hits):
    """{(api id, ts, kind)} of one day's logs directory: kind 'call' for a call (PATTERNS),
    '<source>:ok' / '<source>:fail:<reason>' for a result (RESULTS), 'pm:sent' for a power_mon
    keep-alive with no response line.

    iter_hits(files, [(bre, regex)]) yields (ts, [pattern index, ...], line), untimed
    lines taking the time of the line before them (log_search.iter_service_hits).
    """
    out = set()
    by_service = defaultdict(list)
    for api, service, bre, regex in PATTERNS:
        by_service[service].append(('call', api, bre, regex))
    for source, service, bre, regex in RESULTS:
        by_service[service].append((source, None, bre, regex))
    for service, pats in by_service.items():
        files = _service_files(logs_dir, service)
        if not files:
            continue
        pending = None  # power_mon: the keep-alive waiting for its response line
        # The BRE prefilters are plain text (no BRE special characters)
        for ts, matched, line in iter_hits(files, [(bre, regex) for _, _, bre, regex in pats]):
            for i in matched:
                kind, api = pats[i][0], pats[i][1]
                if kind == 'call':
                    out.add((api, ts, 'call'))
                    if service == 'power_mon' and (pending is None or ts - pending > DUP_MS):
                        if pending is not None:
                            out.add((1, pending, 'pm:sent'))
                        pending = ts
                    continue
                res = _result(kind, line, ts)
                if res:
                    out.add(res)
                if kind == 'pm':
                    pending = None
        if pending is not None:
            out.add((1, pending, 'pm:sent'))
    return out


def by_api(calls):
    """{api id: sorted call timestamps} from scan_day sets merged over days, duplicates dropped."""
    result = defaultdict(list)
    for api, ts, kind in sorted(calls):
        if kind != 'call':
            continue
        stamps = result[api]
        if not stamps or ts - stamps[-1] > DUP_MS:
            stamps.append(ts)
    return dict(result)


def _near(stamps, ts, before, after):
    i = bisect.bisect_left(stamps, ts - before)
    return i < len(stamps) and stamps[i] <= ts + after


def results_by_api(calls):
    """{api id: {'ok': [ts], 'failed': [(ts, reason)], 'unconfirmed': [ts]}} (sorted) from scan_day
    sets merged over days: health stats first, curl_health lines for calls health has no record of,
    power_mon keep-alives with no response line unconfirmed (see the docstring)."""
    by = defaultdict(lambda: defaultdict(list))  # api -> source -> [(ts, outcome)]
    for api, ts, kind in sorted(calls):
        if kind == 'call':
            continue
        source, _, outcome = kind.partition(':')
        recs = by[api][source]
        # The same line in a day's plain and _c log
        if recs and ts - recs[-1][0] <= DUP_MS and recs[-1][1] == outcome:
            continue
        recs.append((ts, outcome))
    result = {}
    for api, sources in by.items():
        recs = list(sources.get('hs', ()))
        stats = sorted(t for t, _ in recs)
        recs += [(t, o) for t, o in sources.get('hc', ()) if not _near(stats, t, SAME_RESULT_MS, SAME_RESULT_MS)]
        pm = sources.get('pm', ())
        answered = sorted(t for t, o in pm if o != 'sent')
        recs += [(t, o) for t, o in pm if o != 'sent' or not _near(answered, t, 0, PM_RESPONSE_MS)]
        recs.sort()
        result[api] = {'ok': [t for t, o in recs if o == 'ok'],
                       'failed': [(t, o.partition(':')[2]) for t, o in recs if o.startswith('fail')],
                       'unconfirmed': [t for t, o in recs if o == 'sent']}
    return result


def boot_results(results, start, end):
    """{api id (str): {'ok', 'failed', 'unconfirmed': counts, 'reasons': {reason: failed calls}}}
    of the results (results_by_api) in [start, end)."""
    lo = float('-inf') if start is None else start
    hi = float('inf') if end is None else end
    out = {}
    for api, r in results.items():
        failed = r['failed'][bisect.bisect_left(r['failed'], (lo,)):bisect.bisect_left(r['failed'], (hi,))]
        counts = {k: bisect.bisect_left(r[k], hi) - bisect.bisect_left(r[k], lo) for k in ('ok', 'unconfirmed')}
        if counts['ok'] or counts['unconfirmed'] or failed:
            reasons = defaultdict(int)
            for _, reason in failed:
                reasons[reason] += 1
            out[str(api)] = dict(counts, failed=len(failed), reasons=dict(reasons))
    return out


def boot_calls(calls_by_api, start, end):
    """{api id (str, for json): call timestamps in [start, end)} for one boot."""
    lo = float('-inf') if start is None else start
    hi = float('inf') if end is None else end
    result = {}
    for api, stamps in calls_by_api.items():
        inside = stamps[bisect.bisect_left(stamps, lo):bisect.bisect_left(stamps, hi)]
        if inside:
            result[str(api)] = inside
    return result


def _gap_stats(stamps):
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    return {'calls': len(stamps), 'gaps': len(gaps), 'gap_sum': sum(gaps),
            'gap_min': min(gaps, default=None), 'gap_max': max(gaps, default=None)}


def _add(total, stats):
    total['calls'] += stats['calls']
    total['gaps'] += stats['gaps']
    total['gap_sum'] += stats['gap_sum']
    for key, pick in (('gap_min', min), ('gap_max', max)):
        if stats[key] is not None:
            total[key] = stats[key] if total[key] is None else pick(total[key], stats[key])


def _empty():
    return {'calls': 0, 'gaps': 0, 'gap_sum': 0, 'gap_min': None, 'gap_max': None}


def boot_stats(boot_api_calls):
    """{api id: calls + gaps between consecutive calls} for one boot's calls."""
    return {api: _gap_stats(stamps) for api, stamps in boot_api_calls.items()}


def _add_results(entry, res):
    """Adds a boot_results / summarize entry's ok, failed, unconfirmed and reasons to entry."""
    for k in ('ok', 'failed', 'unconfirmed'):
        entry[k] = entry.get(k, 0) + (res.get(k) or 0)
    reasons = entry.setdefault('reasons', {})
    for reason, n in (res.get('reasons') or {}).items():
        reasons[reason] = reasons.get(reason, 0) + n


def summarize(boots):
    """Device level: per API the calls and gaps over its boots (gaps never span two boots),
    the boots that had a call, and 'ok' / 'failed' / 'unconfirmed' calls and the failure
    'reasons' when result lines were found.
    boots: dicts with 'api_calls' (boot_calls) and 'api_results' (boot_results)."""
    result = {}
    for b in boots:
        for api, stats in boot_stats(b.get('api_calls') or {}).items():
            entry = result.setdefault(api, dict(_empty(), boots=0))
            _add(entry, stats)
            entry['boots'] += 1
        for api, res in (b.get('api_results') or {}).items():
            _add_results(result.setdefault(api, dict(_empty(), boots=0)), res)
    return {'boots': len(boots), 'apis': result}


def merge_summaries(summaries):
    """OTA level from device summaries: per API calls, gaps, boots and devices with a call."""
    result = {}
    boots = 0
    for s in summaries:
        boots += s['boots']
        for api, stats in s['apis'].items():
            entry = result.setdefault(api, dict(_empty(), boots=0, devices=0))
            _add(entry, stats)
            entry['boots'] += stats['boots']
            entry['devices'] += bool(stats['calls'])
            if 'ok' in stats:
                _add_results(entry, stats)
                entry['devices_with_results'] = entry.get('devices_with_results', 0) + 1
    return {'boots': boots, 'devices': len(summaries), 'apis': result}
