"""
Features enabled per boot: INPUT/Feature_Mapping/feature_mapping.ini evaluated against
the OTA's default config (INPUT/<ota>/bagheera_config.ini + nd_config.ini) and the CFG_PRSR overrides.

feature_mapping.ini: one section per feature; each option is a config section
whose JSON value lists the keys/values the feature needs, e.g.
    [FCW]
    fcw = {"enabled": "1"}
A feature is ON when every listed key has that value.

Overrides: at startup CFG_PRSR logs every key a service reads that is *not* in
bagheera_override.ini ("No value present for key X in override dictionary").
An overridden key gets no line, so a key is taken as overridden in a boot when a
service that normally lists it started in that boot, got past it (logged a key
that normally comes after it) and did not list it. The overridden value itself
is not logged, so such a feature is reported as OVERRIDDEN rather than ON/OFF.
What a service normally logs comes from every device seen on the OTA (this run plus
earlier runs, kept in INPUT/<ota>/cfg_key_reference.json), so a key one device
always overrides still shows up. CFG_PRSR names the key without its section, so
keys shared by several sections ("enabled") are resolved by name only.

Keys CFG_PRSR does not list may still have their effective value printed by the
service itself; override_llm reads those values from the boot's log lines, and a
value found there wins over the default and the override guess.

Statuses per boot: ON, OFF, OVERRIDDEN (a needed key is overridden, value unknown),
NA (a needed key is not in this OTA's default config and not overridden).
"""

import json
import logging
import re
from collections import defaultdict
from pathlib import Path

log = logging.getLogger('feature_status')

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURE_MAPPING = REPO_ROOT / 'INPUT' / 'Feature_Mapping' / 'feature_mapping.ini'

NOT_OVERRIDDEN_RE = re.compile(r'No value present for key (\S+) in override dictionary')
STATUSES = ['ON', 'OFF', 'OVERRIDDEN', 'NA']
TRUE, FALSE = {'true', '1', 'on', 'enable', 'enabled', 'yes'}, {'false', '0', 'off', 'disable', 'disabled', 'no'}


def load_features(path=FEATURE_MAPPING):
    """[{'name', 'conditions': [[section, key, expected], ...]}] in file order."""
    features, current = [], None
    with open(path, encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line[0] in ';#':
                continue
            if line.startswith('[') and line.endswith(']'):
                current = {'name': line[1:-1].strip(), 'conditions': []}
                features.append(current)
                continue
            if current is None or '=' not in line:
                continue
            section, _, value = line.partition('=')
            try:
                wanted = json.loads(value)
            except json.JSONDecodeError:
                log.warning('feature_mapping: %s / %s is not JSON, skipped', current['name'], section.strip())
                continue
            for key, expected in wanted.items():
                current['conditions'].append([section.strip(), key, expected])
    return [f for f in features if f['conditions']]


def parse_ini_values(path):
    """{section: {key: raw value}}; first occurrence wins (the ini repeats some keys)."""
    values, section = defaultdict(dict), None
    with open(path, encoding='iso8859-15') as f:
        for raw in f:
            line = raw.strip()
            if not line or line[0] in ';#':
                continue
            if line.startswith('[') and ']' in line:
                section = line[1:line.index(']')].strip()
                continue
            key, sep, value = line.partition('=')
            if sep and section is not None:
                values[section].setdefault(key.strip(), value.split(';')[0].strip().strip('"'))
    return dict(values)


def _norm(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    text = str(value).strip().strip('"').lower()
    if text in TRUE:
        return 'true'
    if text in FALSE:
        return 'false'
    try:
        number = float(text)
    except ValueError:
        return text
    text = str(int(number)) if number.is_integer() else str(number)
    # 1.0 / 0.0 are booleans too (1 / 0 already matched TRUE / FALSE above)
    return 'true' if text in TRUE else 'false' if text in FALSE else text


def same_value(default, expected):
    return _norm(default) == _norm(expected)


# ── overrides ────────────────────────────────────────────────────────────

# A key is "normally read" by a service on an OTA when its not-overridden line is in
# at least this share of that service's starts (over every device seen on the OTA)
REFERENCE_MIN_SHARE = 0.5
# A service start only counts when it logged at least this share of its normal keys
# (a start cut off by the end of the logs would make every later key look overridden)
MIN_COVERAGE = 0.8


def not_overridden_keys(lines):
    """Keys a service logged as "No value present ... in override dictionary"."""
    return frozenset(m.group(1) for line in lines for m in [NOT_OVERRIDDEN_RE.search(line)] if m)


def boot_profile(lines_by_service):
    """{service: frozenset of not-overridden keys} for a boot's first service starts."""
    profile = {}
    for service, lines in lines_by_service.items():
        keys = not_overridden_keys(lines)
        if keys:
            profile[service] = keys
    return profile


def add_to_reference_counts(counts, profile):
    """counts: {service: {'starts': n, 'keys': {key: n}}}, updated in place."""
    for service, keys in profile.items():
        entry = counts.setdefault(service, {'starts': 0, 'keys': {}})
        entry['starts'] += 1
        for key in keys:
            entry['keys'][key] = entry['keys'].get(key, 0) + 1


def merge_reference_counts(a, b):
    merged = json.loads(json.dumps(a))
    for service, entry in b.items():
        target = merged.setdefault(service, {'starts': 0, 'keys': {}})
        target['starts'] += entry['starts']
        for key, n in entry['keys'].items():
            target['keys'][key] = target['keys'].get(key, 0) + n
    return merged


def reference(counts):
    """{service: set of keys it normally logs as not overridden}."""
    return {service: {k for k, n in entry['keys'].items() if n >= REFERENCE_MIN_SHARE * entry['starts']}
            for service, entry in counts.items() if entry['starts']}


def boot_overrides(profile, ref):
    """Keys overridden in a boot: normally logged by a service that started (fully) in the
    boot but missing from its list, and not logged as not-overridden by any other service."""
    plain = set().union(*profile.values()) if profile else set()
    overridden = set()
    for service, keys in profile.items():
        expected = ref.get(service)
        if not expected or len(keys & expected) < MIN_COVERAGE * len(expected):
            continue
        overridden |= expected - keys
    return overridden - plain


# ── evaluation ───────────────────────────────────────────────────────────

def evaluate(features, defaults, overridden, values=None):
    """[[status, [condition state, ...]], ...] per feature.

    values: {section.key: {'value': ...} | None}, effective values read from the boot's
    logs (override_llm); they win over everything else.
    Condition states: 'log_ok' / 'log_off' (logged value matches / differs), 'ok', 'off'
    (default matches / differs), 'ovr' (overridden, value unknown), 'na' (not in the default config).
    """
    values = values or {}
    results = []
    for feature in features:
        states = []
        for section, key, expected in feature['conditions']:
            logged = values.get(f'{section}.{key}')
            if logged is not None:
                states.append('log_ok' if same_value(logged['value'], expected) else 'log_off')
            elif key in overridden:
                states.append('ovr')
            elif key not in defaults.get(section, {}):
                states.append('na')
            else:
                states.append('ok' if same_value(defaults[section][key], expected) else 'off')
        if 'off' in states or 'log_off' in states:
            status = 'OFF'
        elif 'ovr' in states:
            status = 'OVERRIDDEN'
        elif 'na' in states:
            status = 'NA'
        else:
            status = 'ON'
        results.append([status, states])
    return results


def defaults_only(features, defaults):
    """Feature statuses from the OTA's default config alone (no override information)."""
    return evaluate(features, defaults, set())
