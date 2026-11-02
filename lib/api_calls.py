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
The "command: wget ..." line otacheck and keep_alive_manager print after a call echoes the
same request, so it is not counted. The DIAG REQ_CURL_HEALTH_REPORT / CURL_HC lines are not
used: they are missing for most calls when only critical (_c) logs are uploaded.
APIs without a pattern were not seen being called in the logs these were taken from.

Python service logs (keep_alive_manager, health, btfv, ...) have no _c copy with these
lines, so devices that upload critical logs only show little more than otacheck's calls.
"""

import bisect
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
]
WITH_PATTERN = sorted({api for api, *_ in PATTERNS})

# Calls of one API this close together are one call logged twice (a day's plain and _c
# log, or power_mon's command and wget output lines)
DUP_MS = 1000


def _service_files(logs_dir, service):
    return sorted(p for p in logs_dir.glob(f'{service}*.log*') if p.is_file())


def scan_day(logs_dir, iter_hits):
    """{(api id, ts)} of the calls in one day's logs directory.

    iter_hits(files, [(bre, regex)]) yields (ts, [pattern index, ...], line), untimed
    lines taking the time of the line before them (ota_report.iter_service_hits).
    """
    calls = set()
    by_service = defaultdict(list)
    for api, service, bre, regex in PATTERNS:
        by_service[service].append((api, bre, regex))
    for service, pats in by_service.items():
        files = _service_files(logs_dir, service)
        if not files:
            continue
        # The BRE prefilters are plain text (no BRE special characters)
        for ts, matched, _ in iter_hits(files, [(bre, regex) for _, bre, regex in pats]):
            for i in matched:
                calls.add((pats[i][0], ts))
    return calls


def by_api(calls):
    """{api id: sorted call timestamps} from scan_day sets merged over days, duplicates dropped."""
    result = defaultdict(list)
    for api, ts in sorted(calls):
        stamps = result[api]
        if not stamps or ts - stamps[-1] > DUP_MS:
            stamps.append(ts)
    return dict(result)


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


def summarize(boots):
    """Device level: per API the calls and gaps over its boots (gaps never span two boots),
    and the boots that had a call. boots: dicts with 'api_calls' (boot_calls)."""
    result = {}
    for b in boots:
        for api, stats in boot_stats(b.get('api_calls') or {}).items():
            entry = result.setdefault(api, dict(_empty(), boots=0))
            _add(entry, stats)
            entry['boots'] += 1
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
            entry['devices'] += 1
    return {'boots': boots, 'devices': len(summaries), 'apis': result}
