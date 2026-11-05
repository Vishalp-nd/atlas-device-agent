// Log Analyzer UI — talks only to the /log-analyzer API of the Atlas backend.
// API base, first match wins:
//   1. ?api=<base url> in the page URL
//   2. ATLAS_API_URL from .env, delivered as window.ATLAS_API_URL by config.js
//      (log_analyzer/serve_ui.py), e.g. http://172.16.18.142:8502
//   3. the backend on port 8501 of the same host
'use strict';

const BACKEND_PORT = 8501;
const API = (() => {
  const proto = location.protocol === 'https:' ? 'https:' : 'http:';
  const host = location.hostname || 'localhost';
  const toBase = (v) => {
    v = String(v).trim().replace(/\/+$/, '').replace(/\/log-analyzer$/, '');
    return v + '/log-analyzer';
  };
  const given = new URLSearchParams(location.search).get('api');
  if (given) return toBase(given);
  if (window.ATLAS_API_URL) return toBase(window.ATLAS_API_URL);
  return `${proto}//${host}:${BACKEND_PORT}/log-analyzer`;
})();
const ACTIVE = new Set(['queued', 'running']);
const STAGES = { queued: 'Waiting for the previous run', resolve: 'Resolving devices', download: 'Downloading logs',
  analyse: 'Analysing logs', done: 'Done', cancelled: 'Cancelled' };

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, options = {}) {
  const res = await fetch(API + path, { headers: { 'Content-Type': 'application/json' }, ...options });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === 'string' ? body.detail
        : Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join('; ') : detail;
    } catch (_) { /* not json */ }
    throw new Error(detail);
  }
  return res.json();
}

