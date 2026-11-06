"""
Comparison report: two OTA reports (lib/ota_report.py ota_report.json) side by side, A and B.
The page names each side by its label (the device ID or OTA version the comparison was asked
for), never "A" / "B".

    python lib/compare_report.py <A ota_report.json> <B ota_report.json> <out dir> [--label-a X --label-b Y]

Same sections and look as the OTA report, each with A and B next to each other and the change.
Everything that depends on how much the devices drove is normalised before it is compared,
so a difference in drive time, devices or boots never shows up as a difference by itself:
  - API calls, alerts, retries per drive hour; boot-related KPIs (bootup, downtime, camera
    crashes, watchdog) per boot over the boots of all devices (TABLE_BASIS)
  - shares as % of boots / drives / devices / test cases
  - KPIs that are already per session / event or a snapshot: the P90 over the devices
Change = (B - A) / A in %. KPI cells outside their baseline are amber, as in the OTA report.

Callouts-API Summary and Callouts-KPI Metrics (report_callouts.build_compare) say what differs
between A and B, from the normalised rows that changed only (same model, cache and retries as
the OTA report callouts).

Writes <out dir>/compare_report.json and compare_report.html.
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import api_calls
import kpi_metrics
import report_callouts
from ota_report import (CALLOUT_DOTS, KPI_NO_UNIT, REPORT_JS, REPORT_STYLE, _esc, _kpi_fmt, _shell, _table, _tile,
                        device_type_for_ota)

log = logging.getLogger('compare_report')

# Changes smaller than this (%) are noise: not passed to the callouts
MIN_CHANGE_PCT = 10
# Analytics codes passed to the callouts (largest normalised changes)
MAX_CODE_ROWS = 25

# Left navigation; KPI Metrics is a group that expands into its tables (filled in render_html)
NAV = [('overview', 'Overview'), ('drive', 'Drive Summary'), ('feature-sets', 'Feature Set Summary'),
       ('api', 'API Call Summary'), ('callouts', 'Callouts-API Summary'), ('features', 'Feature Level Summary'),
       ('kpi', 'KPI Metrics'), ('kpi-callouts', 'Callouts-KPI Metrics')]

STYLE = """
.sides{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
.side-card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;min-width:0}
.side-card{border-left:4px solid var(--tag)}
.side-card b{font-size:15px;word-break:break-all;color:var(--tag)}.side-card p{margin:6px 0 0;color:var(--muted);font-size:12.5px}
.a{--tag:#2f6fde}.b{--tag:#a447c9}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]) .a{--tag:#6ea2ff}:root:not([data-theme="light"]) .b{--tag:#c77ee6}}
:root[data-theme="dark"] .a{--tag:#6ea2ff}:root[data-theme="dark"] .b{--tag:#c77ee6}
th.a .who,th.b .who{display:block;color:var(--tag);font-weight:700;white-space:normal;word-break:break-all;max-width:200px}
.group-head{margin:30px 0 14px;scroll-margin-top:20px}.group-head h2{font-size:21px;margin:0 0 6px;letter-spacing:-.01em}
.group-head .legend-ab{margin:0}
td.up{color:var(--fail);font-weight:600}td.down{color:var(--pass);font-weight:600}td.chg{font-weight:600}
td.same{color:var(--none)}.kpi td.warn{background:var(--nt-bg);color:var(--nt);font-weight:600}
.legend-ab{color:var(--muted);font-size:12.5px;margin:0 0 12px}
@media (max-width:820px){.sides{grid-template-columns:1fr}}
"""


# ── numbers per side ────────────────────────────────────────────────────────────────────

def _side(report):
    """The normalising bases of one report."""
    sm = report.get('summary') or {}
    drive = report.get('drive') or {}
    minutes = sm.get('drive_minutes_total', drive.get('drive_minutes_total')) or 0
    devices = sm.get('devices_on_ota') or len(report.get('devices') or {})
    boots = sum(len(b) for b in (report.get('devices') or {}).values())
    return {'report': report, 'sm': sm, 'drive': drive, 'minutes': minutes, 'hours': minutes / 60,
            'devices': devices, 'boots': boots, 'device_type': device_type_for_ota(report.get('ota_version'))}


def _per(n, base):
    return n / base if base else None


def _pct(n, base):
    return 100 * n / base if base else None


def _change(a, b):
    """(B - A) / A in %; None when not comparable, inf when A is 0 and B is not."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return None
    if a == 0:
        return 0.0 if b == 0 else float('inf')
    return 100 * (b - a) / abs(a)


def _r(v, nd=2):
    return round(v, nd) if isinstance(v, float) else v


def _fmt(v, unit=''):
    if v is None:
        return '—'
    if isinstance(v, float):
        if unit == '%':
            return f'{v:.1f}%'
        return f'{v:,.0f}' if abs(v) >= 1000 else f'{v:.1f}' if abs(v) >= 10 else f'{v:.2f}'
    return f'{v:,}' if isinstance(v, int) else _esc(v)


def _chg_cell(a, b, higher_is_worse=None):
    """The change cell: % (or pp for shares given as %), bold past MIN_CHANGE_PCT; red when the
    change is known to be worse, green when better."""
    c = _change(a, b)
    if c is None:
        return '<td class="num same">—</td>'
    if c == float('inf'):
        text, big = 'new', True
    elif c == 0:
        return '<td class="num same">0%</td>'
    else:
        text, big = f'{c:+.0f}%', abs(c) >= MIN_CHANGE_PCT
    cls = 'chg' if big else ''
    if big and higher_is_worse is not None:
        cls = 'up' if (b > a) == higher_is_worse else 'down'
    return f'<td class="num {cls}" data-v="{0 if c == float("inf") else round(c, 1)}">{text}</td>'


def _th(labels, side, col='', cls='num'):
    """A header cell of one side: its label (device ID / OTA) on top, then the column name."""
    k = 'ab'[side]
    return f'<th class="{cls} {k}"><span class="who">{_esc(labels[side])}</span>{_esc(col)}</th>'


def _ab(labels, col):
    """The two sides' header cells of one column."""
    return _th(labels, 0, col) + _th(labels, 1, col)


def _sortable(head_cells, rows, foot=''):
    return (f'<div class="tbl"><table class="sort"><thead><tr>{"".join(head_cells)}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>{foot}</table></div>')


# ── sections ────────────────────────────────────────────────────────────────────────────

def _overview_rows(A, B):
    """(label, a, b, unit, higher_is_worse, normalised) rows of the overview / drive tables."""
    def drive_rows(S):
        d, sm = S['drive'], S['sm']
        drives = d.get('drives') or 0
        return {
            'Devices': (S['devices'], ''),
            'Devices with detailed logs': (len(sm.get('devices_detailed_logs') or []), ''),
            'Devices with only critical logs': (len(sm.get('devices_critical_only') or []), ''),
            'Boots': (S['boots'], ''),
            'Drive time (h)': (S['hours'], ''),
            'Drive time per device (h)': (_per(S['hours'], S['devices']), ''),
            'Boots per device': (_per(S['boots'], S['devices']), ''),
            'Drives per drive hour': (_per(drives, S['hours']), ''),
            'Drives < 6 min (% of drives)': (_pct(d.get('drives_short') or 0, drives), '%'),
            'Low power boots (% of boots)': (_pct(d.get('low_power_boots') or 0, d.get('boots') or 0), '%'),
            'Devices with low power boots (% of devices)': (_pct(len(sm.get('devices_low_power') or []), S['devices']), '%'),
            'Devices running > 6 h (% of devices)': (_pct(len(sm.get('devices_over_6h') or []), S['devices']), '%'),
            'Sessions created per drive hour': (_per(d.get('sessions_created') or 0, S['hours']), ''),
            'Sessions copied to SD card (% of created)': (_pct(d.get('sessions_available') or 0, d.get('sessions_created') or 0), '%'),
            'Drives with session issues (% of drives)': (_pct(d.get('session_issues') or 0, drives), '%'),
            'Improper shutdown runs per drive hour': (_per(d.get('runs_improper') or 0, S['hours']), ''),
        }
    a, b = drive_rows(A), drive_rows(B)
    worse = {'Drives < 6 min (% of drives)': True, 'Low power boots (% of boots)': True,
             'Devices with low power boots (% of devices)': True, 'Drives with session issues (% of drives)': True,
             'Improper shutdown runs per drive hour': True, 'Sessions copied to SD card (% of created)': False,
             'Devices with only critical logs': True}
    raw = {'Devices', 'Devices with detailed logs', 'Devices with only critical logs', 'Boots', 'Drive time (h)'}
    return [(k, a[k][0], b[k][0], a[k][1], worse.get(k), k not in raw) for k in a]


def _overview_section(rows, labels):
    body = []
    for label, a, b, unit, worse, _ in rows:
        body.append(f'<tr><td>{_esc(label)}</td><td class="num">{_fmt(a, unit)}</td><td class="num">{_fmt(b, unit)}</td>'
                    f'{_chg_cell(a, b, worse)}</tr>')
    head = ['<th>Metric</th>', _ab(labels, ''), '<th class="num">Change</th>']
    return ('<section id="drive"><h2>Drive Summary</h2>'
            '<p class="legend-ab">Raw counts first; the rest is per device, per drive hour or a share, so the two sides compare directly.</p>'
            + _sortable(head, body) + '</section>')


def _feature_sets_section(A, B, labels):
    on = lambda S: {f['name']: bool(f['devices_on']) for f in (S['sm'].get('feature_coverage') or [])}
    a, b = on(A), on(B)
    names = sorted(set(a) | set(b), key=str.lower)
    diff = [n for n in names if a.get(n) != b.get(n)]
    cell = lambda v: ('<td class="n-pass">Enabled</td>' if v else '<td class="zero">Disabled</td>' if v is False
                      else '<td class="zero">—</td>')
    rows = [f'<tr><td>{_esc(n)}</td>{cell(a.get(n))}{cell(b.get(n))}</tr>'
            for n in sorted(names, key=lambda n: (n not in diff, n.lower()))]
    return ('<section id="feature-sets"><h2>Feature Set Summary</h2><div class="tiles">'
            + _tile(sum(a.values()), f'{labels[0]}: features enabled') + _tile(sum(b.values()), f'{labels[1]}: features enabled')
            + _tile(len(diff), 'Features that differ', dot='c-nt' if diff else None)
            + '</div>' + (_sortable(['<th>Feature Name</th>', _th(labels, 0, cls=''), _th(labels, 1, cls='')], rows) if rows else '')
            + '</section>'), diff, a, b


def _api_rows(A, B):
    """Per API: calls, calls per drive hour, share of boots with calls, average gap (min)."""
    out = []
    for api in api_calls.SHOWN:
        name, kind = api_calls.APIS[api]
        sa = (A['report'].get('api_calls') or {}).get('apis', {}).get(str(api))
        sb = (B['report'].get('api_calls') or {}).get('apis', {}).get(str(api))
        if not sa and not sb:
            continue

        def vals(S, st):
            st = st or {}
            boots = (S['report'].get('api_calls') or {}).get('boots') or S['boots']
            calls = st.get('calls', 0)
            return {'calls': calls, 'per_h': _per(calls, S['hours']), 'per_boot': _per(calls, boots),
                    'boots_pct': _pct(st.get('boots', 0), boots),
                    'avg_gap_min': st['gap_sum'] / st['gaps'] / 60000 if st.get('gaps') else None,
                    'max_gap_min': st['gap_max'] / 60000 if st.get('gap_max') is not None and st.get('gaps') else None}
        out.append((api, name, kind, vals(A, sa), vals(B, sb)))
    return out


def _api_section(rows, labels):
    body = []
    for api, name, kind, a, b in rows:
        body.append(f'<tr><td>{api}</td><td>{_esc(name)}</td><td>{_esc(kind)}</td>'
                    f'<td class="num">{a["calls"]:,}</td><td class="num">{b["calls"]:,}</td>'
                    f'<td class="num">{_fmt(a["per_h"])}</td><td class="num">{_fmt(b["per_h"])}</td>'
                    f'{_chg_cell(a["per_h"], b["per_h"])}'
                    f'<td class="num">{_fmt(a["boots_pct"], "%")}</td><td class="num">{_fmt(b["boots_pct"], "%")}</td>'
                    f'<td class="num">{_fmt(a["avg_gap_min"])}</td><td class="num">{_fmt(b["avg_gap_min"])}</td></tr>')
    head = ['<th>ID</th>', '<th>API</th>', '<th>Type</th>', _ab(labels, '#Calls'), _ab(labels, 'Calls / drive h'),
            '<th class="num">Change</th>', _ab(labels, 'Boots with calls'), _ab(labels, 'Avg gap (min)')]
    return ('<section id="api"><h2>API Call Summary</h2><p class="legend-ab">Compared per drive hour; '
            'boots with calls as a share of each side\'s boots.</p>' + _sortable(head, body) + '</section>')


def _feature_rates(report):
    out = {}
    for name, feat in (report.get('features') or {}).items():
        tcs = feat.get('test_cases') or []
        fail = sum(1 for tc in tcs if tc['boots'].get('FAIL'))
        trig = sum(1 for tc in tcs if tc['boots'].get('PASS') or tc['boots'].get('FAIL'))
        s = feat.get('summary') or {}
        out[name] = {'tcs': len(tcs), 'triggered': trig, 'fail_tcs': fail, 'fail_pct': _pct(fail, len(tcs)),
                     'boot_fail_pct': _pct(s.get('FAIL', 0), s.get('FAIL', 0) + s.get('PASS', 0))}
    return out


def _features_section(A, B, labels):
    a, b = _feature_rates(A['report']), _feature_rates(B['report'])
    names = sorted(set(a) | set(b), key=lambda n: (-abs((b.get(n, {}).get('fail_pct') or 0) - (a.get(n, {}).get('fail_pct') or 0)), n.lower()))
    rows = []
    for n in names:
        x, y = a.get(n, {}), b.get(n, {})
        rows.append(f'<tr><td>{_esc(n)}</td><td class="num">{x.get("tcs", "—")}</td><td class="num">{y.get("tcs", "—")}</td>'
                    f'<td class="num">{x.get("fail_tcs", "—")}</td><td class="num">{y.get("fail_tcs", "—")}</td>'
                    f'<td class="num">{_fmt(x.get("fail_pct"), "%")}</td><td class="num">{_fmt(y.get("fail_pct"), "%")}</td>'
                    f'{_chg_cell(x.get("fail_pct"), y.get("fail_pct"), True)}'
                    f'<td class="num">{_fmt(x.get("boot_fail_pct"), "%")}</td><td class="num">{_fmt(y.get("boot_fail_pct"), "%")}</td></tr>')
    tot = lambda r: (sum(v['fail_tcs'] for v in r.values()), sum(v['tcs'] for v in r.values()))
    (fa, ta), (fb, tb) = tot(a), tot(b)
    head = ['<th>Feature Name</th>', _ab(labels, '#Testcases'), _ab(labels, '#Failing'), _ab(labels, 'Failure %'),
            '<th class="num">Change</th>', _ab(labels, 'Failed boots %')]
    tiles = ('<div class="tiles">' + _tile(f'{_pct(fa, ta) or 0:.1f}%', f'{labels[0]}: test cases failing') +
             _tile(f'{_pct(fb, tb) or 0:.1f}%', f'{labels[1]}: test cases failing') + '</div>')
    return ('<section id="features"><h2>Feature Level Summary</h2>' + tiles + _sortable(head, rows) + '</section>'), a, b


# How each KPI table is compared beyond its P90 (tables not listed: the P90 only, as their KPIs
# are already per session / per event, or a device snapshot):
#   boot     counts per boot (summed over devices / the boots of all devices), % boot-weighted,
#            times averaged over every boot of every device
#   drive_h  counts per drive hour (they grow with driving: alerts, retries); times the P90 only
TABLE_BASIS = {'kpi-boot': 'boot', 'kpi-downtime': 'boot', 'kpi-crash': 'boot', 'kpi-watchdog': 'boot',
               'kpi-alerts': 'drive_h', 'kpi-retry': 'drive_h', 'kpi-lla': 'drive_h'}
BASIS_HEAD = {'boot': 'per boot', 'drive_h': 'per drive h'}


def _mean_over_samples(S, m, col):
    """A mean KPI averaged over all samples: the report's total (newer reports), else the
    average of the device values."""
    if col.get('total') is not None:
        return col['total']
    vals = [v[m.id] for v in ((S['report'].get('kpi') or {}).get('devices') or {}).values()
            if isinstance(v.get(m.id), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _kpi_value(S, m, basis):
    """(P90 / most common, normalised value or None) of one KPI on one side."""
    col = ((S['report'].get('kpi') or {}).get('columns') or {}).get(m.id) or {}
    if m.unit == 'text':
        return col.get('common'), None
    p90 = col.get('p90')
    if basis == 'boot':
        if m.agg == 'sum':
            return p90, _per(col['total'], S['boots']) if col.get('total') is not None else None
        if m.agg == 'pct':
            return p90, col.get('total')
        if m.agg == 'mean':
            return p90, _mean_over_samples(S, m, col)
        return p90, None
    if basis == 'drive_h' and m.agg == 'sum' and m.unit == 'count':
        return p90, _per(col['total'], S['hours']) if col.get('total') is not None else None
    return p90, None


def _kpi_rows(A, B):
    """{table title: (table id, basis, [row])} for every KPI either side has; a row's a / b are
    compared: the normalised values when there are, else the P90s."""
    tables = {}
    for table in kpi_metrics.KPI_TABLES:
        basis = TABLE_BASIS.get(table.id)
        rows = []
        for m in table.metrics:
            (pa, na), (pb, nb) = _kpi_value(A, m, basis), _kpi_value(B, m, basis)
            if pa is None and pb is None:
                continue
            unit = '' if m.unit in KPI_NO_UNIT else f' ({m.unit})'
            name = m.name + ('' if m.name.endswith(m.unit) else unit)
            normalised = na is not None or nb is not None
            rows.append({'metric': m, 'name': name, 'basis': BASIS_HEAD[basis] if normalised else
                         ('most common' if m.unit == 'text' else 'P90'),
                         'a_p90': pa, 'b_p90': pb, 'a_norm': na, 'b_norm': nb,
                         'a': na if normalised else pa, 'b': nb if normalised else pb,
                         'a_warn': kpi_metrics.baseline_issue(m.id, pa, A['device_type']),
                         'b_warn': kpi_metrics.baseline_issue(m.id, pb, B['device_type'])})
        if rows:
            tables[table.title] = (table.id, basis, rows)
    return tables


def _kpi_cell(value, unit, warn=None):
    if value is None:
        return '<td class="num zero">—</td>'
    flag = ' warn' if warn else ''
    if unit == 'text':
        return f'<td class="text{flag}">{_kpi_fmt(value, unit)}</td>'
    return f'<td class="num{flag}" data-v="{value}">{_kpi_fmt(value, unit)}</td>'


def _kpi_sections(tables, labels, codes_html=''):
    """The KPI table sections, the Analytics KPIs (codes_html) right after the alert tables."""
    out = []
    for title, (tid, basis, rows) in tables.items():
        body = []
        for r in rows:
            m = r['metric']
            if m.unit == 'text':
                chg = '<td class="same">same</td>' if r['a'] == r['b'] else '<td class="chg">changed</td>'
            else:
                chg = _chg_cell(r['a'], r['b'])
            norm = ''
            if basis:
                unit = '%' if m.agg == 'pct' else ''
                norm = (f'<td class="num">{_fmt(r["a_norm"], unit)}</td><td class="num">{_fmt(r["b_norm"], unit)}</td>'
                        if r['a_norm'] is not None or r['b_norm'] is not None
                        else '<td class="num zero"></td><td class="num zero"></td>')
            body.append(f'<tr><td>{_esc(r["name"])}</td>{_kpi_cell(r["a_p90"], m.unit, r["a_warn"])}'
                        f'{_kpi_cell(r["b_p90"], m.unit, r["b_warn"])}{norm}{chg}</tr>')
        head = ['<th>KPI</th>', _ab(labels, 'P90')]
        if basis:
            head.append(_ab(labels, BASIS_HEAD[basis]))
        head.append('<th class="num">Change</th>')
        out.append(f'<section id="{tid}" class="kpi"><h2>{_esc(title)}</h2>' + _sortable(head, body) + '</section>')
        if tid == 'kpi-alert-upload':
            out.append(codes_html)
            codes_html = ''
    return ''.join(out) + codes_html


def _kpi_head():
    """The KPI Metrics heading over all the KPI tables and the Analytics KPIs."""
    return ('<div class="group-head" id="kpi"><h2>KPI Metrics</h2><p class="legend-ab">Per boot = counts over the '
            'boots of all devices, times averaged over all boots; per drive h = counts per drive hour; other KPIs '
            'are compared by their P90 over the devices. Change uses the per boot / per drive h value when there '
            'is one. Amber: outside the baseline.</p></div>')


def _code_rows(A, B):
    counts = lambda S: {c['code']: c['count'] for c in ((S['report'].get('kpi') or {}).get('event_codes') or [])}
    a, b = counts(A), counts(B)
    rows = []
    for code in sorted(set(a) | set(b), key=lambda c: [int(p) if p.isdigit() else p for p in c.split('.')]):
        x, y = a.get(code, 0), b.get(code, 0)
        rows.append({'code': code, 'a_count': x, 'b_count': y, 'a': _per(x, A['hours']), 'b': _per(y, B['hours'])})
    return rows


def _codes_section(rows, labels):
    body = [f'<tr><td>{_esc(r["code"])}</td><td class="num">{r["a_count"]:,}</td><td class="num">{r["b_count"]:,}</td>'
            f'<td class="num">{_fmt(r["a"])}</td><td class="num">{_fmt(r["b"])}</td>{_chg_cell(r["a"], r["b"])}</tr>'
            for r in rows]
    head = ['<th>Event code</th>', _ab(labels, '#Alerts'), _ab(labels, 'per drive h'), '<th class="num">Change</th>']
    inner = _sortable(head, body) if rows else '<p class="muted">No alerts on either side.</p>'
    return f'<section id="kpi-codes" class="kpi"><h2>Analytics KPIs</h2>{inner}</section>'


def _callouts_html(co, section_id, title):
    if co is None:
        return ''
    items = []
    for c in co.get('callouts') or []:
        label = '' if c['api'] in ('General', 'API Call Summary', 'KPI Summary') else f'<b>{_esc(c["api"])}</b> '
        items.append(f'<li><i class="dot {CALLOUT_DOTS.get(c["level"], "c-acc")}"></i>{label}{_esc(c["text"])}</li>')
    body = f'<ul class="callouts">{"".join(items)}</ul>' if items else ''
    if co.get('error'):
        body += f'<p class="muted">{_esc(co["error"])}</p>'
    return (f'<section id="{section_id}" class="callouts-box"><h2><span class="sparkle" aria-hidden="true">✨</span> '
            f'{_esc(title)}</h2>{body}</section>')


# ── callout input: the normalised rows that differ ───────────────────────────────────────

def _differs(a, b, a_out=False, b_out=False):
    if a_out != b_out:
        return True
    c = _change(a, b)
    return c is not None and (c == float('inf') or abs(c) >= MIN_CHANGE_PCT or (a == 0) != (b == 0))


def _context(A, B, overview, feature_a, feature_b, features_diff):
    side = lambda S, rates: {
        'devices': S['devices'], 'boots': S['boots'], 'drive_hours': _r(S['hours'], 1),
        'device_type': S['device_type'],
        'test_cases_failing_pct': _r(_pct(sum(v['fail_tcs'] for v in rates.values()), sum(v['tcs'] for v in rates.values()) or 0) or 0, 1)}
    return {'A': side(A, feature_a), 'B': side(B, feature_b),
            'drive_and_boot_rows': [{'row': k, 'a': _r(a), 'b': _r(b), 'change_pct': _r(_change(a, b), 0) if _change(a, b) not in (None, float('inf')) else None}
                                    for k, a, b, _, _, norm in overview if norm and _differs(a, b)],
            'features_enabled_only_on_one_side': features_diff[:30]}


def _api_callout_rows(rows):
    out = []
    for api, name, kind, a, b in rows:
        if _differs(a['per_h'], b['per_h']) or _differs(a['boots_pct'], b['boots_pct']) or _differs(a['avg_gap_min'], b['avg_gap_min']):
            c = _change(a['per_h'], b['per_h'])
            out.append({'api': name, 'type': kind, 'a_per_drive_h': _r(a['per_h']), 'b_per_drive_h': _r(b['per_h']),
                        'change_pct': None if c in (None, float('inf')) else _r(c, 0),
                        'a_boots_with_calls_pct': _r(a['boots_pct'], 1), 'b_boots_with_calls_pct': _r(b['boots_pct'], 1),
                        'a_avg_gap_min': _r(a['avg_gap_min'], 1), 'b_avg_gap_min': _r(b['avg_gap_min'], 1),
                        'a_max_gap_min': _r(a['max_gap_min'], 1), 'b_max_gap_min': _r(b['max_gap_min'], 1)})
    return out


def _kpi_callout_groups(tables, codes, A, B):
    groups = {}
    for title, (_, _, rows) in tables.items():
        out = []
        for r in rows:
            m = r['metric']
            a_out, b_out = r['a_warn'] is not None, r['b_warn'] is not None
            if m.unit == 'text':
                if r['a'] == r['b'] and a_out == b_out:
                    continue
            elif not _differs(r['a'], r['b'], a_out, b_out):
                continue
            c = _change(r['a'], r['b'])
            row = {'kpi': r['name'], 'basis': r['basis'], 'a': _r(r['a']), 'b': _r(r['b']),
                   'change_pct': None if c in (None, float('inf')) else _r(c, 0)}
            if m.id in kpi_metrics.BASELINES:
                row.update(expected=kpi_metrics.baseline_text(m.id, B['device_type']), a_outside=a_out, b_outside=b_out)
            out.append(row)
        if out:
            groups[title] = out
    code_rows = []
    for r in codes:
        if _differs(r['a'], r['b']):
            c = _change(r['a'], r['b'])
            code_rows.append({'code': r['code'], 'a_alerts_per_drive_h': _r(r['a'], 3), 'b_alerts_per_drive_h': _r(r['b'], 3),
                              'a_alerts': r['a_count'], 'b_alerts': r['b_count'],
                              'change_pct': None if c in (None, float('inf')) else _r(c, 0)})
    code_rows.sort(key=lambda x: -abs((x['b_alerts_per_drive_h'] or 0) - (x['a_alerts_per_drive_h'] or 0)))
    if code_rows:
        groups['Analytics KPIs (alerts per event code)'] = code_rows[:MAX_CODE_ROWS]
    return groups


# ── build ───────────────────────────────────────────────────────────────────────────────

def build(report_a, report_b, labels, out_dir, sides=None):
    """Compare two ota_report.json dicts; labels: (A name, B name); sides: optional
    [{'sub': text under the name, 'href': link to the report}] per side. Writes
    compare_report.json / .html into out_dir and returns the json dict."""
    A, B = _side(report_a), _side(report_b)
    sides = sides or [{}, {}]
    overview = _overview_rows(A, B)
    fs_html, features_diff, _, _ = _feature_sets_section(A, B, labels)
    api_rows = _api_rows(A, B)
    feat_html, rates_a, rates_b = _features_section(A, B, labels)
    kpi_tables = _kpi_rows(A, B)
    codes = _code_rows(A, B)
    context = _context(A, B, overview, rates_a, rates_b, features_diff)
    api_co = report_callouts.build_compare(labels, 'API Call Summary', {'API Call Summary': _api_callout_rows(api_rows)},
                                           context, max_callouts=8)
    # One call over every KPI table, so changes that move together across tables can be linked
    kpi_rows = [dict(r, table=t) for t, rows in _kpi_callout_groups(kpi_tables, codes, A, B).items() for r in rows]
    kpi_co = report_callouts.build_compare(labels, 'KPI Summary', {'KPI Summary': kpi_rows}, context, max_callouts=10)
    doc = {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
        'labels': list(labels), 'sides': sides,
        'ota_versions': [report_a.get('ota_version'), report_b.get('ota_version')],
        'bases': [{k: S[k] for k in ('devices', 'boots', 'minutes', 'device_type')} for S in (A, B)],
        'overview': [{'row': k, 'a': a, 'b': b, 'normalised': n} for k, a, b, _, _, n in overview],
        'features_differ': features_diff,
        'callouts': api_co, 'kpi_callouts': kpi_co,
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'compare_report.json').write_text(json.dumps(doc, indent=1, default=str))
    html = render_html(doc, A, B, overview, fs_html, api_rows, feat_html, kpi_tables, codes)
    (out_dir / 'compare_report.html').write_text(html)
    return doc


def _side_card(k, label, side, S, ota):
    link = f' · <a href="{_esc(side["href"])}" target="_blank" rel="noopener">Open report ↗</a>' if side.get('href') else ''
    ota_line = f'<p>OTA {_esc(ota)}</p>' if ota and ota not in label else ''
    return (f'<div class="side-card {k}"><b>{_esc(label)}</b>{ota_line}'
            f'<p>{_esc(side.get("sub", ""))}{link}</p>'
            f'<p>{S["devices"]} device{"" if S["devices"] == 1 else "s"} · {S["boots"]} boots · '
            f'{S["hours"]:,.1f} h driven{" · " + _esc(S["device_type"]) if S["device_type"] else ""}</p></div>')


def _nav(kpi_tables, codes):
    """Left navigation; KPI Metrics expands into its table headings."""
    out = []
    for i, t in NAV:
        if i != 'kpi':
            out.append(f'<a href="#{i}">{_esc(t)}</a>')
            continue
        subs = []
        for title, (tid, _, _) in kpi_tables.items():
            subs.append((tid, title))
            if tid == 'kpi-alert-upload':
                subs.append(('kpi-codes', 'Analytics KPIs'))
        if ('kpi-codes', 'Analytics KPIs') not in subs:
            subs.append(('kpi-codes', 'Analytics KPIs'))
        out.append(f'<details class="nav-group"><summary>{_esc(t)}</summary><div class="sub">'
                   + ''.join(f'<a href="#{sid}">{_esc(title)}</a>' for sid, title in subs) + '</div></details>')
    return ''.join(out)


def render_html(doc, A, B, overview, fs_html, api_rows, feat_html, kpi_tables, codes):
    labels = doc['labels']
    la, lb = labels
    otas = doc.get('ota_versions') or [None, None]
    body = [f'<div class="top"><h1>{_esc(la)} vs {_esc(lb)}</h1><span>{_esc(doc["generated_at"])}</span></div>',
            '<section id="overview" style="padding:0;border:0;background:none"><div class="sides">'
            + _side_card('a', la, doc['sides'][0], A, otas[0]) + _side_card('b', lb, doc['sides'][1], B, otas[1])
            + '</div></section>',
            _overview_section(overview, labels), fs_html, _api_section(api_rows, labels),
            _callouts_html(doc['callouts'], 'callouts', 'Callouts-API Summary'),
            feat_html, _kpi_head(), _kpi_sections(kpi_tables, labels, _codes_section(codes, labels)),
            _callouts_html(doc['kpi_callouts'], 'kpi-callouts', 'Callouts-KPI Metrics')]
    return _shell(f'{la} vs {lb}', 'Comparison report', f'{la} vs {lb}', _nav(kpi_tables, codes), ''.join(body),
                  f'<script>{REPORT_JS}</script>', STYLE)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('out_dir')
    ap.add_argument('--label-a')
    ap.add_argument('--label-b')
    args = ap.parse_args()
    ra, rb = json.loads(Path(args.a).read_text()), json.loads(Path(args.b).read_text())
    doc = build(ra, rb, (args.label_a or ra.get('ota_version'), args.label_b or rb.get('ota_version')), args.out_dir)
    print(f'{args.out_dir}/compare_report.html: {len(doc["callouts"]["callouts"])} API and '
          f'{len(doc["kpi_callouts"]["callouts"])} KPI callouts')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    main()
