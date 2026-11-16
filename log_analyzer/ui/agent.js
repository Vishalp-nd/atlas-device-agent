// Agent mode — a chat with Claude about the run or comparison open on the left.
// Sign-in is DeviceQAI's (an emailed code for allowed addresses), then the person's own
// Claude token (`claude setup-token`); every answer is a Claude Code run on the server with
// that token, limited to this run's devices, its dates ±1 day, and its reports.
// Talks to /log-analyzer/agent (log_analyzer/agent/router.py); app.js opens and closes it.
'use strict';

window.LAAgent = (() => {
  const SESSION_KEY = 'la-agent-session';
  const MODEL_KEY = 'la-agent-model';
  const box = document.getElementById('agent');
  const h = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const base = () => API + '/agent'; // API: app.js

  let target = null;        // {kind: 'run'|'comparison', id}
  let info = null;          // GET /session
  let chat = null;          // the open chat
  let poll = null;
  let pending = {};         // sign-in form state
  let model = 'sonnet';
  try { model = localStorage.getItem(MODEL_KEY) || 'sonnet'; } catch (_) { /* storage blocked */ }

  // ── session storage ────────────────────────────────────────────────────
  const saved = () => { try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null'); } catch (_) { return null; } };
  const save = (v) => { try { v ? localStorage.setItem(SESSION_KEY, JSON.stringify(v)) : localStorage.removeItem(SESSION_KEY); } catch (_) { /* blocked */ } };

  class ApiError extends Error {
    constructor(message, status, state) { super(message); this.status = status; this.state = state; }
  }

  async function call(path, options = {}) {
    const s = saved();
    const headers = { 'Content-Type': 'application/json' };
    if (s?.session) headers.Authorization = `Bearer ${s.session}`;
    let res;
    try {
      res = await fetch(base() + path, { ...options, headers, cache: 'no-store' });
    } catch (err) { throw new ApiError(`Can't reach the server: ${err.message}`, 0); }
    let body = {};
    try { body = await res.json(); } catch (_) { /* not json */ }
    if (!res.ok) {
      const d = body.detail;
      const msg = typeof d === 'string' ? d : d?.error || (Array.isArray(d) ? d.map((x) => x.msg).join('; ') : res.statusText);
      if (res.status === 401) { save(null); info = info && { ...info, signed_in: false }; }
      throw new ApiError(msg, res.status, d?.state);
    }
    return body;
  }

  // ── markdown (what Claude writes: headings, lists, code, tables, emphasis, links) ──
  function inline(s) {
    return h(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function md(text) {
    const lines = String(text || '').replace(/\r/g, '').split('\n');
    const out = [];
    let i = 0;
    const isTable = (l, n) => /^\s*\|.*\|\s*$/.test(l) && /^\s*\|?\s*:?-{2,}/.test(n || '');
    while (i < lines.length) {
      const l = lines[i];
      const fence = l.match(/^\s*```(\w*)/);
      if (fence) {
        const code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i++;
        out.push(`<pre class="ag-code"><code>${h(code.join('\n'))}</code></pre>`);
        continue;
      }
      if (isTable(l, lines[i + 1])) {
        const row = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
        const head = row(l);
        i += 2;
        const body = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) body.push(row(lines[i++]));
        out.push(`<div class="ag-tbl"><table><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</table></div>`);
        continue;
      }
      const hd = l.match(/^(#{1,4})\s+(.*)/);
      if (hd) { out.push(`<h${Math.min(hd[1].length + 2, 6)} class="ag-h">${inline(hd[2])}</h${Math.min(hd[1].length + 2, 6)}>`); i++; continue; }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
        const ordered = /^\s*\d+[.)]/.test(l);
        const items = [];
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          let item = lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, '');
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) item += ' ' + lines[i++].trim();
          items.push(`<li>${inline(item)}</li>`);
        }
        out.push(ordered ? `<ol>${items.join('')}</ol>` : `<ul>${items.join('')}</ul>`);
        continue;
      }
      if (/^\s*>/.test(l)) {
        const q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ''));
        out.push(`<blockquote>${inline(q.join(' '))}</blockquote>`);
        continue;
      }
      if (/^\s*(---|\*\*\*)\s*$/.test(l)) { out.push('<hr>'); i++; continue; }
      if (!l.trim()) { i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,4}\s|[-*+]\s|\d+[.)]\s|>)/.test(lines[i]) && !isTable(lines[i], lines[i + 1])) para.push(lines[i++]);
      out.push(`<p>${inline(para.join('\n')).replace(/\n/g, '<br>')}</p>`);
    }
    return out.join('');
  }

  // ── frame ──────────────────────────────────────────────────────────────
  const SPARK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9z" fill="currentColor"/><path d="M19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2-2.2-.8 2.2-.8z" fill="currentColor" opacity=".7"/></svg>';
  const targetLabel = () => target ? (target.kind === 'run' ? `Run #${target.id}` : `Comparison #${target.id}`) : '';

  function frame(body, { sub = '', tools = '' } = {}) {
    box.innerHTML = `
      <header class="ag-head">
        <div class="ag-title"><span class="ag-mark">${SPARK}</span><div><h2>Log Agent</h2>
          <div class="ag-sub">${sub || h(targetLabel())}</div></div></div>
        <div class="ag-tools">${tools}<a class="ag-x" href="${target ? (target.kind === 'run' ? `#/runs/${target.id}` : `#/compare/${target.id}`) : '#/'}" title="Leave agent mode" aria-label="Leave agent mode">×</a></div>
      </header>
      <div class="ag-body">${body}</div>`;
  }

  function busyBtn(btn, on, label) {
    btn.disabled = on;
    btn.classList.toggle('loading', on);
    if (label) btn.querySelector('span:last-child').textContent = label;
  }

  // ── sign-in: email → code ──────────────────────────────────────────────
  function renderEmail(error = '') {
    frame(`
      <form class="ag-gate" id="ag-email" novalidate>
        <div class="ag-gate-mark">${SPARK}</div>
        <h3>Sign in to use the agent</h3>
        <p class="ag-lead">Ask questions about ${h(targetLabel())}: its devices' logs, its dates and its report. Claude answers on the server with <b>your own</b> Claude account.</p>
        <label for="ag-mail">Work email</label>
        <div class="ag-field"><input id="ag-mail" type="email" autocomplete="username email" placeholder="you@${h(info?.domain || 'netradyne.com')}" value="${h(pending.email || saved()?.email || '')}" required></div>
        <p class="form-error">${h(error)}</p>
        <button class="primary" type="submit"><span class="spin" aria-hidden="true"></span><span>Email me a code</span></button>
        <p class="hint">Only addresses the DeviceQAI admin has allowed can sign in.</p>
      </form>`);
    const form = box.querySelector('#ag-email');
    const input = box.querySelector('#ag-mail');
    input.focus();
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const email = input.value.trim();
      if (!email) { renderEmail('Enter your email.'); return; }
      busyBtn(form.querySelector('button'), true);
      try {
        const r = await call('/auth/request', { method: 'POST', body: JSON.stringify({ email }) });
        pending = { email: r.email, via: r.via, resendAt: Date.now() + r.resend_in * 1000 };
        renderCode();
      } catch (err) {
        pending.email = email;
        if (err.state === 'not_allowed') renderDenied(email);
        else renderEmail(err.message);
      }
    });
  }

  function renderDenied(email) {
    frame(`<div class="ag-gate"><div class="ag-gate-mark warn">!</div><h3>No access yet</h3>
      <p class="ag-lead"><b>${h(email)}</b> isn't on the DeviceQAI allowlist. Ask the admin to add you, then sign in again.</p>
      <button class="primary" type="button" id="ag-other"><span>Try another email</span></button></div>`);
    box.querySelector('#ag-other').addEventListener('click', () => renderEmail());
  }

  function renderCode(error = '') {
    frame(`
      <form class="ag-gate" id="ag-code" novalidate>
        <div class="ag-gate-mark">${SPARK}</div>
        <h3>Check your email</h3>
        <p class="ag-lead">Enter the 6-digit code sent to <b>${h(pending.email)}</b>.</p>
        ${pending.via === 'console' ? '<p class="ag-note">Mail is off on this server: the code is printed in the backend\'s console (<code>docker logs atlas-agent-api</code>).</p>' : ''}
        <div class="ag-field"><input id="ag-digits" class="ag-otp" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="••••••" required></div>
        <p class="form-error">${h(error)}</p>
        <button class="primary" type="submit"><span class="spin" aria-hidden="true"></span><span>Sign in</span></button>
        <div class="ag-links"><button type="button" class="ag-link" id="ag-change">Use another email</button>
          <button type="button" class="ag-link" id="ag-resend" disabled>Resend code</button></div>
      </form>`);
    const form = box.querySelector('#ag-code');
    const input = box.querySelector('#ag-digits');
    input.focus();
    input.addEventListener('input', () => {
      input.value = input.value.replace(/\D/g, '').slice(0, 6);
      if (input.value.length === 6) form.requestSubmit();
    });
    const resend = box.querySelector('#ag-resend');
    const tick = () => {
      if (!box.contains(resend)) return;
      const left = Math.ceil((pending.resendAt - Date.now()) / 1000);
      resend.disabled = left > 0;
      resend.textContent = left > 0 ? `Resend in ${left}s` : 'Resend code';
      if (left > 0) setTimeout(tick, 1000);
    };
    tick();
    resend.addEventListener('click', async () => {
      try {
        const r = await call('/auth/request', { method: 'POST', body: JSON.stringify({ email: pending.email }) });
        pending.resendAt = Date.now() + r.resend_in * 1000;
        renderCode();
      } catch (err) { renderCode(err.message); }
    });
    box.querySelector('#ag-change').addEventListener('click', () => renderEmail());
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (input.value.length !== 6) { renderCode('Enter the 6 digits.'); return; }
      busyBtn(form.querySelector('button.primary'), true);
      try {
        const r = await call('/auth/verify', { method: 'POST', body: JSON.stringify({ email: pending.email, code: input.value }) });
        save({ session: r.session, email: r.email });
        pending = {};
        await start();
      } catch (err) {
        if (err.state === 'expired') { renderEmail(err.message); return; }
        renderCode(err.message);
      }
    });
  }

  // ── Claude token ───────────────────────────────────────────────────────
  function renderToken(error = '') {
    const tok = info?.token;
    const why = tok?.status === 'invalid' ? `<p class="ag-note bad">Claude rejected the token you added${tok.hint ? ` (…${h(tok.hint)})` : ''}. Paste a new one.</p>` : '';
    frame(`
      <form class="ag-gate wide" id="ag-token" novalidate>
        <div class="ag-gate-mark">${SPARK}</div>
        <h3>Connect your Claude account</h3>
        <p class="ag-lead">The agent runs Claude Code on the server as <b>${h(info.email)}</b>, with your own token, so its usage counts against your Claude plan.</p>
        ${why}
        ${info.claude_cli ? '' : '<p class="ag-note bad">Claude Code isn\'t installed on this server, so the token can\'t be checked and the agent can\'t run. Ask the admin.</p>'}
        <ol class="ag-steps">
          <li>On your laptop, in a terminal: <code>claude setup-token</code></li>
          <li>Sign in to your Claude account when the browser opens.</li>
          <li>Copy the token it prints (starts with <code>sk-ant-oat01-</code>) and paste it here.</li>
        </ol>
        <label for="ag-tok">Claude token</label>
        <div class="ag-field"><input id="ag-tok" type="password" spellcheck="false" autocapitalize="off" autocomplete="off" placeholder="sk-ant-oat01-…"></div>
        <p class="form-error">${h(error)}</p>
        <button class="primary" type="submit"><span class="spin" aria-hidden="true"></span><span>Connect</span></button>
        <p class="hint">Stored encrypted on the server, shared with DeviceQAI, and never shown again. You can remove it from the agent's menu.</p>
        <div class="ag-links"><button type="button" class="ag-link" id="ag-out">Sign out</button></div>
      </form>`, { sub: `${h(targetLabel())} · ${h(info.email)}` });
    const form = box.querySelector('#ag-token');
    const input = box.querySelector('#ag-tok');
    input.focus();
    box.querySelector('#ag-out').addEventListener('click', signOut);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!input.value.trim()) { renderToken('Paste your token.'); return; }
      const btn = form.querySelector('button.primary');
      busyBtn(btn, true, 'Checking with Claude…');
      input.disabled = true;
      try {
        const r = await call('/token', { method: 'POST', body: JSON.stringify({ token: input.value.trim() }) });
        info.token = r.token;
        await openChat();
      } catch (err) {
        if (err.status === 401) { renderEmail('Your session ended. Sign in again.'); return; }
        renderToken(err.message);
      }
    });
  }

  // ── chat ───────────────────────────────────────────────────────────────
  const fmtSecs = (s) => s == null ? '' : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
  const fmtDay = (d) => new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

  function scopeLine(sc) {
    if (!sc) return h(targetLabel());
    const devs = sc.devices === 1 ? sc.device_ids[0] : `${sc.devices} devices`;
    return `${h(sc.title)} · <span class="mono">${h(devs)}</span> · ${h(fmtDay(sc.from))} – ${h(fmtDay(sc.to))}`
      + ` <span class="ag-chip" title="The run's dates plus one day either side">±1 day</span>`;
  }

  const SUGGEST = {
    run: ['Summarise the failing test cases in this report and what the logs show for the top one.',
      'Were there any service crashes or restarts? Show the evidence.',
      'Walk me through the last boot of each device: ignition, recording, upload.',
      'Which config keys are missing, and do the logs explain why?'],
    comparison: ['What are the biggest differences between A and B, and what in the logs explains them?',
      'Which test cases pass on one side and fail on the other?',
      'Compare crashes and restarts between the two sides.'],
  };

  function stepsHtml(m, open) {
    const steps = m.steps || [];
    if (!steps.length) return '';
    const tools = steps.filter((s) => s.s);
    const label = `${tools.length} step${tools.length === 1 ? '' : 's'}${m.seconds ? ` · ${fmtSecs(m.seconds)}` : ''}`;
    return `<details class="ag-steps-log" ${open ? 'open' : ''}><summary>${h(label)}</summary><ol>${steps.map((s) => s.note
      ? `<li class="note">${h(s.note)}</li>`
      : `<li class="${s.error ? 'err' : ''}${s.sub ? ' sub' : ''}"><span class="t">${h(fmtSecs(s.t))}</span>${h(s.s)}</li>`).join('')}</ol></details>`;
  }

  function msgHtml(m) {
    if (m.role === 'user') return `<div class="ag-msg user" data-i="${m.i}"><div class="ag-bubble">${h(m.text).replace(/\n/g, '<br>')}</div></div>`;
    const live = m.status === 'queued' || m.status === 'running';
    const last = (m.steps || []).filter((s) => s.s).slice(-1)[0];
    let body = '';
    if (m.status === 'queued') body = `<div class="ag-wait"><span class="ag-dots"><i></i><i></i><i></i></span>${h(m.detail || 'Starting…')}</div>`;
    else if (live) {
      body = (m.live ? `<div class="ag-md">${md(m.live)}</div>` : '')
        + `<div class="ag-wait"><span class="ag-dots"><i></i><i></i><i></i></span>${h(last ? last.s : 'Thinking…')}</div>`;
    } else {
      body = (m.text ? `<div class="ag-md">${md(m.text)}</div>` : '')
        + (m.status === 'failed' ? `<div class="alert">${h(m.detail || 'Something went wrong.')}</div>` : '')
        + (m.status === 'stopped' ? '<div class="alert warn">Stopped.</div>' : '');
    }
    const meta = !live && m.status === 'done' ? `<div class="ag-meta">${h(m.model || '')}${m.seconds ? ` · ${fmtSecs(m.seconds)}` : ''}${m.cost != null ? ` · $${Number(m.cost).toFixed(2)}` : ''}</div>` : '';
    return `<div class="ag-msg bot" data-i="${m.i}"><span class="ag-avatar">${SPARK}</span><div class="ag-reply">${stepsHtml(m, live)}${body}${meta}</div></div>`;
  }

  function renderChat() {
    const sc = chat.scope;
    const tools = `
      <select id="ag-model" aria-label="Model" title="Claude model">${(info.models || []).map((x) => `<option value="${h(x.id)}" ${x.id === model ? 'selected' : ''}>${h(x.label)}</option>`).join('')}</select>
      <div class="ag-menu"><button type="button" class="ag-dots-btn" id="ag-more" aria-haspopup="menu" aria-expanded="false" title="More">⋯</button>
        <div class="ag-pop" role="menu" hidden>
          <div class="ag-pop-h"><b>${h(info.email)}</b><span>Claude token ${info.token?.hint ? `…${h(info.token.hint)}` : ''} · ${h(info.token?.status || '')}</span></div>
          <button type="button" role="menuitem" data-act="new">New chat</button>
          <button type="button" role="menuitem" data-act="test">Check my Claude token</button>
          <button type="button" role="menuitem" data-act="token">Replace my Claude token</button>
          <button type="button" role="menuitem" data-act="drop">Remove my Claude token</button>
          <button type="button" role="menuitem" data-act="out">Sign out</button>
        </div></div>`;
    frame(`
      <div class="ag-thread" id="ag-thread"></div>
      <form class="ag-compose" id="ag-compose">
        <textarea id="ag-input" rows="1" placeholder="Ask about ${h(targetLabel())}'s logs or report…"></textarea>
        <button type="submit" class="ag-send" id="ag-send" title="Send (Enter)" aria-label="Send"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12l16-8-6 16-2.5-6.5z" fill="currentColor"/></svg></button>
        <button type="button" class="ag-stop" id="ag-stop" hidden title="Stop">Stop</button>
      </form>
      <p class="ag-foot">Read-only. Sees only ${sc ? `${sc.devices === 1 ? 'this device' : `these ${sc.devices} devices`}, ${h(fmtDay(sc.from))} – ${h(fmtDay(sc.to))},` : 'this run'} and the report. Check what matters.</p>`,
    { sub: scopeLine(sc), tools });

    box.querySelector('#ag-model').addEventListener('change', (e) => {
      model = e.target.value;
      try { localStorage.setItem(MODEL_KEY, model); } catch (_) { /* blocked */ }
    });
    const more = box.querySelector('#ag-more');
    const pop = box.querySelector('.ag-pop');
    more.addEventListener('click', (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; more.setAttribute('aria-expanded', String(!pop.hidden)); });
    document.addEventListener('click', (e) => { if (!pop.contains(e.target)) { pop.hidden = true; more.setAttribute('aria-expanded', 'false'); } });
    pop.addEventListener('click', async (e) => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (!act) return;
      pop.hidden = true;
      if (act === 'new') { if (!chat.busy || confirm('Claude is still answering. Start a new chat anyway?')) await openChat(true); }
      else if (act === 'token') renderToken();
      else if (act === 'drop') {
        if (!confirm('Remove your Claude token from the server? DeviceQAI uses the same one; you can add it again any time.')) return;
        try { await call('/token/delete', { method: 'POST' }); info.token = null; renderToken(); } catch (err) { alert(err.message); }
      }
      else if (act === 'out') signOut();
      else if (act === 'test') {
        try {
          const r = await call('/token/test', { method: 'POST' });
          info.token = r.token;
          alert(r.token?.status === 'ok' ? 'Your Claude token works.' : `Token: ${r.token?.status}. ${r.token?.detail || ''}`);
          if (r.token?.status === 'invalid') renderToken();
        } catch (err) { alert(err.message); }
      }
    });

    const input = box.querySelector('#ag-input');
    const grow = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 180)}px`; };
    input.addEventListener('input', grow);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); box.querySelector('#ag-compose').requestSubmit(); }
    });
    box.querySelector('#ag-compose').addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text || chat.busy) return;
      await send(text);
    });
    box.querySelector('#ag-stop').addEventListener('click', async () => {
      try { merge(await call(`/chats/${chat.id}/stop`, { method: 'POST' })); } catch (err) { alert(err.message); }
    });
    drawThread(true);
    if (chat.busy) schedulePoll();
    input.focus();
  }

  function drawThread(scroll) {
    const el = box.querySelector('#ag-thread');
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (!chat.messages.length) {
      el.innerHTML = `<div class="ag-empty"><div class="ag-gate-mark">${SPARK}</div>
        <h3>What do you want to know?</h3>
        <p class="ag-lead">I can search and read the logs of ${chat.scope?.devices === 1 ? 'this device' : 'these devices'} for the run's dates (±1 day), and read the report.</p>
        <div class="ag-suggest">${(SUGGEST[chat.kind] || SUGGEST.run).map((s) => `<button type="button" class="ag-sugg">${h(s)}</button>`).join('')}</div></div>`;
      el.querySelectorAll('.ag-sugg').forEach((b) => b.addEventListener('click', () => send(b.textContent)));
    } else {
      el.innerHTML = chat.messages.map(msgHtml).join('');
    }
    if (scroll || atBottom) el.scrollTop = el.scrollHeight;
    const stopBtn = box.querySelector('#ag-stop');
    const sendBtn = box.querySelector('#ag-send');
    if (stopBtn) { stopBtn.hidden = !chat.busy; sendBtn.hidden = chat.busy; }
  }

  function merge(update) {
    if (!chat || update.id !== chat.id) return;
    const byI = new Map(chat.messages.map((m) => [m.i, m]));
    update.messages.forEach((m) => byI.set(m.i, m));
    chat.messages = [...byI.values()].sort((a, b) => a.i - b.i);
    chat.busy = update.busy;
    chat.scope = update.scope || chat.scope;
    drawThread(false);
    if (chat.busy) schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(poll);
    poll = setTimeout(async () => {
      if (!chat || !box.isConnected || box.hidden) return;
      const from = Math.max(0, chat.messages.length - 2);
      try { merge(await call(`/chats/${chat.id}?after=${from}`)); }
      catch (err) {
        if (err.status === 401) { renderEmail('Your session ended. Sign in again.'); return; }
        schedulePoll();
      }
    }, 900);
  }

  async function send(text) {
    const input = box.querySelector('#ag-input');
    try {
      const r = await call(`/chats/${chat.id}/messages`, { method: 'POST', body: JSON.stringify({ text, model }) });
      if (input) { input.value = ''; input.style.height = 'auto'; }
      merge(r);
      drawThread(true);
    } catch (err) {
      if (err.state === 'token') { info.token = { ...(info.token || {}), status: 'invalid' }; renderToken(err.message); return; }
      if (err.status === 401) { renderEmail('Your session ended. Sign in again.'); return; }
      alert(err.message);
    }
  }

  async function openChat(fresh = false) {
    frame('<div class="ag-loading"><div class="skeleton" style="width:60%"></div><div class="skeleton"></div><div class="skeleton" style="width:80%"></div></div>');
    try {
      chat = await call('/chats', { method: 'POST', body: JSON.stringify({ kind: target.kind, id: target.id, new: fresh }) });
      renderChat();
    } catch (err) {
      if (err.status === 401) { renderEmail(); return; }
      frame(`<div class="ag-gate"><div class="alert">${h(err.message)}</div></div>`);
    }
  }

  async function signOut() {
    try { await call('/auth/logout', { method: 'POST' }); } catch (_) { /* ended anyway */ }
    save(null);
    chat = null;
    info = { ...info, signed_in: false };
    renderEmail();
  }

  async function start() {
    clearTimeout(poll);
    frame('<div class="ag-loading"><div class="skeleton" style="width:50%"></div><div class="skeleton"></div></div>');
    try {
      info = await call('/session');
    } catch (err) {
      frame(`<div class="ag-gate"><div class="alert">${h(err.message)}</div>
        <p class="hint">Agent mode needs the DeviceQAI sign-in (device-automation/deviceqa_auth) next to this server.</p></div>`);
      return;
    }
    if (!info.signed_in) { renderEmail(); return; }
    if (!info.token || info.token.status === 'invalid') { renderToken(); return; }
    await openChat();
  }

  return {
    // app.js: the run / comparison on the left is in agent mode
    open(kind, id) {
      const same = target && target.kind === kind && target.id === id && !box.hidden;
      target = { kind, id };
      document.body.classList.add('agent-mode');
      box.hidden = false;
      if (!same) { chat = null; start(); }
    },
    close() {
      if (box.hidden) return;
      clearTimeout(poll);
      box.hidden = true;
      box.innerHTML = '';
      document.body.classList.remove('agent-mode');
      target = null;
      chat = null;
    },
    active: () => !box.hidden,
  };
})();