// ── formatting ───────────────────────────────────────────────────────────
function fmtDuration(s) {
  if (s == null) return '—';
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  if (m < 60) return `${m}m ${String(sec).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}
function fmtAgo(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
const fmtTime = (iso) => iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
const fmtDate = (d) => new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const range = (r) => r.start_date === r.end_date ? fmtDate(r.start_date) : `${fmtDate(r.start_date)} – ${fmtDate(r.end_date)}`;
const target = (r) => r.mode === 'ota' ? r.ota_version
  : `${r.requested_devices.length} device${r.requested_devices.length === 1 ? '' : 's'}`
    + (r.options?.otas?.length ? ` on ${r.options.otas.join(', ')}` : '');
const elapsed = (r) => r.duration_s ?? (r.started_at ? (Date.now() - new Date(r.started_at).getTime()) / 1000 : null);
const pill = (status) => `<span class="pill ${esc(status)}">${esc(status)}</span>`;

function progressBar(run) {
  const p = run.progress || {};
  if (!ACTIVE.has(run.status)) return '';
  if (p.total) return `<div class="bar"><span style="width:${Math.max(3, (100 * p.done) / p.total)}%"></span></div>`;
  return '<div class="bar indeterminate"><span></span></div>';
}

// ── environment ──────────────────────────────────────────────────────────
// Staging/production: picks the logs bucket, the device db and the log cache for new
// runs, and which runs the list shows. Remembered per browser.
const ENV_LABELS = { stag: 'Staging', prod: 'Production' };
let envs = {}; // key -> {label, bucket, device_db, log_root}, from /environments
let env = 'stag';
try { env = localStorage.getItem('log-analyzer-env') === 'prod' ? 'prod' : 'stag'; } catch (_) { /* storage blocked */ }
const envBadge = (key) => `<span class="env-badge ${key === 'prod' ? 'prod' : ''}">${esc(ENV_LABELS[key] || key)}</span>`;

function renderEnv() {
  document.querySelectorAll('.env-opt').forEach((b) => b.setAttribute('aria-checked', b.dataset.env === env));
  document.body.classList.toggle('env-prod', env === 'prod');
  const badge = $('#form-env');
  badge.textContent = ENV_LABELS[env];
  badge.className = `env-badge ${env === 'prod' ? 'prod' : ''}`;
  $('#submit').firstElementChild.textContent = env === 'prod' ? 'Run on production' : 'Run analysis';
}

function setEnv(next) {
  if (next === env) return;
  env = next;
  try { localStorage.setItem('log-analyzer-env', env); } catch (_) { /* storage blocked */ }
  renderEnv();
  loadSuggestions();
  if (current?.kind === 'list') renderList();
  else if (current?.kind !== 'comparisons') location.hash = '#/';
}
document.querySelectorAll('.env-opt').forEach((b) => b.addEventListener('click', () => setEnv(b.dataset.env)));

async function loadEnvironments() {
  try {
    const data = await api('/environments');
    envs = Object.fromEntries(data.environments.map((e) => [e.key, e]));
    data.environments.forEach((e) => { ENV_LABELS[e.key] = e.label; });
  } catch (_) { /* the switch still works with the built-in labels */ }
  renderEnv();
}

// ── debug mode ───────────────────────────────────────────────────────────
// Timings, devices and the run log are only shown in debug mode, turned on with a password
// and remembered for the browser tab. This only hides them in the page: the API still
// returns them, so it is not access control.
const DEBUG_PASSWORD = 'Debug@123';
let debug = false;
try { debug = sessionStorage.getItem('log-analyzer-debug') === '1'; } catch (_) { /* storage blocked */ }

function renderDebug() {
  const btn = $('#debug-toggle');
  btn.setAttribute('aria-pressed', debug);
  btn.textContent = debug ? 'Debug on' : 'Debug';
}

$('#debug-toggle').addEventListener('click', () => {
  if (!debug) {
    const given = prompt('Password for debug mode');
    if (given === null) return;
    if (given !== DEBUG_PASSWORD) { alert('Wrong password'); return; }
  }
  debug = !debug;
  try { sessionStorage.setItem('log-analyzer-debug', debug ? '1' : '0'); } catch (_) { /* storage blocked */ }
  renderDebug();
  if (current?.kind === 'run') renderRun(current.id);
});
renderDebug();

// ── form ─────────────────────────────────────────────────────────────────
let mode = 'ota';
const form = $('#run-form');

function setMode(next) {
  mode = next;
  document.querySelectorAll('.seg').forEach((b) => {
    const on = b.dataset.mode === next;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on);
  });
  document.querySelectorAll('[data-for]').forEach((f) => f.classList.toggle('hidden', f.dataset.for !== next));
}
document.querySelectorAll('.seg').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));

const parseDevices = (text) => [...new Set(text.split(/[\s,;]+/).filter(Boolean))];
$('#devices').addEventListener('input', (e) => {
  const n = parseDevices(e.target.value).length;
  $('#device-count').textContent = n ? `· ${n}` : '';
});

(function defaultDates() {
  const iso = (d) => d.toISOString().slice(0, 10);
  const end = new Date(Date.now() - 86400e3);
  const start = new Date(end.getTime() - 6 * 86400e3);
  $('#start').value = iso(start);
  $('#end').value = iso(end);
})();

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const error = $('#form-error');
  error.textContent = '';
  const body = {
    mode,
    start: $('#start').value,
    end: $('#end').value,
    environment: env,
    product: $('#product').value.trim() || null,
    extract_logs: $('#extract_logs').checked,
  };
  if ($('#max_devices').value) body.max_devices = Number($('#max_devices').value);
  if ($('#workers').value) body.workers = Number($('#workers').value);
  if (mode === 'ota') {
    body.ota_version = $('#ota').value.trim();
    if (!body.ota_version) { error.textContent = 'Enter an OTA version.'; return; }
  } else {
    body.devices = parseDevices($('#devices').value);
    if (!body.devices.length) { error.textContent = 'Enter at least one device ID.'; return; }
    body.otas = parseDevices($('#device-otas').value);
  }
  if (!body.start || !body.end) { error.textContent = 'Pick a date range.'; return; }
  if (body.start > body.end) { error.textContent = '“From” must be on or before “To”.'; return; }
  if (env === 'prod' && !confirm(`Start a production run?\nLogs are downloaded from s3://${envs.prod?.bucket || 'idms-production'}.`)) return;

  const button = $('#submit');
  button.disabled = true;
  button.firstElementChild.textContent = 'Starting…';
  try {
    const run = await api('/runs', { method: 'POST', body: JSON.stringify(body) });
    location.hash = `#/runs/${run.id}`;
  } catch (err) {
    error.textContent = err.message;
  } finally {
    button.disabled = false;
    renderEnv();
  }
});

async function loadSuggestions() {
  try {
    const { otas } = await api(`/otas?environment=${env}`);
    $('#ota-list').innerHTML = otas.map((o) => `<option value="${esc(o)}">`).join('');
  } catch (_) { /* suggestions are optional */ }
}

// ── views ────────────────────────────────────────────────────────────────
const view = $('#view');
let timer = null;
let current = null; // {kind: 'list'|'run', ...}

function schedule(fn, ms) {
  clearTimeout(timer);
  timer = setTimeout(fn, ms);
}

