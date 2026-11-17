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

Overall callouts (device_profiles + one more LLM call, in parallel with the API ones): across APIs
and devices rather than per API. Each device with an issue gets a profile from the report (API
findings, failing test cases, failing config keys, KPIs outside their baseline, cloud config
verdict, plus drive / low-power / shutdown facts); the code ranks the devices, counts which
issues occur together on the same devices and how concentrated the issues are, and the model
words what stands out: the devices with the most issues, issues that move together (and what
they point to), patterns over drive time, low power or features. They come first, as "Overall".

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
# Health Central API (when the service is enabled): one call per upload interval, the interval the
# service logged (kpi_metrics.health_central_boots: [health_central] upload_max_pending_interval_secs
# from the config default or a cloud override, or the server's payloadFrequencyMinutes); each gap
# between two calls is judged against the interval in effect when it started, over the limit when
# longer than HC_GAP_FACTOR intervals
HEALTH_CENTRAL_API = 28
HC_GAP_FACTOR = 1.5

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
- HEALTH_CENTRAL (boots where the service was enabled): 1 call per upload interval of drive time, the
  interval the device logged (upload_interval_s; interval_source: "config default" = [health_central]
  upload_max_pending_interval_secs of bagheera_config.ini, "server" = payloadFrequencyMinutes of the upload reply,
  anything else = the source the line names, e.g. a cloud override). expected_min = the sum over those boots of
  floor(drive_min while enabled / that boot's interval in minutes). Kinds: zero, far_below, below as above (more
  calls are normal: an upload at every shutdown and of what earlier boots left), and gap_over_limit (the gap
  between two calls in a boot, measured against the interval in effect when it started, > {HC_GAP_FACTOR:g} x it).
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

OVERALL_SYSTEM_PROMPT = QA_ROLE + """
You write the overall callouts of the OTA report's API Call Summary. Each API gets its own callouts separately; yours
look across APIs and devices. You get, all computed exactly from the report:
- api_issue_totals: per API and finding kind (see the rules), how many devices have it and its level;
- devices: a profile of the devices with issues, the most affected first: their API findings, failing test cases,
  failing config keys, KPIs outside their baseline, cloud config verdict, and facts (drive minutes, boots,
  low-power boots, previous shutdown reasons ...); multi_issue: issues in several areas or APIs;
- issues_together: pairs of issues found on the same devices, with how many devices have both;
- concentration: how many devices have any issue and what share of all issues the most affected devices carry.
Write at most {max_callouts} callouts, most important first, each one or two plain, specific sentences:
1. First, the devices with the most issues (multi_issue): name the worst 2-4 with what each has (e.g. "103182502310:
   no Keep Alive or Version Check calls, 12 failing test cases, 4 KPIs missed"). If no device is multi_issue, say
   the issues are spread out instead.
2. Then patterns that a per-API look misses: issues that come together on the same devices and what they point to
   (e.g. every periodic API missing on the same devices: those devices were offline or lost connectivity rather
   than an API bug; uploads missing with events present: upload path), whether the issues sit on a few devices or
   across the fleet, and links to drive time, low power, shutdown reasons or enabled features when the numbers show it.
   With fewer than 3 devices on the OTA, say nothing about concentration or spread (it says nothing then).
Do not repeat a single API's finding on its own (those callouts exist already). Only use the given numbers and
devices; never recompute, invent devices, values or causes the data does not support. Level: critical (a device or
pattern that blocks the release: many issues on one device, or one pattern across most devices), warning (a few
devices or a partial pattern), info (worth knowing).
Answer with JSON only: {{"callouts": [{{"level": "critical|warning|info", "text": "<callout>",
"devices": ["<ids of the devices the callout is about, from the input>"]}}]}}"""

OVERALL_HUMAN_PROMPT = """OTA {ota}

{rules}

Context:
{context}

API issues over all devices:
{api_issue_totals}

Devices with issues (most affected first):
{devices}

Issues found together on the same devices:
{issues_together}

Concentration:
{concentration}"""

