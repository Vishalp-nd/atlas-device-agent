"""
Boot states: what state a device was in, and when, during a boot (INPUT/boot_states.ini).

A test declares the states it needs with a pytest marker in its file
    @pytest.mark.boot_state(power="ignition_off", link=("lte", "lte_wifi"))
(lib/testcase_log_steps.py reads it without running the test). ota_report then judges the
test only on the parts of a boot where every named dimension had one of the listed values;
a boot that never had them gives the test CONDITION_NOT_MET instead of FAIL / NOT_TRIGGERED.

Cost: the rules' lines are added to the test steps' grep (one pass per service and day, on
the day already rebuilt in RAM), so the logs are not read again. Per boot, the hits become a
timeline per dimension: a handful of (start, end, value) intervals, built once per boot and
shared by every test.

Derived dimensions combine primitive ones (DERIVED): link from lte + wifi, network from
internet + signal.
"""

import bisect
import logging
from pathlib import Path

import testcase_log_steps

log = logging.getLogger('boot_states')

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = REPO_ROOT / 'INPUT' / 'boot_states.ini'
INF = float('inf')


def _link(lte, wifi):
    # Unknown until conn_mgr reports LTE (wifi starts "down" by default, see the ini)
    if lte is None:
        return None
    return {('up', 'up'): 'lte_wifi', ('up', 'down'): 'lte', ('down', 'up'): 'wifi',
            ('down', 'down'): 'none'}.get((lte, wifi or 'down'))


def _network(internet, signal):
    if internet == 'fail':
        return 'none'
    if internet == 'pass':
        return 'poor' if signal == 'poor' else 'proper'
    return None


# name -> (primitive dimensions, value function, values)
DERIVED = {
    'link': (('lte', 'wifi'), _link, ('lte', 'wifi', 'lte_wifi', 'none')),
    'network': (('internet', 'signal'), _network, ('proper', 'poor', 'none')),
}


class Rules:
    """The parsed rules file.

    dims: {dimension: {'values': [value, ...], 'default': value | None,
                       'rules': [(value, service, bre, regex), ...]}}
    """

    def __init__(self, dims):
        self.dims = dims

    def values(self, dim):
        if dim in DERIVED:
            return list(DERIVED[dim][2])
        return self.dims[dim]['values'] if dim in self.dims else None

    def detected(self, dim):
        """Whether the logs can tell this dimension at all (some value has a rule)."""
        if dim in DERIVED:
            return all(self.detected(p) for p in DERIVED[dim][0])
        return bool(self.dims.get(dim, {}).get('rules'))

    def value_detected(self, dim, value):
        if dim in DERIVED:
            return self.detected(dim)
        return any(v == value for v, *_ in self.dims.get(dim, {}).get('rules', ()))

    def patterns(self):
        """{service: [(bre, regex)]} to add to the test steps' grep."""
        out = {}
        for d in self.dims.values():
            for _, service, bre, regex in d['rules']:
                out.setdefault(service, {}).setdefault(regex, bre)
        return {service: [(bre, regex) for regex, bre in pats.items()] for service, pats in out.items()}

    def usable(self, requirement):
        """The part of a test's requirement the logs can check: {dim: set(values)}.

        Dimensions (or values) without a rule are dropped, so a test is never filtered out
        on a state nothing detects.
        """
        out = {}
        for dim, values in (requirement or {}).items():
            if not self.detected(dim):
                continue
            wanted = {v for v in values if self.value_detected(dim, v)}
            if len(wanted) == len(set(values)):
                out[dim] = frozenset(wanted)
        return out


def load(path=RULES_FILE):
    """Rules from the ini (see its header). Missing file: no rules, nothing is gated."""
    dims, current = {}, None
    path = Path(path)
    if not path.exists():
        log.warning('%s not found: tests are not filtered on boot states', path)
        return Rules({})
    for n, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in ';#':
            continue
        if line.startswith('[') and line.endswith(']'):
            current = dims.setdefault(line[1:-1].strip(), {'values': [], 'default': None, 'rules': []})
            continue
        if current is None or '=' not in line:
            log.warning('%s:%d: not a rule, skipped: %s', path.name, n, line)
            continue
        value, _, rule = (s.strip() for s in line.partition('='))
        if value == 'default':
            current['default'] = rule or None
            continue
        if value not in current['values']:
            current['values'].append(value)
        if not rule:
            continue
        service, sep, bre = (s.strip() for s in rule.partition('|'))
        if not sep or not service or not bre:
            log.warning('%s:%d: expected "<value> = <service> | <pattern>", skipped', path.name, n)
            continue
        current['rules'].append((value, service, bre, testcase_log_steps.bre_to_python(bre)))
    return Rules(dims)