let listFilter = '';

async function renderList() {
  current = { kind: 'list' };
  let runs;
  try {
    ({ runs } = await api(`/runs?limit=100&environment=${env}${listFilter ? `&status=${listFilter}` : ''}`));
  } catch (err) {
    view.innerHTML = `<div class="alert">${esc(err.message)}</div>`;
    schedule(renderList, 10000);
    return;
  }
  if (current.kind !== 'list') return;
  const filters = ['', 'running', 'succeeded', 'failed'].map((f) =>
    `<button class="filter ${f === listFilter ? 'active' : ''}" data-filter="${f}">${f || 'All'}</button>`).join('');
  view.innerHTML = `
    <div class="list-head"><div class="left">${viewTabs('runs')}${envBadge(env)}</div><div class="filters">${filters}</div></div>
    ${runs.length ? `<ul class="runs">${runs.map((r) => `
      <li><a class="run" href="#/runs/${r.id}">
        <span class="id">#${r.id}</span>
        <span class="target">${esc(target(r))}</span>
        <span class="right">${pill(r.status)}<span class="muted" style="font-size:12px">${fmtDuration(elapsed(r))}</span></span>
        <span class="meta"><span>${esc(range(r))}</span>
          ${r.devices.length ? `<span>${r.devices.length} device${r.devices.length === 1 ? '' : 's'}</span>` : ''}
          <span>${fmtAgo(r.created_at)}</span>
          ${ACTIVE.has(r.status) && r.stage ? `<span>· ${esc(STAGES[r.stage] || r.stage)}</span>` : ''}</span>
        ${progressBar(r) ? `<span class="bar-wrap" style="grid-column:2 / span 2">${progressBar(r)}</span>` : ''}
      </a></li>`).join('')}</ul>`
    : $('#tpl-empty').innerHTML}`;
  view.querySelectorAll('.filter').forEach((b) => b.addEventListener('click', () => {
    listFilter = b.dataset.filter;
    renderList();
  }));
  schedule(renderList, runs.some((r) => ACTIVE.has(r.status)) ? 4000 : 20000);
}

const viewTabs = (active) => `<nav class="view-tabs" aria-label="Show">
  <a href="#/" class="${active === 'runs' ? 'active' : ''}">Runs</a>
  <a href="#/comparisons" class="${active === 'comparisons' ? 'active' : ''}">Comparisons</a></nav>`;

function summaryLine(s) {
  if (!s || s.devices_given == null) return '';
  const hours = (s.drive_minutes_total / 60).toFixed(1);
  return `<div class="legend">
    <span><b>${s.devices_active}</b> / ${s.devices_given} active</span>
    <span><b>${s.devices_detailed_logs}</b> detailed logs</span>
    <span><b>${hours}</b> h driven</span>
    <span><b>${s.devices_over_6h}</b> &gt; 6 h · <b>${s.devices_over_24h}</b> &gt; 24 h</span>
    <span><b>${s.devices_low_power}</b> low power</span>
  </div>`;
}

function verdictCard(rep, run) {
  const total = rep.pass + rep.fail || 1;
  const w = (n) => `${(100 * n) / total}%`;
  const picked = isPicked(run.id, rep.ota_version);
  return `
    <div class="report${picked ? ' picked' : ''}" data-run="${run.id}" data-ota="${esc(rep.ota_version)}" data-env="${esc(run.environment)}">
      <button type="button" class="dots" aria-label="More actions" aria-haspopup="menu" aria-expanded="false">⋯</button>
      <div class="top"><span class="ota" title="${esc(rep.ota_version)}">${esc(rep.ota_version)}</span>
        <span class="sub">${rep.devices} device${rep.devices === 1 ? '' : 's'} · ${rep.boots} boot${rep.boots === 1 ? '' : 's'}</span></div>
      <div class="verdicts" title="Test-case verdicts, one per test case per boot">
        <span class="v-fail" style="width:${w(rep.fail)}"></span><span class="v-pass" style="width:${w(rep.pass)}"></span>
      </div>
      <div class="legend">
        <span><i class="v-fail"></i><b>${rep.fail.toLocaleString()}</b> fail</span>
        <span><i class="v-pass"></i><b>${rep.pass.toLocaleString()}</b> pass</span>
      </div>
      ${summaryLine(rep.summary)}
      <div class="legend">${rep.config_error
        ? `<span>Config check unavailable: ${esc(rep.config_error)}</span>`
        : `<span><b>${rep.config_keys_failing}</b> of ${rep.config_keys} config keys missing in at least one boot</span>`}</div>
      <div><a class="button" href="${esc(API.replace(/\/log-analyzer$/, '') + rep.url)}" target="_blank" rel="noopener">Open report ↗</a></div>
    </div>`;
}

