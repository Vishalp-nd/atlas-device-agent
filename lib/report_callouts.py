"""
Callouts for an OTA report: "Callouts-API Summary" (below the API Call Summary) and
"Callouts-KPI Metrics" (after the KPI Summary).

KPI callouts (build_kpi): only the KPIs that missed their baseline (lib/kpi_metrics.BASELINES,
the amber cells): the OTA P90 and every device value outside the range, one LLM call per KPI
table run like the API ones, the model reviewing them as a senior QA engineer (bullets: how
far off, across all devices or a few, devices with more issues, trends).

The rule checks (API_RULES below) run here over the report's per-device drive time, API call
counts and enabled features, so every number is exact. The LLM (the Azure OpenAI deployment
override_llm uses) then words the findings: one call per API that has findings, run in
parallel, each with all of that API's affected devices (no cap). Each callout lists every
affected device from the checks, not from the model's answer.

Answers are cached on disk by prompt hash (same findings -> no new call).
REPORT_CALLOUTS=0 turns the calls off; so does a missing LLM configuration.
"""

import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import api_calls
import kpi_metrics
import override_llm

log = logging.getLogger('report_callouts')

CACHE_DIR = Path(os.environ.get('REPORT_CALLOUTS_CACHE',
                                override_llm.REPO_ROOT / 'OUTPUT' / 'llm_cache' / 'report_callouts'))
PROMPT_VERSION = 'v4'
LLM_PARALLEL = int(os.environ.get('REPORT_CALLOUTS_PARALLEL', '6'))
LEVELS = ('critical', 'warning', 'info')
# Level of each finding kind
KIND_LEVEL = {'zero': 'critical', 'far_below': 'critical', 'no_status_after_ota_change': 'critical',
              'no_event': 'critical', 'no_video_with_events': 'critical', 'zero_with_drive': 'critical',
              'below': 'warning', 'far_above': 'warning', 'gap_over_limit': 'warning'}

# Calls of these APIs are expected at least once per this many drive minutes
# (Download OTA has no rate: it only runs when there is an OTA to download)
PER_DRIVE_MINUTES = {1: 10, 2: 10, 8: 5, 7: 10, 10: 10}
# Far below / above the expected minimum ("huge difference")
FAR_BELOW = 0.5
FAR_ABOVE = 3.0
# Upload Observation: one call per observation_frequency (in [upload_settings]) drive minutes
OBSERVATION_KEY = ('upload_settings', 'observation_frequency')
# Feature names (feature_mapping.ini) the LLA / Driver Login checks depend on
LLA_FEATURE = 'LLA'
DRIVER_LOGIN_FEATURE = 'driverlogin_v2'
GAP_LIMIT_MIN = 30
EVENT_MIN_DRIVE = 30

RULES = f"""\
How the findings were worked out (drive_min = the device's drive minutes on this OTA):
- Keep Alive, Version Check, Upload Logs, Upload Device Status: at least 1 call per 10 drive minutes
  (expected_min = floor(drive_min / 10)); Upload Video list: 1 per 5 drive minutes; Upload Observation data:
  1 per observation_frequency drive minutes from the OTA config. Kinds: zero (0 calls), far_below
  (< {FAR_BELOW:.0%} of expected_min), below (under expected_min), far_above (> {FAR_ABOVE:g}x expected_min).
- Download OTA: not checked on its own; it only runs when there is an OTA to download, so 0 calls is normal.
- Download Status: no_status_after_ota_change (the device was also seen on another OTA, so Download OTA brought
  a new version, but Download Status was never called).
- Upload Event: no_event (drive_min > {EVENT_MIN_DRIVE} and 0 calls).
- Upload Video: no_video_with_events (Upload Event calls but no Upload Video call).
- Upload LLA (LLA feature enabled) / Driver Login (driverlogin_v2 enabled): zero_with_drive (0 calls,
  drive_min > {GAP_LIMIT_MIN}) or gap_over_limit (longest gap between calls > {GAP_LIMIT_MIN} min).
- Can Diagnostics: not checked (no call line in the logs)."""

QA_ROLE = """You are a senior QA engineer reviewing an OTA (firmware) test of telematics devices: you look at
where the devices deviate from what is expected, judge how serious it is, whether it is seen across all devices
or only on a few, which devices stand out, and what trend it points to, and you write the callouts the team
should act on."""

