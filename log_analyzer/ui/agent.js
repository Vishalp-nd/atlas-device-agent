// Agent mode — a chat with Claude about the run or comparison open on the left.
// Sign-in and the Claude token are the page's account (account.js, LAAuth); every answer is
// a Claude Code run on the server with the person's own token, limited to this run's
// devices, its dates ±1 day, and its reports. Talks to /log-analyzer/agent
// (log_analyzer/agent/router.py); app.js opens and closes it. The panel's width is the
// person's: drag its left edge (remembered per browser), double-click it to reset.
'use strict';

window.LAAgent = (() => {
  const MODEL_KEY = 'la-agent-model';
  const WIDTH_KEY = 'la-agent-width';
  const MIN_PANEL = 360;   // px, the chat
  const MIN_VIEW = 340;    // px, the run on the left
  const box = document.getElementById('agent');
  const layout = document.querySelector('.layout');
  const h = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const call = (...a) => LAAuth.call(...a);
  const SPARK = LAAuth.spark;

  let target = null;        // {kind: 'run'|'comparison', id}
  let chat = null;          // the open chat
  let poll = null;
  let shownFor = null;      // the sign-in state the panel was drawn for
  let model = 'sonnet';
  try { model = localStorage.getItem(MODEL_KEY) || 'sonnet'; } catch (_) { /* storage blocked */ }
  const info = () => LAAuth.info() || {};

  // ── width: drag the panel's left edge ───────────────────────────────────
  const wide = () => matchMedia('(min-width: 901px)').matches;
  let width = null;
  try { width = Number(localStorage.getItem(WIDTH_KEY)) || null; } catch (_) { /* blocked */ }

  function clampWidth(w) {
    const gap = 20;
    const room = layout.clientWidth - parseFloat(getComputedStyle(layout).paddingLeft) - parseFloat(getComputedStyle(layout).paddingRight) - gap;
    return Math.round(Math.min(Math.max(w, MIN_PANEL), Math.max(MIN_PANEL, room - MIN_VIEW)));
  }
  function applyWidth() {
    if (box.hidden || !wide() || !width) { layout.style.gridTemplateColumns = ''; return; }
    const w = clampWidth(width);
    layout.style.gridTemplateColumns = `minmax(0, 1fr) ${w}px`;
    const grip = box.querySelector('.ag-grip');
    if (grip) grip.setAttribute('aria-valuenow', String(w));
  }
  function setWidth(w, keep = true) {
    width = w;
    if (keep) { try { w ? localStorage.setItem(WIDTH_KEY, String(Math.round(w))) : localStorage.removeItem(WIDTH_KEY); } catch (_) { /* blocked */ } }
    applyWidth();
  }
  addEventListener('resize', applyWidth);

  function grip() {
    const el = document.createElement('div');
    el.className = 'ag-grip';
    el.setAttribute('role', 'separator');
    el.setAttribute('aria-orientation', 'vertical');
    el.setAttribute('aria-label', 'Resize the agent panel');
    el.setAttribute('aria-valuemin', String(MIN_PANEL));
    el.tabIndex = 0;
    el.title = 'Drag to resize · double-click to reset';
    el.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || !wide()) return;
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      document.body.classList.add('ag-resizing');
      const right = box.getBoundingClientRect().right;
      const move = (ev) => setWidth(clampWidth(right - ev.clientX), false);
      const up = () => {
        el.removeEventListener('pointermove', move);
        el.removeEventListener('pointerup', up);
        el.removeEventListener('pointercancel', up);
        document.body.classList.remove('ag-resizing');
        setWidth(clampWidth(box.getBoundingClientRect().width));
      };
      el.addEventListener('pointermove', move);
      el.addEventListener('pointerup', up);
      el.addEventListener('pointercancel', up);
    });
    el.addEventListener('dblclick', () => setWidth(null));
    el.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 80 : 24;
      const now = box.getBoundingClientRect().width;
      if (e.key === 'ArrowLeft') { e.preventDefault(); setWidth(clampWidth(now + step)); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); setWidth(clampWidth(now - step)); }
      else if (e.key === 'Home' || e.key === 'Escape') { e.preventDefault(); setWidth(null); }
    });
    return el;
  }

  // ── markdown (what Claude writes: headings, lists, code, tables, emphasis, links) ──
  function inline(s) {
    return h(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?!\w)/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function md(text) {
    const lines = String(text || '').replace(/\r/g, '').split('\n');
    const out = [];
    let i = 0;
    const isTable = (l, n) => /^\s*\|.*\|\s*$/.test(l) && /^\s*\|?\s*:?-{2,}/.test(n || '');
    while (i < lines.length) {
      const l = lines[i];
      if (/^\s*```/.test(l)) {
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
      if (hd) { const n = Math.min(hd[1].length + 2, 6); out.push(`<h${n} class="ag-h">${inline(hd[2])}</h${n}>`); i++; continue; }
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
  const targetLabel = () => target ? (target.kind === 'run' ? `Run #${target.id}` : `Comparison #${target.id}`) : '';
  const pageOf = () => target ? (target.kind === 'run' ? `#/runs/${target.id}` : `#/compare/${target.id}`) : '#/';

  function frame(body, { sub = '', tools = '' } = {}) {
    box.innerHTML = `
      <header class="ag-head">
        <div class="ag-title"><span class="ag-mark">${SPARK}</span><div><h2>Log Agent</h2>
          <div class="ag-sub">${sub || h(targetLabel())}</div></div></div>
        <div class="ag-tools">${tools}<a class="ag-x" href="${pageOf()}" title="Leave agent mode" aria-label="Leave agent mode">×</a></div>
      </header>
      <div class="ag-body">${body}</div>`;
    box.prepend(grip());
    applyWidth();
    return box.querySelector('.ag-body');
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
    const me = info();
    const tools = `
      <select id="ag-model" aria-label="Model" title="Claude model">${(me.models || []).map((x) => `<option value="${h(x.id)}" ${x.id === model ? 'selected' : ''}>${h(x.label)}</option>`).join('')}</select>
      <div class="ag-menu"><button type="button" class="ag-dots-btn" id="ag-more" aria-haspopup="menu" aria-expanded="false" title="More">⋯</button>
        <div class="ag-pop" role="menu" hidden>
          <button type="button" role="menuitem" data-act="new">New chat</button>
          <button type="button" role="menuitem" data-act="test">Check my Claude token</button>
          <button type="button" role="menuitem" data-act="profile">Update profile</button>
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
    const shut = () => { pop.hidden = true; more.setAttribute('aria-expanded', 'false'); };
    more.addEventListener('click', (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; more.setAttribute('aria-expanded', String(!pop.hidden)); });
    box.onclick = (e) => { if (!pop.contains(e.target) && e.target !== more) shut(); };
    pop.addEventListener('click', async (e) => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (!act) return;
      shut();
      if (act === 'new') { if (!chat.busy || confirm('Claude is still answering. Start a new chat anyway?')) await openChat(true); }
      else if (act === 'profile') LAAuth.openProfile();
      else if (act === 'test') {
        try {
          const r = await call('/token/test', { method: 'POST' });
          LAAuth.setInfo({ ...info(), token: r.token });
          if (r.token?.status === 'ok') alert('Your Claude token works.');
          else alert(`Token: ${r.token?.status}. ${r.token?.detail || ''}`);
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
      if (!chat || box.hidden) return;
      const from = Math.max(0, chat.messages.length - 2);
      try { merge(await call(`/chats/${chat.id}?after=${from}`)); }
      catch (err) { if (err.status !== 401) schedulePoll(); }   // 401: LAAuth's change redraws
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
      if (err.state === 'token') { LAAuth.setInfo({ ...info(), token: { ...(info().token || {}), status: 'invalid' } }); return; }
      if (err.status !== 401) alert(err.message);
    }
  }

  async function openChat(fresh = false) {
    frame('<div class="ag-loading"><div class="skeleton" style="width:60%"></div><div class="skeleton"></div><div class="skeleton" style="width:80%"></div></div>');
    try {
      chat = await call('/chats', { method: 'POST', body: JSON.stringify({ kind: target.kind, id: target.id, new: fresh }) });
      renderChat();
    } catch (err) {
      if (err.status === 401) return;   // LAAuth's change redraws
      frame(`<div class="ag-gate"><div class="alert">${h(err.message)}</div></div>`);
    }
  }

  // What the panel shows follows the account: signed out → sign in, no usable token →
  // connect Claude, else the chat. Redrawn only when that state changes.
  const stateOf = (me) => !me ? 'loading' : me.unavailable ? 'unavailable' : !me.signed_in ? 'out'
    : (!me.token || me.token.status === 'invalid') ? 'token' : `in:${me.email}`;

  function show() {
    if (box.hidden || !target) return;
    const me = LAAuth.info();
    const state = stateOf(me);
    if (state === shownFor) return;
    shownFor = state;
    clearTimeout(poll);
    if (state === 'loading') { frame('<div class="ag-loading"><div class="skeleton" style="width:50%"></div><div class="skeleton"></div></div>'); return; }
    if (state === 'unavailable') {
      frame(`<div class="ag-gate"><div class="alert">${h(me.unavailable)}</div>
        <p class="hint">Agent mode needs the DeviceQAI sign-in (device-automation/deviceqa_auth) next to this server.</p></div>`);
      return;
    }
    chat = null;
    if (state === 'out') {
      const body = frame('');
      LAAuth.signInForm(body, { lead: `Ask questions about ${h(targetLabel())}: its devices' logs, its dates and its report. Claude answers on the server with <b>your own</b> Claude account.` });
      return;
    }
    if (state === 'token') { LAAuth.tokenForm(frame('', { sub: `${h(targetLabel())} · ${h(me.email)}` }), {}); return; }
    openChat();
  }
  LAAuth.onChange(show);

  return {
    // app.js: the run / comparison on the left is in agent mode
    open(kind, id) {
      const same = target && target.kind === kind && target.id === id && !box.hidden;
      target = { kind, id };
      document.body.classList.add('agent-mode');
      box.hidden = false;
      if (same) return;
      chat = null;
      shownFor = null;
      if (LAAuth.info()) show(); else { show(); LAAuth.load(); }
    },
    close() {
      if (box.hidden) return;
      clearTimeout(poll);
      box.hidden = true;
      box.innerHTML = '';
      layout.style.gridTemplateColumns = '';
      document.body.classList.remove('agent-mode');
      target = null;
      chat = null;
      shownFor = null;
    },
    active: () => !box.hidden,
  };
})();