function stepsTable(steps) {
  if (!steps.length) return '<p class="muted">Timings appear when the run finishes.</p>';
  const max = Math.max(...steps.map((s) => s.total_s || 0), 0.001);
  const labels = { queue: 'Queue wait', resolve: 'Resolve devices', download_all: 'Download (all)', download: 'Download per device',
    analyse: 'Analyse (all)', load_tests: 'Load test cases', process_devices: 'Process devices', read_day: 'Read day',
    rebuild_day: 'Rebuild day from archives', cfg_parse: 'CFG_PRSR parse', ota_lookup: 'OTA lookup', grep_tests: 'Grep test patterns',
    drive_scan: 'Grep drive / power metrics', evidence_grep: 'Grep config value lines',
    override_llm: 'Read override values (LLM)', features: 'Features enabled (all devices)',
    detect_boots: 'Detect boots', load_hits: 'Assign hits to boots', evaluate: 'Evaluate boots', device_total: 'Device total',
    write_ota_report: 'Write OTA report', callouts: 'Callouts (rule checks + LLM)', fetch_ini: 'Fetch config ini', run_total: 'Run total' };
  return `<div class="tbl"><table>
    <tr><th>Step</th><th class="num">Count</th><th class="num">Total</th><th class="num">Avg</th><th class="num">Max</th><th>Slowest</th><th style="width:22%"></th></tr>
    ${steps.map((s) => `<tr>
      <td>${esc(labels[s.step] || s.step)}${s.any_failed ? ' <span class="pill failed">failed</span>' : ''}</td>
      <td class="num">${s.count}</td><td class="num">${fmtDuration(s.total_s)}</td>
      <td class="num">${fmtDuration(s.avg_s)}</td><td class="num">${fmtDuration(s.max_s)}</td>
      <td class="mono">${esc(s.slowest_device || '')}</td>
      <td><div class="timebar"><span style="width:${(100 * (s.total_s || 0)) / max}%"></span></div></td></tr>`).join('')}
  </table></div>`;
}

function devicesTable(devices, errors) {
  if (!devices.length && !Object.keys(errors).length) return '';
  const rows = new Map(devices.map((d) => [d.device, d]));
  Object.keys(errors).forEach((d) => rows.has(d) || rows.set(d, { device: d }));
  return `<h3>Devices</h3><div class="tbl"><table>
    <tr><th>Device</th><th class="num">Days</th><th class="num">Download</th><th class="num">Analysis</th><th>Error</th></tr>
    ${[...rows.values()].map((d) => `<tr><td class="mono">${esc(d.device)}</td><td class="num">${d.days ?? '—'}</td>
      <td class="num">${fmtDuration(d.download_s)}</td><td class="num">${fmtDuration(d.analyse_s)}</td>
      <td>${errors[d.device] ? `<span style="color:var(--bad)">${esc(errors[d.device])}</span>` : ''}</td></tr>`).join('')}
  </table></div>`;
}

function colorLog(text) {
  return esc(text).split('\n').map((line) => {
    if (/\| (ERROR|CRITICAL) \|/.test(line)) return `<span class="le">${line}</span>`;
    if (/\| WARNING \|/.test(line)) return `<span class="lw">${line}</span>`;
    if (/ took \d/.test(line)) return line.replace(/(took [\d.]+s)/, '<span class="lt">$1</span>');
    return line;
  }).join('\n');
}