COMPARE_SYSTEM_PROMPT = QA_ROLE + """
Now you compare two reports, A and B (two OTA versions, or the same device on two builds / periods), for one
section of the comparison report. Every row is already normalised so the two sides can be compared directly:
counts are per drive hour (per_drive_h) or per boot, shares are % of boots / drives / devices, timings are the
P90 over the devices. Differences in drive time, number of devices or boots are therefore already taken out:
never explain a difference by more or less driving, more devices or more boots, and never just restate that a
raw count is higher. change_pct is (B - A) / A in %. expected is the KPI's baseline when there is one, with
a_outside / b_outside telling which side misses it.
Write what really differs and what it points to: a regression or an improvement on B (crossing the baseline
matters most), how large, and the likely area (a service, a camera, connectivity, upload path, boot sequence),
linking rows that move together (e.g. more crashes and fewer videos uploaded, slower boot and later first frame).
Ignore changes under 10% unless they cross a baseline or go to / from zero. Only use the given numbers; never
invent values or causes that the rows do not support. At most {max_callouts} callouts, most important first, each
one or two plain, specific sentences naming the two sides by their labels (the device ID or OTA
version given), never as "A" or "B". Level: critical (B regressed past a baseline
or by a large margin), warning (a smaller regression or a notable change), info (an improvement or a neutral
difference worth knowing).
Answer with JSON only: {{"callouts": [{{"level": "critical|warning|info", "topic": "<the API or KPI table the
callout is mainly about, as given in the rows>", "text": "<callout>"}}]}}"""

COMPARE_HUMAN_PROMPT = """A: {a}
B: {b}
Section: {section}

Context (both sides):
{context}

Rows (only those that differ):
{rows}"""

# KPI units not shown after the name (as in the report)
NO_UNIT = {'count', 'WAF', 'text'}

PROMPTS = {'api': (SYSTEM_PROMPT, HUMAN_PROMPT), 'kpi': (KPI_SYSTEM_PROMPT, KPI_HUMAN_PROMPT),
           'overall': (OVERALL_SYSTEM_PROMPT, OVERALL_HUMAN_PROMPT),
           'compare': (COMPARE_SYSTEM_PROMPT, COMPARE_HUMAN_PROMPT)}
# Overall callouts: at most this many, over at most OVERALL_DEVICES device profiles (the most affected)
OVERALL_MAX_CALLOUTS = 6
OVERALL_DEVICES = 40
# Points per issue when ranking devices (a level's weight; test cases / KPIs capped so one area can't dominate)
LEVEL_POINTS = {'critical': 3, 'warning': 2, 'info': 1}
AREA_CAP = 5
_chains = {}
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


def _get_chain(kind='api'):
    """The chain of one prompt (PROMPTS: api, kpi, compare); same model, retries and timeout."""
    with _chain_lock:
        if kind not in _chains:
            from langchain_core.output_parsers import JsonOutputParser
            from langchain_core.prompts import ChatPromptTemplate

            system, human = PROMPTS[kind]
            _chains[kind] = (ChatPromptTemplate.from_messages([('system', system), ('human', human)])
                             | _llm() | JsonOutputParser())
    return _chains[kind]


