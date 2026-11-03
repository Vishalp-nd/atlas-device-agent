"""
Cloud override config of a device (IDMS) compared with the config the device ran with.

The cloud keeps a device's override config as a base64 INI in
GET <IDMS_API_URL>/tenants/devices/<device>  ->  {"response": true, "data": {"overrideconfig": ...}}
(login: OAuth password grant on <IDMS_AUTH_URL>/oauth/token, then a session key from /session).

The device's final config is built from its logs (ota_report.complete_device_reports): the OTA
defaults (bagheera_config.ini + nd_config.ini) with the values services printed at startup
(read by the LLM, override_llm) on top. Every cloud key is compared with it:
  - logged at startup: that value;
  - otherwise the OTA default: no override shows in the logs, so the device runs on it
    ('default_confirmed' when CFG_PRSR even logged the key as not in the override file);
  - unknown (not counted either way) when CFG_PRSR shows the key overridden but its value is
    not logged, or when the key is in neither the defaults nor the logs.
CFG_PRSR names keys without their section, so its lines only count for a key name that is
in one section (over the OTA defaults and the cloud config).

A device PASSES when no cloud key differs from its final config, FAILS when some key
differs, and is UNVERIFIED when no key could be compared.

Environment: ATLAS_ENV=stag (default) or prod, set by the Log Analyzer worker from the run's
environment; it picks the IDMS hosts (idms-staging / idms, auth-staging / auth).
Configuration (env or .env at the repo root): IDMS_USERNAME, IDMS_PASSWORD (production:
IDMS_PROD_USERNAME / IDMS_PROD_PASSWORD when set, else the same), optional IDMS_AUTH_URL /
IDMS_API_URL to point elsewhere. CLOUD_CONFIG=0 turns the check off; missing credentials do too.
"""

import base64
import configparser
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import feature_status

log = logging.getLogger('cloud_config')

REPO_ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTS = {
    'stag': {'label': 'Staging', 'auth_url': 'https://auth-staging.netradyne.com/authserver/api/v1',
             'api_url': 'https://idms-staging.netradyne.com/restserver/api/v1', 'prefix': 'IDMS_'},
    'prod': {'label': 'Production', 'auth_url': 'https://auth.netradyne.com/authserver/api/v1',
             'api_url': 'https://idms.netradyne.com/restserver/api/v1', 'prefix': 'IDMS_PROD_'},
}
TIMEOUT_S = 30
FETCH_WORKERS = 8
RETRIES = 3

# SKIPPED: no boot with detailed logs (critical-only uploads log no override information)
VERDICTS = ['PASS', 'FAIL', 'UNVERIFIED', 'SKIPPED', 'ERROR']
# Key statuses: same / different value, or the device value is unknown
MATCH, DIFF, UNKNOWN = 'match', 'diff', 'unknown'

# The device record fields shown with the result (which override config, when it was pushed)
DEVICE_FIELDS = ('config_override_version', 'current_config_override_version', 'config_override_update_time',
                 'config_override_update_status', 'current_merge_config_version',
                 'cloud_expected_merge_config_version', 'merge_config_update_time', 'merge_config_update_status')


def _load_env():
    if os.environ.get('IDMS_USERNAME'):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / '.env')
    except ImportError:
        pass


def environment():
    """'stag' or 'prod' (ATLAS_ENV; 'production' / 'staging' also accepted)."""
    env = os.environ.get('ATLAS_ENV', 'stag').strip().lower()
    return 'prod' if env.startswith('prod') else 'stag'


def _credentials(env):
    """(username, password) for the environment: IDMS_PROD_* for production when set, else IDMS_*."""
    _load_env()
    prefix = ENVIRONMENTS[env]['prefix']
    user, password = os.environ.get(f'{prefix}USERNAME', '').strip(), os.environ.get(f'{prefix}PASSWORD', '').strip()
    if not (user and password):
        user, password = os.environ.get('IDMS_USERNAME', '').strip(), os.environ.get('IDMS_PASSWORD', '').strip()
    return user, password