async function renderRun(id) {
  const state = { kind: 'run', id, logOffset: 0, follow: true };
  current = state;
  view.innerHTML = '<a class="back" href="#/">← All runs</a><div class="skeleton" style="width:40%"></div><div class="skeleton"></div><div class="skeleton" style="width:70%"></div>';

  async function refresh() {
    if (current !== state) return;
    let detail;
    try {
      detail = await api(`/runs/${id}`);
    } catch (err) {
      view.innerHTML = `<a class="back" href="#/">← All runs</a><div class="alert">${esc(err.message)}</div>`;
      return;
    }
    if (current !== state) return;
    const { run, reports, steps, devices } = detail;
    const active = ACTIVE.has(run.status);
    const p = run.progress || {};
    const stageLabel = STAGES[run.stage] || run.stage || '';
    const logEl = $('#log');
    const logHtml = logEl ? logEl.innerHTML : '';

    view.innerHTML = `
      <a class="back" href="#/">← All runs</a>
      <div class="detail-head">
        <div>
          <h2>${esc(target(run))} ${pill(run.status)}</h2>
          <div class="sub"><span>Run #${run.id}</span><span>${esc(range(run))}</span>
            <span>${run.mode === 'ota' ? 'by OTA' : 'by devices'}</span>
            ${envBadge(run.environment)}</div>
        </div>
        <div class="actions">
          ${active ? '<button class="ghost danger" id="cancel">Cancel</button>' : '<button class="ghost" id="rerun">Run again</button>'}
        </div>
      </div>
      <div class="facts">
        <div class="fact"><div class="k">Devices</div><div class="v">${run.devices.length || (run.mode === 'devices' ? run.requested_devices.length : '—')}</div></div>
        <div class="fact"><div class="k">Start date</div><div class="v">${fmtDate(run.start_date)}</div></div>
        <div class="fact"><div class="k">End date</div><div class="v">${fmtDate(run.end_date)}</div></div>
        <div class="fact"><div class="k">Duration</div><div class="v">${fmtDuration(elapsed(run))}</div></div>
        <div class="fact"><div class="k">Created</div><div class="v">${fmtTime(run.created_at)}</div></div>
        <div class="fact"><div class="k">Finished</div><div class="v">${fmtTime(run.finished_at)}</div></div>
      </div>
      ${active ? `<div class="stage"><div class="label"><span>${esc(stageLabel)}${p.device ? ` · ${esc(p.device)}` : ''}</span>
        <span>${p.total ? `${p.done} / ${p.total}` : ''}</span></div>${progressBar(run)}</div>` : ''}
      ${run.error ? `<div class="alert${run.status === 'succeeded' ? ' warn' : ''}">${esc(run.error)}</div>` : ''}
      ${Object.keys(run.device_errors || {}).length ? `<div class="alert warn">${Object.keys(run.device_errors).length} device(s) failed${debug ? ' — see Devices below' : ''}.</div>` : ''}
      ${reports.length ? `<h3>Reports</h3><div class="reports">${reports.map((rep) => verdictCard(rep, run)).join('')}</div>` : ''}
      ${debug ? `<h3>Timings</h3>${stepsTable(steps)}
      ${devicesTable(devices, run.device_errors || {})}
      <div class="log-head"><h3>Log</h3><label class="muted"><input type="checkbox" id="follow" ${state.follow ? 'checked' : ''}> Follow</label></div>
      <pre class="log" id="log">${logHtml}</pre>` : ''}`;

    const follow = $('#follow');
    if (follow) follow.addEventListener('change', (e) => { state.follow = e.target.checked; });
    const cancel = $('#cancel');
    if (cancel) cancel.addEventListener('click', async () => {
      if (!confirm(`Cancel run #${run.id}?`)) return;
      cancel.disabled = true;
      try { await api(`/runs/${run.id}/cancel`, { method: 'POST' }); } catch (err) { alert(err.message); }
      refresh();
    });
    const again = $('#rerun');
    if (again) again.addEventListener('click', async () => {
      again.disabled = true;
      try {
        const next = await api(`/runs/${run.id}/rerun`, { method: 'POST' });
        location.hash = `#/runs/${next.id}`;
      } catch (err) { alert(err.message); again.disabled = false; }
    });

    await pullLog(state);
    if (active) schedule(refresh, 3000);
  }

  async function pullLog(st) {
    if (!debug) return;
    try {
      const chunk = await api(`/runs/${id}/log?offset=${st.logOffset}`);
      if (current !== st) return;
      const el = $('#log');
      if (chunk.text) {
        const placeholder = el.querySelector(':scope > .muted');
        if (placeholder) placeholder.remove();
        el.insertAdjacentHTML('beforeend', colorLog(chunk.text));
        st.logOffset = chunk.offset;
      }
      if (!el.textContent) el.innerHTML = '<span class="muted">No log output yet.</span>';
      if (st.follow) el.scrollTop = el.scrollHeight;
    } catch (_) { /* log is best effort */ }
  }

  refresh();
}