def _number(value):
    try:
        return float(str(value).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def _name(api):
    return api_calls.APIS[api][0]


def health_central_boots(boots):
    """[{enabled, calls, drive_min (while enabled), interval_s, intervals, interval_source, worst_gap}]
    per boot where health_central ran (boot['health_central'], kpi_metrics.health_central_boots),
    from a device's boot details; the input of the HEALTH_CENTRAL callouts."""
    out = []
    for b in boots:
        hc = b.get('health_central')
        if not hc:
            continue
        drive = b.get('drive_minutes') or 0
        # Enabled during the boot (a config reload): only the drive time after it counts
        if hc.get('enabled_from') and b.get('start') and b.get('end') and hc['enabled_from'] > b['start']:
            drive = min(drive, max(0, (b['end'] - hc['enabled_from']) / 60000))
        out.append({'enabled': hc['enabled'], 'calls': len((b.get('api_calls') or {}).get(str(HEALTH_CENTRAL_API)) or ()),
                    'drive_min': round(drive, 1), 'interval_s': hc['interval_s'], 'intervals': hc['intervals'],
                    'interval_source': hc['interval_source'], 'worst_gap': hc.get('worst_gap')})
    return out


def callout_data(summary, device_summaries, device_info, defaults):
    """The rule checks per device -> {'context': ..., 'findings': {api: {kind: [{device, ...numbers}]}}}."""
    minutes = summary.get('drive_minutes_by_device') or {}
    calls_by_device = summary.get('api_calls_by_device') or {}
    observation = _number((defaults or {}).get(OBSERVATION_KEY[0], {}).get(OBSERVATION_KEY[1]))
    per_minutes = dict(PER_DRIVE_MINUTES)
    if observation and observation > 0:
        per_minutes[16] = observation
    findings = []
    lla_on = login_on = hc_on = 0
    hc_intervals = set()

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
        hc = [b for b in (device_summaries.get(d) or {}).get('health_central') or [] if b['enabled']]
        if hc:
            hc_on += 1
            api = HEALTH_CENTRAL_API
            n = sum(b['calls'] for b in hc)
            expected = sum(int(b['drive_min'] // (b['interval_s'] / 60)) for b in hc if b['interval_s'])
            hc_drive = sum(b['drive_min'] for b in hc)
            intervals = sorted({i for b in hc for i in b['intervals']})
            sources = sorted({b['interval_source'] for b in hc})
            hc_intervals.update((i, src) for b in hc for i in b['intervals'] for src in [b['interval_source']])
            kind = (None if not expected else 'zero' if n == 0 else 'far_below' if n < FAR_BELOW * expected
                    else 'below' if n < expected else None)
            if kind:
                add(api, kind, d, calls=n, expected_min=expected, drive_min=hc_drive,
                    upload_interval_s=intervals, interval_source=sources)
            gaps = [b['worst_gap'] for b in hc if b.get('worst_gap')]
            worst = max(gaps, key=lambda g: g['gap_s'] / g['interval_s'], default=None)
            if worst and worst['gap_s'] > HC_GAP_FACTOR * worst['interval_s']:
                add(api, 'gap_over_limit', d, calls=n, max_gap_min=round(worst['gap_s'] / 60, 1),
                    limit_min=round(HC_GAP_FACTOR * worst['interval_s'] / 60, 1),
                    upload_interval_s=worst['interval_s'], interval_source=worst['interval_source'])
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
            'devices_with_health_central_enabled': hc_on,
            'health_central_upload_intervals': [{'interval_s': i, 'source': src} for i, src in sorted(hc_intervals)],
            'features_enabled_on_ota': [f['name'] for f in summary.get('feature_coverage') or [] if f['devices_on']],
        },
        'findings': groups,  # {api: {kind: [finding, ...]}}
    }


def device_profiles(findings, device_summaries, minutes, report_kpi=None, device_type=None):
    """Per device with any issue: what is wrong where, ranked most affected first.

    findings: callout_data()['findings'] ({api: {kind: [{device, ...}]}}). Returns
    {'devices': [profile], 'issues_together': [...], 'concentration': {...}, 'api_issue_totals': [...]}.
    A profile: device, score, areas, multi_issue, api_issues [{api, kind, level}], failing_test_cases
    {count, examples}, config_keys_failing, kpis_missed {count, kpis}, cloud_config, facts.
    """
    api_issues = {}
    totals = []
    for api, kinds in findings.items():
        for kind, items in kinds.items():
            level = KIND_LEVEL.get(kind, 'info')
            totals.append({'api': api, 'kind': kind, 'level': level, 'devices': len(items)})
            for f in items:
                api_issues.setdefault(f['device'], []).append({'api': api, 'kind': kind, 'level': level})
    kpi_missed = {}
    if report_kpi:
        for table, rows in kpi_callout_data(report_kpi, device_summaries, device_type)['tables'].items():
            for row in rows:
                for dv in row['devices_outside']:
                    kpi_missed.setdefault(dv['device'], []).append(row['kpi'])

    profiles = []
    for d, s in device_summaries.items():
        s = s or {}
        failing = sorted(k for k, c in (s.get('tc_counts') or {}).items() if (c or {}).get('FAIL'))
        keys = len(s.get('key_fails') or {})
        cloud = (s.get('cloud_config') or {}).get('verdict')
        apis = api_issues.get(d, [])
        kpis = kpi_missed.get(d, [])
        areas = [a for a, on in (('api', apis), ('tests', failing), ('config', keys), ('kpi', kpis),
                                 ('cloud_config', cloud == 'FAIL')) if on]
        if not areas:
            continue
        score = (sum(LEVEL_POINTS[i['level']] for i in apis) + min(len(failing), AREA_CAP)
                 + min(len(kpis), AREA_CAP) + (2 if keys else 0) + (2 if cloud == 'FAIL' else 0))
        drive = s.get('drive') or {}
        reasons = drive.get('previous_shutdown_reasons') or {}
        facts = {k: v for k, v in {
            'drive_min': round(minutes.get(d) or 0),
            'boots': drive.get('boots'),
            'low_power_boots': drive.get('low_power_boots') or None,
            'short_drives': drive.get('drives_short') or None,
            'session_issues': drive.get('session_issues') or None,
            'improper_service_runs': drive.get('runs_improper') or None,
            'previous_shutdown_reasons': dict(list(reasons.items())[:3]) or None,
            'features_on': s.get('feature_set') or None,
        }.items() if v is not None}
        profiles.append({
            'device': d, 'score': score, 'areas': areas,
            'multi_issue': len(areas) >= 2 or len(apis) >= 3,
            'api_issues': apis,
            'failing_test_cases': {'count': len(failing), 'examples': [k.split('/')[-1] for k in failing[:3]]} if failing else None,
            'config_keys_failing': keys or None,
            'kpis_missed': {'count': len(kpis), 'kpis': kpis[:5]} if kpis else None,
            'cloud_config': cloud if cloud == 'FAIL' else None,
            'facts': facts,
        })
    profiles.sort(key=lambda p: (-p['score'], p['device']))

    # Issues found together: pairs of API findings / areas on the same devices
    labels = {p['device']: sorted({f"{i['api']}: {i['kind']}" for i in p['api_issues']}
                                  | {a for a in p['areas'] if a != 'api'}) for p in profiles}
    pairs = {}
    for d, items in labels.items():
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                pairs.setdefault((a, b), []).append(d)
    together = [{'issues': list(k), 'devices': len(v), 'device_ids': v[:5]}
                for k, v in sorted(pairs.items(), key=lambda kv: -len(kv[1])) if len(v) >= 2][:10]

    total_score = sum(p['score'] for p in profiles) or 1
    top = profiles[:max(1, len(profiles) // 5)]
    concentration = {
        'devices_on_ota': len(minutes) or len(device_summaries),
        'devices_with_issues': len(profiles),
        'multi_issue_devices': sum(1 for p in profiles if p['multi_issue']),
        'top_20pct_devices': len(top),
        'top_20pct_share_of_issues_pct': round(100 * sum(p['score'] for p in top) / total_score),
    }
    return {'devices': profiles, 'issues_together': together, 'concentration': concentration,
            'api_issue_totals': sorted(totals, key=lambda t: (LEVELS.index(t['level']), -t['devices']))}


def _plain_overall(profiles, concentration):
    """The overall callouts without the LLM: the multi-issue devices, plainly."""
    multi = [p for p in profiles if p['multi_issue']]
    if not multi:
        return [{'level': 'info', 'api': 'Overall', 'kind': 'overall', 'devices': [p['device'] for p in profiles],
                 'text': f"{concentration['devices_with_issues']} of {concentration['devices_on_ota']} devices have "
                         f"issues, none in several areas."}]
    worst = '; '.join(f"{p['device']} ({', '.join(p['areas'])})" for p in multi[:4])
    return [{'level': 'critical' if any(i['level'] == 'critical' for p in multi for i in p['api_issues']) else 'warning',
             'api': 'Overall', 'kind': 'overall', 'devices': [p['device'] for p in multi],
             'text': f"{len(multi)} of {concentration['devices_on_ota']} devices have issues in several areas: {worst}."}]


def _overall_callouts(ota, context, data):
    """Overall callouts by the LLM (devices kept only if they are in the input); the plain ones
    if the call fails."""
    profiles = data['devices']
    shown = profiles[:OVERALL_DEVICES]
    variables = {'ota': ota, 'rules': RULES, 'max_callouts': OVERALL_MAX_CALLOUTS,
                 'context': json.dumps(context, separators=(',', ':')),
                 'api_issue_totals': json.dumps(data['api_issue_totals'], separators=(',', ':')),
                 'devices': json.dumps([{k: v for k, v in p.items() if v not in (None, [], {})} for p in shown],
                                       separators=(',', ':'))
                 + (f'\n(+ {len(profiles) - len(shown)} more devices with fewer issues)' if len(profiles) > len(shown) else ''),
                 'issues_together': json.dumps(data['issues_together'], separators=(',', ':')),
                 'concentration': json.dumps(data['concentration'], separators=(',', ':'))}
    known = {p['device'] for p in profiles}
    try:
        raw, source = _cached_invoke(variables, kind='overall')
        callouts = []
        for item in (raw.get('callouts') if isinstance(raw, dict) else None) or []:
            text = str((item or {}).get('text', '')).strip()
            if not text:
                continue
            level = item.get('level') if item.get('level') in LEVELS else 'warning'
            devices = [str(d) for d in item.get('devices') or [] if str(d) in known]
            callouts.append({'level': level, 'api': 'Overall', 'kind': 'overall', 'text': text[:800],
                             'devices': list(dict.fromkeys(devices))})
        if callouts:
            return callouts[:OVERALL_MAX_CALLOUTS], source, None
        return _plain_overall(profiles, data['concentration']), source, 'empty answer'
    except Exception as exc:
        log.exception('%s: overall callouts LLM call failed: %s', ota, exc)
        return _plain_overall(profiles, data['concentration']), None, type(exc).__name__


def _cached_invoke(variables, kpi=False, kind=None):
    """(answer, source) for one prompt, from the disk cache or the LLM."""
    kind = kind or ('kpi' if kpi else 'api')
    model = os.environ.get('OAI_MODEL', '').strip()
    system = PROMPTS[kind][0]
    digest = hashlib.sha256(json.dumps([PROMPT_VERSION, model, system, variables],
                                       sort_keys=True).encode()).hexdigest()
    cache_path = CACHE_DIR / digest[:2] / f'{digest}.json'
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text()), 'cache'
        except ValueError:
            pass
    raw = _get_chain(kind).invoke(variables)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
        tmp.write_text(json.dumps(raw))
        tmp.replace(cache_path)
    except OSError as exc:  # e.g. a cache folder made by another user: keep the answer
        log.warning('callouts cache not written (%s): %s', cache_path, exc)
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


def build(ota, summary, device_summaries, device_info, defaults, report_kpi=None, device_type=None):
    """{'callouts': [{'level', 'api', 'kind', 'text', 'devices'}], 'llm_calls', 'cached', 'error'}.
    The checks always run; without the LLM the callouts get a plain text (error says why).
    The "Overall" callouts (across APIs and devices, see device_profiles) come first; report_kpi /
    device_type add the KPIs outside their baseline to the device profiles."""
    if not summary.get('drive_minutes_by_device'):
        return {'callouts': [], 'llm_calls': 0, 'cached': 0, 'error': 'No devices with drive data on this OTA'}
    data = callout_data(dict(summary, ota=ota), device_summaries, device_info, defaults)
    context, findings = data['context'], data['findings']
    overall = device_profiles(findings, device_summaries, summary.get('drive_minutes_by_device') or {},
                              report_kpi, device_type)
    if not findings and not overall['devices']:
        return {'callouts': [{'level': 'info', 'api': 'General', 'kind': 'ok', 'devices': [],
                              'text': 'No anomaly noticed.'}],
                'llm_calls': 0, 'cached': 0, 'error': None}
    use_llm = enabled()
    results = []
    if use_llm:
        jobs = [lambda kv=kv: _api_callouts(ota, kv[0], kv[1], context) for kv in findings.items()]
        if overall['devices']:
            jobs.append(lambda: _overall_callouts(ota, context, overall))
        with ThreadPoolExecutor(min(LLM_PARALLEL, len(jobs))) as pool:
            results = list(pool.map(lambda job: job(), jobs))
    else:
        if overall['devices']:
            results.append((_plain_overall(overall['devices'], overall['concentration']), None, None))
        for api, kinds in findings.items():
            results.append(([{'level': KIND_LEVEL.get(k, 'info'), 'api': api, 'kind': k,
                              'devices': [f['device'] for f in items],
                              'text': f'{len(items)} of {context["devices"]} devices: {k.replace("_", " ")}'}
                             for k, items in kinds.items()], None, None))
    callouts = [c for cs, _, _ in results for c in cs]
    # Overall first (in the model's order), then the per-API ones by level and reach
    callouts.sort(key=lambda c: (c['api'] != 'Overall', 0 if c['api'] == 'Overall' else LEVELS.index(c['level']),
                                 0 if c['api'] == 'Overall' else -len(c['devices'])))
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


# ── comparison callouts (lib/compare_report.py) ─────────────────────────────────────────

def _compare_group(labels, section, group, rows, context, max_callouts):
    variables = {'a': labels[0], 'b': labels[1], 'section': f'{section}: {group}' if group != section else section,
                 'context': json.dumps(context, separators=(',', ':')),
                 'rows': json.dumps(rows, separators=(',', ':')), 'max_callouts': max_callouts}
    callouts, source, error = [], None, None
    try:
        raw, source = _cached_invoke(variables, kind='compare')
        for item in ((raw.get('callouts') if isinstance(raw, dict) else None) or [])[:max_callouts]:
            text = str((item or {}).get('text', '')).strip() if isinstance(item, dict) else ''
            if text:
                level = item.get('level') if item.get('level') in LEVELS else 'warning'
                topic = str(item.get('topic') or '').strip()[:80] or group
                callouts.append({'level': level, 'api': topic, 'kind': 'compare', 'text': text[:800], 'devices': []})
    except Exception as exc:
        log.exception('comparison callouts LLM call for %s failed: %s', group, exc)
        error = f'{type(exc).__name__}'
    if not callouts:
        callouts = _plain_compare_callouts(labels, group, rows, error)
    return callouts, source, error


def _plain_compare_callouts(labels, group, rows, error=None):
    out = []
    for r in rows[:8]:
        name = r.get('kpi') or r.get('api') or r.get('code') or r.get('row') or '?'
        change = r.get('change_pct')
        key = next((k[2:] for k in r if k.startswith('a_') and f'b_{k[2:]}' in r and k[2:] not in ('outside',)), None)
        a, b = (r.get('a'), r.get('b')) if 'a' in r else (r.get(f'a_{key}'), r.get(f'b_{key}')) if key else (None, None)
        basis = f' {key.replace("_", " ")}' if key and 'a' not in r else f' ({r["basis"]})' if r.get('basis') else ''
        text = (f'{name}{basis}: {labels[0]} {a} vs {labels[1]} {b}' + (f' ({change:+.0f}%)' if isinstance(change, (int, float)) else '')
                + (' (LLM text unavailable)' if error else ''))
        out.append({'level': 'warning' if r.get('b_outside') and not r.get('a_outside') else 'info',
                    'api': r.get('table') or group, 'kind': 'compare', 'text': text, 'devices': []})
    return out


def build_compare(labels, section, groups, context, max_callouts=8):
    """Callouts on what differs between two reports, for one section of the comparison report.
    groups: {group title: rows (only the rows that differ, normalised)}; one LLM call per group,
    in parallel, as the OTA report callouts. Same result format as build() / build_kpi()."""
    groups = {g: rows for g, rows in groups.items() if rows}
    if not groups:
        return {'callouts': [{'level': 'info', 'api': 'General', 'kind': 'ok', 'devices': [],
                              'text': 'No notable difference between the two reports once normalised.'}],
                'llm_calls': 0, 'cached': 0, 'error': None}
    use_llm = enabled()
    if use_llm:
        with ThreadPoolExecutor(min(LLM_PARALLEL, len(groups))) as pool:
            results = list(pool.map(lambda kv: _compare_group(labels, section, kv[0], kv[1], context, max_callouts),
                                    groups.items()))
    else:
        results = [(_plain_compare_callouts(labels, g, rows), None, None) for g, rows in groups.items()]
    order = list(groups)
    callouts = [c for cs, _, _ in results for c in cs]
    callouts.sort(key=lambda c: (LEVELS.index(c['level']), order.index(c['api']) if c['api'] in order else 99))
    errors = sorted({e for _, _, e in results if e})
    return {
        'callouts': callouts,
        'llm_calls': sum(1 for _, src, _ in results if src == 'llm'),
        'cached': sum(1 for _, src, _ in results if src == 'cache'),
        'error': (None if use_llm else 'LLM not configured (REPORT_CALLOUTS / Azure credentials): plain texts shown')
                 if not errors else f'LLM call failed for some sections ({", ".join(errors)}): plain texts shown',
    }
