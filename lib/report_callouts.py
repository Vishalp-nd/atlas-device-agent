"""
Callouts for an OTA report, shown above the Feature Set Summary.

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
import override_llm

log = logging.getLogger('report_callouts')

CACHE_DIR = Path(os.environ.get('REPORT_CALLOUTS_CACHE',
                                override_llm.REPO_ROOT / 'OUTPUT' / 'llm_cache' / 'report_callouts'))
PROMPT_VERSION = 'v3'
LLM_PARALLEL = int(os.environ.get('REPORT_CALLOUTS_PARALLEL', '6'))
LEVELS = ('critical', 'warning', 'info')
# Level of each finding kind
KIND_LEVEL = {'zero': 'critical', 'far_below': 'critical', 'no_status_after_ota_change': 'critical',
              'no_event': 'critical', 'no_video_with_events': 'critical', 'zero_with_drive': 'critical',
              'below': 'warning', 'far_above': 'warning', 'gap_over_limit': 'warning'}

# Calls of these APIs are expected at least once per this many drive minutes
PER_DRIVE_MINUTES = {1: 10, 2: 10, 8: 5, 7: 10, 5: 10, 10: 10}
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
- Keep Alive, Version Check, Upload Logs, Download OTA, Upload Device Status: at least 1 call per 10 drive minutes
  (expected_min = floor(drive_min / 10)); Upload Video list: 1 per 5 drive minutes; Upload Observation data:
  1 per observation_frequency drive minutes from the OTA config. Kinds: zero (0 calls), far_below
  (< {FAR_BELOW:.0%} of expected_min), below (under expected_min), far_above (> {FAR_ABOVE:g}x expected_min).
- Download Status: no_status_after_ota_change (the device was also seen on another OTA, so Download OTA brought
  a new version, but Download Status was never called).
- Upload Event: no_event (drive_min > {EVENT_MIN_DRIVE} and 0 calls).
- Upload Video: no_video_with_events (Upload Event calls but no Upload Video call).
- Upload LLA (LLA feature enabled) / Driver Login (driverlogin_v2 enabled): zero_with_drive (0 calls,
  drive_min > {GAP_LIMIT_MIN}) or gap_over_limit (longest gap between calls > {GAP_LIMIT_MIN} min).
- Can Diagnostics: not checked (no call line in the logs)."""

SYSTEM_PROMPT = """You write callouts for an OTA (firmware) test report of telematics devices, for one API.
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

_chain = None
_chain_lock = threading.Lock()


def enabled():
    if os.environ.get('REPORT_CALLOUTS', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    override_llm._load_env()
    return all(os.environ.get(k, '').strip() for k in ('AZURE_OAI', 'OAI_ENDPOINT', 'OAI_MODEL'))


def _get_chain():
    global _chain
    with _chain_lock:
        if _chain is None:
            from langchain_core.output_parsers import JsonOutputParser
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_openai import AzureChatOpenAI

            override_llm._load_env()
            llm = AzureChatOpenAI(
                azure_deployment=os.environ['OAI_MODEL'].strip(),
                azure_endpoint=os.environ['OAI_ENDPOINT'].strip(),
                api_key=os.environ['AZURE_OAI'].strip(),
                api_version=os.environ.get('OAI_API_VERSION', '2024-08-01-preview').strip(),
                max_retries=int(os.environ.get('OVERRIDE_LLM_RETRIES', '6')),
                timeout=float(os.environ.get('REPORT_CALLOUTS_TIMEOUT', '180')),
            )
            prompt = ChatPromptTemplate.from_messages([('system', SYSTEM_PROMPT), ('human', HUMAN_PROMPT)])
            _chain = prompt | llm | JsonOutputParser()
    return _chain


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


def _cached_invoke(variables):
    """(answer, source) for one prompt, from the disk cache or the LLM."""
    model = os.environ.get('OAI_MODEL', '').strip()
    digest = hashlib.sha256(json.dumps([PROMPT_VERSION, model, SYSTEM_PROMPT, variables],
                                       sort_keys=True).encode()).hexdigest()
    cache_path = CACHE_DIR / digest[:2] / f'{digest}.json'
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), 'cache'
        except ValueError:
            pass
    raw = _get_chain().invoke(variables)
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
                              'text': f'Every checked API met the rules on all {context["devices"]} devices.'}],
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