function route() {
  clearTimeout(timer);
  const m = location.hash.match(/^#\/runs\/(\d+)/);
  const c = location.hash.match(/^#\/compare\/(\d+)/);
  if (m) renderRun(Number(m[1]));
  else if (c) renderCompare(Number(c[1]));
  else if (location.hash.startsWith('#/comparisons')) renderComparisons();
  else renderList();
}

async function health() {
  const el = $('#health');
  try {
    await api('/health');
    el.className = 'health ok';
    el.querySelector('.label').textContent = 'connected';
  } catch (err) {
    el.className = 'health down';
    el.querySelector('.label').textContent = 'unavailable';
    el.title = err.message;
  }
}

// ── report comparison ────────────────────────────────────────────────────
// The floating button opens a panel: compare two OTAs or two devices over a date range (a
// normal run per side, then the comparison report), or two existing reports picked with
// "Add to compare" in a report tile's ⋯ menu (kept for the browser, at most 2).
const CMP_KEY = 'log-analyzer-compare';
let cmpPicked = [];
try { cmpPicked = JSON.parse(localStorage.getItem(CMP_KEY) || '[]').slice(0, 2); } catch (_) { cmpPicked = []; }
const isPicked = (runId, ota) => cmpPicked.some((p) => p.run_id === runId && p.ota_version === ota);
const fab = $('#cmp-fab'), panel = $('#cmp-panel');

function saveCmp() {
  try { localStorage.setItem(CMP_KEY, JSON.stringify(cmpPicked)); } catch (_) { /* storage blocked */ }
  const n = cmpPicked.length;
  $('#cmp-count').hidden = !n;
  $('#cmp-count').textContent = `${n}/2`;
  document.querySelectorAll('.report[data-run]').forEach((el) =>
    el.classList.toggle('picked', isPicked(Number(el.dataset.run), el.dataset.ota)));
  renderPicked();
}

function togglePick(item) {
  if (isPicked(item.run_id, item.ota_version)) {
    cmpPicked = cmpPicked.filter((p) => !(p.run_id === item.run_id && p.ota_version === item.ota_version));
  } else {
    if (cmpPicked.length >= 2) { alert('Only 2 reports can be compared. Remove one from the comparison first.'); return; }
    cmpPicked.push(item);
  }
  saveCmp();
  if (cmpPicked.length === 2) openPanel();
}

function renderPicked() {
  const box = $('#cmp-picked');
  // Picked reports are compared as they are: the new comparison form only shows without them
  $('#cmp-form').hidden = cmpPicked.length > 0;
  if (!cmpPicked.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<h3>Selected reports · ${cmpPicked.length}/2</h3><ul class="cmp-list">${cmpPicked.map((p, i) => `
    <li><span class="cmp-tag ${i ? 'b' : 'a'}">${i ? 'B' : 'A'}</span>
      <span class="mono" title="${esc(p.ota_version)}">${esc(p.ota_version)}</span>
      <a href="#/runs/${p.run_id}" class="muted" style="flex:0 0 auto">#${p.run_id}</a>${envBadge(p.environment)}
      <button type="button" data-unpick="${i}" aria-label="Remove">×</button></li>`).join('')}</ul>
    <button class="primary" type="button" id="cmp-reports" ${cmpPicked.length === 2 ? '' : 'disabled'}>
      <span>${cmpPicked.length === 2 ? 'Compare selected reports' : 'Add one more report to compare'}</span></button>`;
  box.querySelectorAll('[data-unpick]').forEach((b) => b.addEventListener('click', () => {
    cmpPicked.splice(Number(b.dataset.unpick), 1);
    saveCmp();
  }));
  const go = $('#cmp-reports');
  if (go) go.addEventListener('click', async () => {
    go.disabled = true;
    try {
      const c = await api('/comparisons/reports', { method: 'POST', body: JSON.stringify({
        items: cmpPicked.map((p) => ({ run_id: p.run_id, ota_version: p.ota_version })) }) });
      cmpPicked = [];
      saveCmp();
      closePanel();
      location.hash = `#/compare/${c.id}`;
    } catch (err) { alert(err.message); go.disabled = false; }
  });
}

const cmpName = (c) => c.items.map((i) => i.label || i.target).join(' vs ');

async function renderRecent() {
  const box = $('#cmp-recent');
  try {
    const { comparisons } = await api('/comparisons?limit=5');
    box.innerHTML = comparisons.length ? `<h3><a href="#/comparisons" id="cmp-all">Recent comparisons <span aria-hidden="true">›</span></a></h3><ul class="cmp-list">${comparisons.map((c) => `
      <li><a href="#/compare/${c.id}" title="${esc(cmpName(c))}">${esc(cmpName(c))}</a>${pill(c.status)}</li>`).join('')}</ul>` : '';
    box.querySelectorAll('a').forEach((a) => a.addEventListener('click', closePanel));
  } catch (_) { box.innerHTML = ''; }
}