def enabled():
    if os.environ.get('CLOUD_CONFIG', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    return all(_credentials(environment()))


def parse_override(text):
    """{section: {key: value}} of an override INI. interpolation=None: values hold % and =
    (a wifi password ends in =); keys keep their case (LLA_alerts)."""
    cp = configparser.ConfigParser(interpolation=None, strict=False, allow_no_value=True,
                                   delimiters=('=',), comment_prefixes=(';', '#'))
    cp.optionxform = str
    cp.read_string(text)
    return {s: {k: ('' if v is None else v.strip().strip('"')) for k, v in cp[s].items()} for s in cp.sections()}


class Cloud:
    """IDMS session: login once, log in again when the token is refused."""

    def __init__(self, env=None, username=None, password=None):
        self.env = env or environment()
        user, pw = _credentials(self.env)
        self.username, self.password = username or user, password or pw
        self.auth_url = os.environ.get('IDMS_AUTH_URL', ENVIRONMENTS[self.env]['auth_url']).rstrip('/')
        self.api_url = os.environ.get('IDMS_API_URL', ENVIRONMENTS[self.env]['api_url']).rstrip('/')
        self._headers = None
        self._lock = threading.Lock()
        import requests
        self._http = requests.Session()

    def login(self):
        r = self._http.post(f'{self.auth_url}/oauth/token', timeout=TIMEOUT_S,
                            headers={'Content-Type': 'application/x-www-form-urlencoded'},
                            data=urlencode({'client_id': 'idms', 'grant_type': 'password',
                                            'username': self.username, 'password': self.password}))
        r.raise_for_status()
        token = r.json()['access_token']
        r = self._http.post(f'{self.auth_url}/session', timeout=TIMEOUT_S, headers={'Authorization': f'bearer {token}'})
        r.raise_for_status()
        self._headers = {'Authorization': f'bearer {token}', 'Session-Key': r.json()['session']['session_id'],
                         'Content-Type': 'application/json'}

    def _auth_headers(self, renew=False):
        with self._lock:
            if self._headers is None or renew:
                self.login()
            return self._headers

    def device(self, device):
        """The device's IDMS record: {'overrideconfig', 'device', ...} (data of the response)."""
        headers = self._auth_headers()
        for attempt in range(RETRIES):
            r = self._http.get(f'{self.api_url}/tenants/devices/{device}', headers=headers, timeout=TIMEOUT_S)
            if r.status_code in (401, 403) and attempt == 0:
                headers = self._auth_headers(renew=True)
                continue
            if r.status_code >= 500 and attempt + 1 < RETRIES:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            body = r.json()
            if not body.get('response') or not isinstance(body.get('data'), dict):
                raise ValueError(f'unexpected response: {str(body)[:200]}')
            return body['data']
        r.raise_for_status()


def fetch_one(cloud, device):
    """{'config': {section: {key: value}} | None, 'device': {...}, 'fetched_at', 'error'}."""
    fetched_at = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} ({ENVIRONMENTS[cloud.env]['label']} IDMS)"
    try:
        data = cloud.device(device)
        info = {k: (data.get('device') or {}).get(k) for k in DEVICE_FIELDS}
        encoded = data.get('overrideconfig')
        if not encoded:
            return {'config': None, 'device': info, 'fetched_at': fetched_at, 'error': 'No override config in the cloud'}
        text = base64.b64decode(encoded).decode('utf-8', 'replace')
        return {'config': parse_override(text), 'device': info, 'fetched_at': fetched_at, 'error': None}
    except Exception as exc:  # one device's failure does not stop the report
        log.warning('%s: cloud override config not fetched: %s', device, exc)
        return {'config': None, 'device': None, 'fetched_at': fetched_at,
                'error': f'Cloud fetch failed: {type(exc).__name__}: {exc}'[:300]}


def fetch_all(devices, workers=FETCH_WORKERS):
    """{device: fetch_one result}; {} when the check is off (see enabled())."""
    devices = sorted(set(devices))
    if not devices or not enabled():
        return {}
    cloud = Cloud()
    try:
        cloud.login()
    except Exception as exc:
        log.warning('Cloud login (%s) failed, override configs not compared: %s', cloud.auth_url, exc)
        error = f'Cloud login failed: {type(exc).__name__}: {exc}'[:300]
        return {d: {'config': None, 'device': None, 'fetched_at': None, 'error': error} for d in devices}
    with ThreadPoolExecutor(min(workers, len(devices))) as pool:
        results = dict(zip(devices, pool.map(lambda d: fetch_one(cloud, d), devices)))
    log.info('Cloud override configs (%s): %d fetched, %d failed', cloud.api_url, sum(1 for r in results.values() if r['config']),
             sum(1 for r in results.values() if not r['config']))
    return results


# ── comparison ───────────────────────────────────────────────────────────

def compare(cloud, defaults, logged_values, not_overridden, overridden):
    """Each cloud key against what one boot ran with.

    cloud: {section: {key: value}}; defaults: the OTA's {section: {key: value}} (or None);
    logged_values: {section.key: {'value', 'evidence'}} (override_llm); not_overridden: key
    names CFG_PRSR listed as not in the override dictionary; overridden: key names taken as
    overridden (feature_status.boot_overrides).
    Returns {'keys': [[section, key, cloud, device, status, source, evidence]], 'counts'}.
    source: 'log' (printed at startup), 'default_confirmed' (CFG_PRSR: not overridden),
    'default' (no override in the logs), or for an unknown device value 'overridden' (CFG_PRSR:
    overridden, value not logged) / 'missing' (in neither the defaults nor the logs).
    """
    defaults = defaults or {}
    sections_of = {}
    for config in (defaults, cloud):
        for section, keys in config.items():
            for key in keys:
                sections_of.setdefault(key, set()).add(section)
    rows, counts = [], {MATCH: 0, DIFF: 0, UNKNOWN: 0}
    for section in sorted(cloud):
        for key in sorted(cloud[section]):
            want = cloud[section][key]
            logged = logged_values.get(f'{section}.{key}')
            default = defaults.get(section, {}).get(key)
            one_section = sections_of.get(key) == {section}
            device = evidence = None
            if logged is not None:
                device, source, evidence = logged['value'], 'log', logged.get('evidence')
            elif key in overridden and one_section:
                source = 'overridden'
            elif default is not None:
                device, source = default, 'default_confirmed' if key in not_overridden and one_section else 'default'
            else:
                source = 'missing'
            if device is None:
                status = UNKNOWN
            else:
                status = MATCH if feature_status.same_value(device, want) else DIFF
            counts[status] += 1
            rows.append([section, key, want, device, status, source, evidence])
    return {'keys': rows, 'counts': counts}


def verdict(counts):
    if counts[DIFF]:
        return 'FAIL'
    return 'PASS' if counts[MATCH] else 'UNVERIFIED'