def check_markers(tests, rules, feature_names):
    """Log, once per run, marker values the analyzer does not know (typos, new states)."""
    unknown = {}
    known_features = set(feature_names) | {'generic'}
    for t in tests:
        for f in t.get('features') or ():
            if f not in known_features:
                unknown.setdefault(f'feature "{f}"', []).append(t['tc_id'])
        for dim, values in (t.get('boot_state') or {}).items():
            allowed = rules.values(dim)
            if allowed is None:
                unknown.setdefault(f'boot_state dimension "{dim}"', []).append(t['tc_id'])
                continue
            for v in values:
                if v not in allowed:
                    unknown.setdefault(f'boot_state {dim}="{v}"', []).append(t['tc_id'])
    for what, tcs in sorted(unknown.items()):
        log.warning('Unknown %s in %d test(s), e.g. %s: not used for filtering', what, len(tcs), tcs[:3])
    return unknown


# ── per boot ─────────────────────────────────────────────────────────────

def _primitive_timeline(dim_rules, hits, boot, lo, hi):
    """[(start, end, value)] of one primitive dimension inside [lo, hi)."""
    events = []
    for value, service, _, regex in dim_rules['rules']:
        service_hits = hits.get(service)
        entry = service_hits and service_hits.get(regex, {}).get(boot)
        if entry:
            events.extend((ts, value) for ts in entry[0])
    events.sort()
    out = []
    state, since = dim_rules['default'], lo
    for ts, value in events:
        if value != state:
            if state is not None and ts > since:
                out.append((since, ts, state))
            state, since = value, ts
    if state is not None and hi > since:
        out.append((since, hi, state))
    return out


def _value_at(timeline, ts):
    i = bisect.bisect_right(timeline, (ts, INF, '￿')) - 1
    if i >= 0 and timeline[i][0] <= ts < timeline[i][1]:
        return timeline[i][2]
    return None


def _derived_timeline(parts, fn, lo, hi):
    cuts = sorted({lo, hi, *(t for tl in parts for s, e, _ in tl for t in (s, e) if lo <= t <= hi)})
    out = []
    for a, b in zip(cuts, cuts[1:]):
        value = fn(*(_value_at(tl, a) for tl in parts))
        if value is None:
            continue
        if out and out[-1][2] == value and out[-1][1] == a:
            out[-1] = (out[-1][0], b, value)
        else:
            out.append((a, b, value))
    return out


class BootTimeline:
    """The states of one boot, built lazily per dimension and cached."""

    def __init__(self, rules, hits, boot, start, end):
        self.rules, self.hits, self.boot = rules, hits, boot
        self.lo = start if start is not None else -INF
        self.hi = end if end is not None else INF
        self._dims, self._windows = {}, {}

    def dim(self, name):
        tl = self._dims.get(name)
        if tl is None:
            if name in DERIVED:
                parts, fn, _ = DERIVED[name]
                tl = _derived_timeline([self.dim(p) for p in parts], fn, self.lo, self.hi)
            else:
                tl = _primitive_timeline(self.rules.dims[name], self.hits, self.boot, self.lo, self.hi)
            self._dims[name] = tl
        return tl

    def windows(self, requirement):
        """Sorted, merged [(start, end)] where every dimension of requirement
        ({dim: frozenset(values)}, from Rules.usable) had one of its values."""
        key = frozenset(requirement.items())
        cached = self._windows.get(key)
        if cached is not None:
            return cached
        result = [(self.lo, self.hi)]
        for dim, values in requirement.items():
            spans = [(s, e) for s, e, v in self.dim(dim) if v in values]
            result = _intersect(result, spans)
            if not result:
                break
        self._windows[key] = result
        return result


def _intersect(a, b):
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if lo < hi:
            if out and out[-1][1] == lo:
                out[-1] = (out[-1][0], hi)
            else:
                out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def describe(requirement):
    """'power=ignition_off, link=lte|lte_wifi' for reasons and the report."""
    return ', '.join(f'{d}={"|".join(sorted(v))}' for d, v in sorted(requirement.items()))