function openPanel() {
  panel.hidden = false;
  fab.setAttribute('aria-expanded', 'true');
  if (!$('#cmp-start').value) { $('#cmp-start').value = $('#start').value; $('#cmp-end').value = $('#end').value; }
  $('#cmp-a-env').value = $('#cmp-a-env').dataset.touched ? $('#cmp-a-env').value : env;
  $('#cmp-b-env').value = $('#cmp-b-env').dataset.touched ? $('#cmp-b-env').value : env;
  renderPicked();
  renderRecent();
}
function closePanel() {
  panel.hidden = true;
  fab.setAttribute('aria-expanded', 'false');
}
fab.addEventListener('click', openPanel);
$('#cmp-close').addEventListener('click', closePanel);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) closePanel(); });
['#cmp-a-env', '#cmp-b-env'].forEach((sel) => $(sel).addEventListener('change', (e) => { e.target.dataset.touched = '1'; }));

const DEVICE_ID = /^\d{6,14}$/;
$('#cmp-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const error = $('#cmp-error');
  error.textContent = '';
  const a = $('#cmp-a').value.trim(), b = $('#cmp-b').value.trim();
  const start = $('#cmp-start').value, end = $('#cmp-end').value;
  if (!a || !b) { error.textContent = 'Enter a device ID or an OTA version on both sides.'; return; }
  if (DEVICE_ID.test(a) !== DEVICE_ID.test(b)) { error.textContent = 'Both sides must be OTA versions, or both device IDs.'; return; }
  if (!start || !end) { error.textContent = 'Pick a date range.'; return; }
  if (start > end) { error.textContent = '“From” must be on or before “To”.'; return; }
  const items = [{ target: a, environment: $('#cmp-a-env').value }, { target: b, environment: $('#cmp-b-env').value }];
  if (items.some((i) => i.environment === 'prod') && !confirm('Start production runs for this comparison?')) return;
  const button = $('#cmp-submit');
  button.disabled = true;
  button.firstElementChild.textContent = 'Starting…';
  try {
    const c = await api('/comparisons', { method: 'POST', body: JSON.stringify({ start, end, items }) });
    closePanel();
    location.hash = `#/compare/${c.id}`;
  } catch (err) {
    error.textContent = err.message;
  } finally {
    button.disabled = false;
    button.firstElementChild.textContent = 'Compare';
  }
});

// Report tiles: ⋯ opens a small menu with Add to / Remove from compare
let openMenu = null;
function closeMenu() {
  if (!openMenu) return;
  openMenu.remove();
  document.querySelectorAll('.report .dots[aria-expanded="true"]').forEach((b) => b.setAttribute('aria-expanded', 'false'));
  openMenu = null;
}
document.addEventListener('click', (e) => {
  const dots = e.target.closest('.report .dots');
  if (!dots) { if (openMenu && !openMenu.contains(e.target)) closeMenu(); return; }
  e.preventDefault();
  const tile = dots.closest('.report');
  const wasOpen = dots.getAttribute('aria-expanded') === 'true';
  closeMenu();
  if (wasOpen) return;
  const item = { run_id: Number(tile.dataset.run), ota_version: tile.dataset.ota, environment: tile.dataset.env };
  const picked = isPicked(item.run_id, item.ota_version);
  const full = !picked && cmpPicked.length >= 2;
  openMenu = document.createElement('div');
  openMenu.className = 'tile-menu';
  openMenu.setAttribute('role', 'menu');
  openMenu.innerHTML = `<button type="button" role="menuitem" ${full ? 'disabled title="Only 2 reports can be compared"' : ''}>
    ${picked ? 'Remove from compare' : full ? 'Add to compare (2 already selected)' : 'Add to compare'}</button>`;
  openMenu.querySelector('button').addEventListener('click', () => { closeMenu(); togglePick(item); });
  tile.appendChild(openMenu);
  dots.setAttribute('aria-expanded', 'true');
});