SYSTEM_PROMPT = QA_ROLE + """
You write the callouts for one API of the OTA report.
The rule checks have already been done: you get the API's findings grouped by kind, each with every affected
device and its numbers, plus context. Only use the given numbers; never recompute, invent devices or add problems.
For each kind write one callout: how many of the devices on the OTA are affected and how bad it is (range or
typical actual vs expected, the worst 2-3 devices with their numbers). The full device list is shown next to the
callout, so do not list every device. Plain, specific, one or two sentences.
Answer with JSON only: {{"callouts": [{{"kind": "<kind as given>", "text": "<callout>"}}]}}"""

HUMAN_PROMPT = """OTA {ota}, API {api}

{rules}

Context:
{context}

Findings for {api}:
{findings}"""

KPI_SYSTEM_PROMPT = QA_ROLE + """
You write the callouts for one KPI table of the OTA report. You only get the KPIs of that table that missed their
baseline: for each, the expected value / range, the OTA's P90 over the devices (p90_outside: whether the P90
itself is outside) and every device whose value is outside, with its value (devices_with_value: how many devices
have the KPI at all). The context gives the devices on the OTA and, per device, how many KPIs it missed in all
tables. Only use the given numbers; never recompute, invent devices or values, or add problems.
Write at most 6 callouts, most important first, each one or two plain, specific sentences: what deviates and by
how much, how widespread it is, the devices that stand out (worst values, or missing many KPIs), and any trend
across the table's KPIs (e.g. every boot step late, one camera only). Judge how widespread it is from
outside_count of devices_with_value ("all 3 devices", "2 of 5 devices"), not from the P90 alone: with few devices
the P90 follows the worst one. Mention a device's overall number of missed KPIs at most once, where it matters. Level: critical (seen across most devices, or a hard failure such as crashes / watchdog resets),
warning (a few devices or a small deviation), info (borderline).
Answer with JSON only: {{"callouts": [{{"level": "critical|warning|info", "text": "<callout>",
"devices": ["<ids of the devices the callout is about, from the input>"]}}]}}"""

KPI_HUMAN_PROMPT = """OTA {ota} ({device_type}), KPI table: {table}

Context:
{context}

KPIs outside their baseline:
{rows}"""

# KPI units not shown after the name (as in the report)
NO_UNIT = {'count', 'WAF', 'text'}

_chain = None
_kpi_chain = None
_chain_lock = threading.Lock()