async function renderCompare(id) {
  const state = { kind: 'compare', id };
  current = state;
  view.innerHTML = '<a class="back" href="#/comparisons">← All comparisons</a><div class="skeleton" style="width:40%"></div><div class="skeleton"></div>';
  async function refresh() {
    if (current !== state) return;
    let c;
    try { c = await api(`/comparisons/${id}`); } catch (err) {
      view.innerHTML = `<a class="back" href="#/comparisons">← All comparisons</a><div class="alert">${esc(err.message)}</div>`;
      return;
    }
    if (current !== state) return;
    const active = ACTIVE.has(c.status);
    const runs = Object.fromEntries((c.runs || []).map((r) => [r.id, r]));
    const kindLabel = { ota: 'Two OTA versions', devices: 'Two devices', reports: 'Two existing reports' }[c.kind] || c.kind;
    const stage = { waiting: 'Waiting for both reports', building: 'Building the comparison report' }[c.stage] || '';
    const sideCard = (item, i) => {
      const r = runs[item.run_id];
      const p = r?.progress || {};
      return `<div class="side"><div><span class="cmp-tag ${i ? 'b' : 'a'}">${i ? 'B' : 'A'}</span>
          <span class="name" title="${esc(item.label || item.target)}">${esc(item.label || item.target)}</span></div>
        <div class="legend"><a href="#/runs/${item.run_id}">Run #${item.run_id}</a>${envBadge(item.environment)}
          ${r ? pill(r.status) : ''}${r && ACTIVE.has(r.status) ? `<span>${esc(STAGES[r.stage] || r.stage || '')}${p.total ? ` · ${p.done} / ${p.total}` : ''}</span>` : ''}</div>
        ${r && ACTIVE.has(r.status) ? progressBar(r) : ''}</div>`;
    };
    view.innerHTML = `
      <a class="back" href="#/comparisons">← All comparisons</a>
      <div class="detail-head"><div>
        <h2>${esc(cmpName(c))} ${pill(c.status)}</h2>
        <div class="sub"><span>Comparison #${c.id}</span><span>${esc(kindLabel)}</span>
          ${c.start_date ? `<span>${esc(range(c))}</span>` : ''}<span>${fmtAgo(c.created_at)}</span></div>
      </div>
      <div class="actions">${c.url ? `<a class="button" href="${esc(API.replace(/\/log-analyzer$/, '') + c.url)}" target="_blank" rel="noopener">Open comparison report ↗</a>` : ''}</div></div>
      ${active && stage ? `<div class="stage"><div class="label"><span>${esc(stage)}</span></div><div class="bar indeterminate"><span></span></div></div>` : ''}
      ${c.error ? `<div class="alert">${esc(c.error)}</div>` : ''}
      <div class="sides">${c.items.map(sideCard).join('')}</div>
      ${c.kind !== 'reports' && active ? '<p class="hint" style="margin-top:14px">Each side runs as a normal analysis (listed under Runs); the comparison report is built when both are done.</p>' : ''}`;
    if (active) schedule(refresh, 4000);
  }
  refresh();
}
// All comparisons, like the runs list (every environment: a side carries its own)
const CMP_KINDS = { ota: 'OTA vs OTA', devices: 'Device vs device', reports: 'Existing reports' };
async function renderComparisons() {
  const state = { kind: 'comparisons' };
  current = state;
  let comparisons;
  try {
    ({ comparisons } = await api('/comparisons?limit=100'));
  } catch (err) {
    view.innerHTML = `<div class="alert">${esc(err.message)}</div>`;
    schedule(renderComparisons, 10000);
    return;
  }
  if (current !== state) return;
  view.innerHTML = `
    <div class="list-head"><div class="left">${viewTabs('comparisons')}</div></div>
    ${comparisons.length ? `<ul class="runs">${comparisons.map((c) => `
      <li><a class="run" href="#/compare/${c.id}">
        <span class="id">C${c.id}</span>
        <span class="target" title="${esc(cmpName(c))}">${esc(cmpName(c))}</span>
        <span class="right">${pill(c.status)}<span class="muted" style="font-size:12px">${esc(CMP_KINDS[c.kind] || c.kind)}</span></span>
        <span class="meta">${c.start_date ? `<span>${esc(range(c))}</span>` : ''}
          ${[...new Set(c.items.map((i) => i.environment))].map(envBadge).join('')}
          <span>${c.items.map((i) => `#${i.run_id}`).join(' · ')}</span>
          <span>${fmtAgo(c.created_at)}</span></span>
      </a></li>`).join('')}</ul>`
    : '<div class="empty"><div class="empty-mark" aria-hidden="true"></div><p>No comparisons yet. Start one with Report Comparison.</p></div>'}`;
  schedule(renderComparisons, comparisons.some((c) => ACTIVE.has(c.status)) ? 4000 : 20000);
}

saveCmp();

window.addEventListener('hashchange', route);
renderEnv();
loadEnvironments();
health();
loadSuggestions();
route();