def enabled():
    if os.environ.get('REPORT_CALLOUTS', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    override_llm._load_env()
    return all(os.environ.get(k, '').strip() for k in ('AZURE_OAI', 'OAI_ENDPOINT', 'OAI_MODEL'))


def _llm():
    from langchain_openai import AzureChatOpenAI

    override_llm._load_env()
    return AzureChatOpenAI(
        azure_deployment=os.environ['OAI_MODEL'].strip(),
        azure_endpoint=os.environ['OAI_ENDPOINT'].strip(),
        api_key=os.environ['AZURE_OAI'].strip(),
        api_version=os.environ.get('OAI_API_VERSION', '2024-08-01-preview').strip(),
        max_retries=int(os.environ.get('OVERRIDE_LLM_RETRIES', '6')),
        timeout=float(os.environ.get('REPORT_CALLOUTS_TIMEOUT', '180')),
    )


def _get_chain(kpi=False):
    """The API callouts chain, or the KPI callouts one (same model, retries and timeout)."""
    global _chain, _kpi_chain
    with _chain_lock:
        if (_kpi_chain if kpi else _chain) is None:
            from langchain_core.output_parsers import JsonOutputParser
            from langchain_core.prompts import ChatPromptTemplate

            system, human = (KPI_SYSTEM_PROMPT, KPI_HUMAN_PROMPT) if kpi else (SYSTEM_PROMPT, HUMAN_PROMPT)
            chain = ChatPromptTemplate.from_messages([('system', system), ('human', human)]) | _llm() | JsonOutputParser()
            if kpi:
                _kpi_chain = chain
            else:
                _chain = chain
    return _kpi_chain if kpi else _chain


def _number(value):
    try:
        return float(str(value).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def _name(api):
    return api_calls.APIS[api][0]


def callout_data(summary, device_summaries, device_info, defaults):
    """The rule checks per device -> {'context': ..., 'findings': {api: {kind: [{device, ...numbers}]}}}."""
    minutes = summary.get('drive_minutes_by_device') or {}
    calls_by_device = summary.get('api_calls_by_device') or {}
    observation = _number((defaults or {}).get(OBSERVATION_KEY[0], {}).get(OBSERVATION_KEY[1]))
    per_minutes = dict(PER_DRIVE_MINUTES)
    if observation and observation > 0:
        per_minutes[16] = observation
    findings = []
    lla_on = login_on = 0

    def add(api, kind, device, **numbers):
        findings.append(dict(api=_name(api), kind=kind, device=device, **numbers))

    for d in sorted(minutes, key=lambda d: -minutes[d]):
        drive = minutes[d]
        calls = {int(k): v for k, v in (calls_by_device.get(d) or {}).items()}
        stats = {int(k): v for k, v in ((device_summaries.get(d) or {}).get('api') or {}).get('apis', {}).items()}
        features = (device_summaries.get(d) or {}).get('feature_set') or []
        for api, per in per_minutes.items():
            expected = int(drive // per)
            n = calls.get(api, 0)
            if not expected:
                continue
            kind = ('zero' if n == 0 else 'far_below' if n < FAR_BELOW * expected else 'below' if n < expected
                    else 'far_above' if n > FAR_ABOVE * expected else None)
            if kind:
                add(api, kind, d, calls=n, expected_min=expected, drive_min=drive)
        other = [o for o in (device_info.get(d) or {}).get('otas', []) if o != summary.get('ota')]
        if other and not calls.get(6):
            add(6, 'no_status_after_ota_change', d, other_otas=other, download_ota_calls=calls.get(5, 0))
        if drive > EVENT_MIN_DRIVE and not calls.get(3):
            add(3, 'no_event', d, calls=0, drive_min=drive)
        if calls.get(3) and not calls.get(9):
            add(9, 'no_video_with_events', d, calls=0, event_calls=calls[3])
        for api, feature in ((19, LLA_FEATURE), (13, DRIVER_LOGIN_FEATURE)):
            if feature not in features:
                continue
            if api == 19:
                lla_on += 1
            else:
                login_on += 1
            gap = (stats.get(api) or {}).get('gap_max')
            if not calls.get(api) and drive > GAP_LIMIT_MIN:
                add(api, 'zero_with_drive', d, calls=0, drive_min=drive)
            elif gap is not None and gap > GAP_LIMIT_MIN * 60000:
                add(api, 'gap_over_limit', d, calls=calls.get(api, 0), max_gap_min=round(gap / 60000, 1))
    groups = {}
    for f in findings:
        groups.setdefault(f.pop('api'), {}).setdefault(f.pop('kind'), []).append(f)
    for kinds in groups.values():
        for items in kinds.values():
            # worst first: furthest from the expected count, else the longest gap / drive
            items.sort(key=lambda f: -(abs((f.get('expected_min') or 0) - (f.get('calls') or 0))
                                       or f.get('max_gap_min') or f.get('drive_min') or 0))
    return {
        'context': {
            'devices': len(minutes),
            'drive_min_total': sum(minutes.values()),
            'observation_frequency_min': observation,
            'devices_with_lla_enabled': lla_on,
            'devices_with_driverlogin_v2_enabled': login_on,
            'features_enabled_on_ota': [f['name'] for f in summary.get('feature_coverage') or [] if f['devices_on']],
        },
        'findings': groups,  # {api: {kind: [finding, ...]}}
    }


def _cached_invoke(variables, kpi=False):
    """(answer, source) for one prompt, from the disk cache or the LLM."""
    model = os.environ.get('OAI_MODEL', '').strip()
    system = KPI_SYSTEM_PROMPT if kpi else SYSTEM_PROMPT
    digest = hashlib.sha256(json.dumps([PROMPT_VERSION, model, system, variables],
                                       sort_keys=True).encode()).hexdigest()
    cache_path = CACHE_DIR / digest[:2] / f'{digest}.json'
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), 'cache'
        except ValueError:
            pass
    raw = _get_chain(kpi).invoke(variables)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_text(json.dumps(raw))
    tmp.replace(cache_path)
    return raw, 'llm'


def _api_callouts(ota, api, kinds, context):
    """Callouts for one API: the model's text per kind, the level and devices from the checks.
    A failed call still gives one callout per kind, with a plain text."""
    variables = {'ota': ota, 'api': api, 'rules': RULES,
                 'context': json.dumps(context, separators=(',', ':')),
                 'findings': json.dumps(kinds, separators=(',', ':'))}
    texts, source, error = {}, None, None
    try:
        raw, source = _cached_invoke(variables)
        for item in (raw.get('callouts') if isinstance(raw, dict) else None) or []:
            if isinstance(item, dict) and item.get('kind') in kinds and str(item.get('text', '')).strip():
                texts.setdefault(item['kind'], str(item['text']).strip()[:800])
    except Exception as exc:
        log.exception('%s: callouts LLM call for %s failed: %s', ota, api, exc)
        error = f'{type(exc).__name__}'
    callouts = []
    for kind, items in kinds.items():
        text = texts.get(kind) or (f'{len(items)} of {context["devices"]} devices: '
                                   f'{kind.replace("_", " ")}' + (' (LLM text unavailable)' if error else ''))
        callouts.append({'level': KIND_LEVEL.get(kind, 'info'), 'api': api, 'kind': kind, 'text': text,
                         'devices': [f['device'] for f in items]})
    return callouts, source, error


def build(ota, summary, device_summaries, device_info, defaults):
    """{'callouts': [{'level', 'api', 'kind', 'text', 'devices'}], 'llm_calls', 'cached', 'error'}.
    The checks always run; without the LLM the callouts get a plain text (error says why)."""
    if not summary.get('drive_minutes_by_device'):
        return {'callouts': [], 'llm_calls': 0, 'cached': 0, 'error': 'No devices with drive data on this OTA'}
    data = callout_data(dict(summary, ota=ota), device_summaries, device_info, defaults)
    context, findings = data['context'], data['findings']
    if not findings:
        return {'callouts': [{'level': 'info', 'api': 'General', 'kind': 'ok', 'devices': [],
                              'text': 'No anomaly noticed.'}],
                'llm_calls': 0, 'cached': 0, 'error': None}
    use_llm = enabled()
    results = []
    if use_llm:
        with ThreadPoolExecutor(min(LLM_PARALLEL, len(findings))) as pool:
            results = list(pool.map(lambda kv: _api_callouts(ota, kv[0], kv[1], context), findings.items()))
    else:
        for api, kinds in findings.items():
            results.append(([{'level': KIND_LEVEL.get(k, 'info'), 'api': api, 'kind': k,
                              'devices': [f['device'] for f in items],
                              'text': f'{len(items)} of {context["devices"]} devices: {k.replace("_", " ")}'}
                             for k, items in kinds.items()], None, None))
    callouts = [c for cs, _, _ in results for c in cs]
    callouts.sort(key=lambda c: (LEVELS.index(c['level']), -len(c['devices'])))
    errors = sorted({e for _, _, e in results if e})
    return {
        'callouts': callouts,
        'llm_calls': sum(1 for _, src, _ in results if src == 'llm'),
        'cached': sum(1 for _, src, _ in results if src == 'cache'),
        'error': (None if use_llm else 'LLM not configured (REPORT_CALLOUTS / Azure credentials): plain texts shown')
                 if not errors else f'LLM call failed for some APIs ({", ".join(errors)}): plain texts shown',
    }


# ── KPI callouts ─────────────────────────────────────────────────────────────────────────

def _deviation(metric_id, value, device_type):
    """How far a value is outside its baseline, relative (for ordering the devices)."""
    b = kpi_metrics.BASELINES.get(metric_id)
    if b is None or not isinstance(value, (int, float)):
        return 0
    if b.expect:
        return abs(value - b.expect[0]) / (b.expect[0] or 1)
    override = (b.by_family or {}).get(device_type, (b.by_family or {}).get(kpi_metrics.family(device_type)))
    lo, hi = override if isinstance(override, tuple) else (b.lo, b.hi)
    if hi is not None and value > hi:
        return (value - hi) / (abs(hi) or 1)
    if lo is not None and value < lo:
        return (lo - value) / (abs(lo) or 1)
    return 0


def kpi_callout_data(report_kpi, device_summaries, device_type):
    """Only the amber rows: {'context': ..., 'tables': {table: [row]}}; a row is a KPI outside its
    baseline at the OTA P90 or on some device: expected, p90, p90_outside, devices_with_value
    and every device outside it with its value (furthest off first)."""
    columns = (report_kpi or {}).get('columns') or {}
    values = {d: ((s or {}).get('kpi') or {}).get('metrics') or {} for d, s in device_summaries.items()}
    tables, missed = {}, {}
    for table in kpi_metrics.KPI_TABLES:
        rows = []
        for m in table.metrics:
            if m.id not in kpi_metrics.BASELINES:
                continue
            col = columns.get(m.id) or {}
            p90 = col.get('common') if m.unit == 'text' else col.get('p90')
            p90_out = kpi_metrics.baseline_issue(m.id, p90, device_type) is not None
            devices = [(d, v[m.id][0]) for d, v in values.items()
                       if m.id in v and kpi_metrics.baseline_issue(m.id, v[m.id][0], device_type)]
            if not p90_out and not devices:
                continue
            for d, _ in devices:
                missed[d] = missed.get(d, 0) + 1
            devices.sort(key=lambda dv: -_deviation(m.id, dv[1], device_type))
            unit = '' if m.unit in NO_UNIT else f' ({m.unit})'
            rows.append({'kpi': m.name + unit, 'expected': kpi_metrics.baseline_text(m.id, device_type),
                         'p90': _round(p90), 'p90_outside': p90_out,
                         'devices_with_value': sum(1 for v in values.values() if m.id in v),
                         'outside_count': len(devices),
                         'devices_outside': [{'device': d, 'value': _round(v)} for d, v in devices]})
        if rows:
            tables[table.title] = rows
    return {'context': {'devices_on_ota': len(device_summaries),
                        'kpis_missed_per_device': dict(sorted(missed.items(), key=lambda kv: -kv[1]))},
            'tables': tables}


def _round(v):
    return round(v, 2) if isinstance(v, float) else v


def _kpi_table_callouts(ota, device_type, table, rows, context):
    """Callouts for one KPI table: the model's bullets (devices kept only if they are in the
    input). A failed call gives a plain bullet per KPI."""
    variables = {'ota': ota, 'device_type': device_type or 'unknown product', 'table': table,
                 'context': json.dumps(context, separators=(',', ':')),
                 'rows': json.dumps(rows, separators=(',', ':'))}
    known = {x['device'] for r in rows for x in r['devices_outside']}
    callouts, source, error = [], None, None
    try:
        raw, source = _cached_invoke(variables, kpi=True)
        for item in ((raw.get('callouts') if isinstance(raw, dict) else None) or [])[:6]:
            text = str((item or {}).get('text', '')).strip() if isinstance(item, dict) else ''
            if not text:
                continue
            level = item.get('level') if item.get('level') in LEVELS else 'warning'
            devices = [d for d in dict.fromkeys(str(x) for x in item.get('devices') or []) if d in known]
            callouts.append({'level': level, 'api': table, 'kind': 'kpi', 'text': text[:800], 'devices': devices})
    except Exception as exc:
        log.exception('%s: KPI callouts LLM call for %s failed: %s', ota, table, exc)
        error = f'{type(exc).__name__}'
    if not callouts:
        callouts = _plain_kpi_callouts(table, rows, context, error)
    return callouts, source, error


def _plain_kpi_callouts(table, rows, context, error=None):
    out = []
    for r in rows:
        n = len(r['devices_outside'])
        text = (f'{r["kpi"]}: {n} of {r["devices_with_value"]} devices outside {r["expected"]}'
                + (f', P90 {r["p90"]}' if r['p90_outside'] else '') + (' (LLM text unavailable)' if error else ''))
        out.append({'level': 'critical' if r['p90_outside'] else 'warning', 'api': table, 'kind': 'kpi',
                    'text': text, 'devices': [x['device'] for x in r['devices_outside']]})
    return out


def build_kpi(ota, report_kpi, device_summaries, device_type):
    """{'callouts': [{'level', 'api' (the KPI table), 'kind', 'text', 'devices'}], 'llm_calls', 'cached',
    'error'}: the KPI callouts, from the amber rows only. One call per KPI table with amber rows,
    in parallel, as the API callouts."""
    if not report_kpi:
        return {'callouts': [], 'llm_calls': 0, 'cached': 0, 'error': 'No KPI data on this OTA'}
    data = kpi_callout_data(report_kpi, device_summaries, device_type)
    context, tables = data['context'], data['tables']
    if not tables:
        return {'callouts': [{'level': 'info', 'api': 'General', 'kind': 'ok', 'devices': [],
                              'text': 'All KPIs are within their baselines.'}],
                'llm_calls': 0, 'cached': 0, 'error': None}
    use_llm = enabled()
    if use_llm:
        with ThreadPoolExecutor(min(LLM_PARALLEL, len(tables))) as pool:
            results = list(pool.map(lambda kv: _kpi_table_callouts(ota, device_type, kv[0], kv[1], context),
                                    tables.items()))
    else:
        results = [(_plain_kpi_callouts(t, rows, context), None, None) for t, rows in tables.items()]
    callouts = [c for cs, _, _ in results for c in cs]
    order = [t.title for t in kpi_metrics.KPI_TABLES]
    callouts.sort(key=lambda c: (LEVELS.index(c['level']), order.index(c['api']) if c['api'] in order else 99))
    errors = sorted({e for _, _, e in results if e})
    return {
        'callouts': callouts,
        'llm_calls': sum(1 for _, src, _ in results if src == 'llm'),
        'cached': sum(1 for _, src, _ in results if src == 'cache'),
        'error': (None if use_llm else 'LLM not configured (REPORT_CALLOUTS / Azure credentials): plain texts shown')
                 if not errors else f'LLM call failed for some KPI tables ({", ".join(errors)}): plain texts shown',
    }
