// Account — who is signed in, for the whole page (DeviceQAI sign-in, /log-analyzer/agent).
// The profile chip next to the environment switch, its menu, the sign-in and profile
// dialogs, and the sign-in / Claude-token forms agent mode reuses (agent.js).
// The session is a bearer token kept in localStorage (the API is on another origin).
'use strict';

window.LAAuth = (() => {
  const SESSION_KEY = 'la-agent-session';
  const h = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const base = () => API + '/agent'; // API: app.js
  const listeners = new Set();
  let info = null;     // GET /session
  let loading = null;

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
      if (res.status === 401 && s?.session) { save(null); if (info?.signed_in) { info = { ...info, signed_in: false, token: null }; emit(); } }
      throw new ApiError(msg, res.status, d?.state);
    }
    return body;
  }

  function emit() { listeners.forEach((fn) => { try { fn(info); } catch (_) { /* a listener's problem */ } }); renderChip(); }

  async function load() {
    if (!loading) {
      loading = call('/session').then((r) => { info = r; return r; })
        .catch((err) => { info = { unavailable: err.message, signed_in: false }; return info; })
        .finally(() => { loading = null; emit(); });
    }
    return loading;
  }

  async function signOut() {
    try { await call('/auth/logout', { method: 'POST' }); } catch (_) { /* ended anyway */ }
    save(null);
    info = { ...(info || {}), signed_in: false, token: null, email: '', name: '' };
    emit();
  }

  const spark = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9z" fill="currentColor"/><path d="M19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2-2.2-.8 2.2-.8z" fill="currentColor" opacity=".7"/></svg>';
  const busy = (btn, on, label) => {
    btn.disabled = on;
    btn.classList.toggle('loading', on);
    if (label) btn.querySelector('span:last-child').textContent = label;
  };

  // ── sign-in form: email → code; renders into `el`, calls onDone() once signed in ──
  function signInForm(el, { lead = '', onDone }) {
    let pending = {};
    const email = (error = '') => {
      el.innerHTML = `
        <form class="ag-gate" novalidate>
          <div class="ag-gate-mark">${spark}</div>
          <h3>Sign in</h3>
          ${lead ? `<p class="ag-lead">${lead}</p>` : ''}
          <label for="la-mail">Work email</label>
          <div class="ag-field"><input id="la-mail" type="email" autocomplete="username email" placeholder="you@${h(info?.domain || 'netradyne.com')}" value="${h(pending.email || saved()?.email || '')}"></div>
          <p class="form-error">${h(error)}</p>
          <button class="primary" type="submit"><span class="spin" aria-hidden="true"></span><span>Email me a code</span></button>
          <p class="hint">Any @${h(info?.domain || 'netradyne.com')} address. We'll email you a 6-digit code.</p>
        </form>`;
      const form = el.querySelector('form');
      const input = el.querySelector('#la-mail');
      input.focus();
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const value = input.value.trim();
        if (!value) { email('Enter your email.'); return; }
        busy(form.querySelector('button'), true);
        try {
          const r = await call('/auth/request', { method: 'POST', body: JSON.stringify({ email: value }) });
          pending = { email: r.email, via: r.via, resendAt: Date.now() + r.resend_in * 1000 };
          code();
        } catch (err) {
          pending.email = value;
          if (err.state === 'not_allowed') denied(value); else email(err.message);
        }
      });
    };
    const denied = (who) => {
      el.innerHTML = `<div class="ag-gate"><div class="ag-gate-mark warn">!</div><h3>No access yet</h3>
        <p class="ag-lead"><b>${h(who)}</b> can't sign in here. Use your @${h(info?.domain || 'netradyne.com')} address.</p>
        <button class="primary" type="button"><span>Try another email</span></button></div>`;
      el.querySelector('button').addEventListener('click', () => email());
    };
    const code = (error = '') => {
      el.innerHTML = `
        <form class="ag-gate" novalidate>
          <div class="ag-gate-mark">${spark}</div>
          <h3>Check your email</h3>
          <p class="ag-lead">Enter the 6-digit code sent to <b>${h(pending.email)}</b>.</p>
          ${pending.via === 'console' ? '<p class="ag-note">Mail is off on this server: the code is printed in the backend\'s console (<code>docker logs atlas-agent-api</code>).</p>' : ''}
          <div class="ag-field"><input class="ag-otp" inputmode="numeric" autocomplete="one-time-code" placeholder="••••••" aria-label="6-digit code"></div>
          <p class="form-error">${h(error)}</p>
          <button class="primary" type="submit"><span class="spin" aria-hidden="true"></span><span>Sign in</span></button>
          <div class="ag-links"><button type="button" class="ag-link" data-a="change">Use another email</button>
            <button type="button" class="ag-link" data-a="resend" disabled>Resend code</button></div>
        </form>`;
      const form = el.querySelector('form');
      const input = el.querySelector('.ag-otp');
      input.focus();
      // No maxlength: the mail shows the code spaced ("1 2 3 4 5 6"), and a length limit would
      // cut a paste to its first 6 characters before the spaces are dropped. Keep the digits.
      const take = (raw) => {
        input.value = String(raw || '').replace(/\D/g, '').slice(0, 6);
        if (input.value.length === 6) form.requestSubmit();
      };
      input.addEventListener('paste', (e) => {
        const text = (e.clipboardData || window.clipboardData)?.getData('text');
        if (text == null) return;
        e.preventDefault();
        take(text);
      });
      input.addEventListener('input', () => take(input.value));
      const resend = el.querySelector('[data-a="resend"]');
      const tick = () => {
        if (!el.contains(resend)) return;
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
          pending.via = r.via;
          code();
        } catch (err) { code(err.message); }
      });
      el.querySelector('[data-a="change"]').addEventListener('click', () => email());
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (input.value.length !== 6) { code('Enter the 6 digits.'); return; }
        busy(form.querySelector('button.primary'), true);
        try {
          const r = await call('/auth/verify', { method: 'POST', body: JSON.stringify({ email: pending.email, code: input.value }) });
          save({ session: r.session, email: r.email });
          await load();
          onDone?.();
        } catch (err) {
          if (err.state === 'expired') email(err.message); else code(err.message);
        }
      });
    };
    email();
  }

  // ── Claude token form; onDone() once a working token is kept ──
  function tokenForm(el, { onDone, onSkip, title = 'Connect your Claude account' }) {
    const draw = (error = '') => {
      const tok = info?.token;
      el.innerHTML = `
        <form class="ag-gate wide" novalidate>
          <div class="ag-gate-mark">${spark}</div>
          <h3>${h(title)}</h3>
          <p class="ag-lead">The agent runs Claude Code on the server as <b>${h(info?.email)}</b>, with your own token, so its usage counts against your Claude plan.</p>
          ${tok?.status === 'invalid' ? `<p class="ag-note bad">Claude rejected the token you added${tok.hint ? ` (…${h(tok.hint)})` : ''}. Paste a new one.</p>` : ''}
          ${info?.claude_cli === false ? '<p class="ag-note bad">Claude Code isn\'t installed on this server, so the token can\'t be checked and the agent can\'t run. Ask the admin.</p>' : ''}
          <ol class="ag-steps">
            <li>On your laptop, in a terminal: <code>claude setup-token</code></li>
            <li>Sign in to your Claude account when the browser opens.</li>
            <li>Copy the token it prints (starts with <code>sk-ant-oat01-</code>) and paste it here.</li>
          </ol>
          <label for="la-tok">Claude token</label>
          <div class="ag-field"><input id="la-tok" type="password" spellcheck="false" autocapitalize="off" autocomplete="off" placeholder="sk-ant-oat01-…"></div>
          <p class="form-error">${h(error)}</p>
          <button class="primary" type="submit"><span class="spin" aria-hidden="true"></span><span>Connect</span></button>
          <p class="hint">Stored encrypted on the server, shared with DeviceQAI, never shown again. Change it any time from your profile.</p>
          ${onSkip ? '<div class="ag-links"><button type="button" class="ag-link" data-a="skip">Later</button></div>' : ''}
        </form>`;
      const form = el.querySelector('form');
      const input = el.querySelector('#la-tok');
      input.focus();
      el.querySelector('[data-a="skip"]')?.addEventListener('click', () => onSkip());
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!input.value.trim()) { draw('Paste your token.'); return; }
        busy(form.querySelector('button.primary'), true, 'Checking with Claude…');
        input.disabled = true;
        try {
          const r = await call('/token', { method: 'POST', body: JSON.stringify({ token: input.value.trim() }) });
          info = { ...info, token: r.token };
          emit();
          onDone?.();
        } catch (err) { draw(err.message); }
      });
    };
    draw();
  }

  // ── dialog ─────────────────────────────────────────────────────────────
  let dialog = null;
  function openDialog(title) {
    closeDialog();
    dialog = document.createElement('div');
    dialog.className = 'la-modal';
    dialog.innerHTML = `<div class="la-modal-card" role="dialog" aria-modal="true" aria-label="${h(title)}">
      <button type="button" class="la-modal-x" aria-label="Close">×</button><div class="la-modal-body"></div></div>`;
    document.body.appendChild(dialog);
    dialog.addEventListener('mousedown', (e) => { if (e.target === dialog) closeDialog(); });
    dialog.querySelector('.la-modal-x').addEventListener('click', closeDialog);
    return dialog.querySelector('.la-modal-body');
  }
  function closeDialog() { if (dialog) { dialog.remove(); dialog = null; } }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && dialog) closeDialog(); });

  function openSignIn() {
    const body = openDialog('Sign in');
    signInForm(body, {
      lead: 'Sign in with your Netradyne email to use agent mode with your own Claude account.',
      onDone: () => {
        if (!info?.token || info.token.status === 'invalid') tokenForm(body, { onDone: closeDialog, onSkip: closeDialog });
        else closeDialog();
      },
    });
  }

  const tokenLine = (tok) => {
    if (!tok) return '<span class="la-tok none">Not connected</span>';
    const label = { ok: 'Connected', expiring: 'Expires soon', unchecked: 'Not checked', invalid: 'Rejected' }[tok.status] || tok.status;
    return `<span class="la-tok ${h(tok.status)}">${h(label)}${tok.hint ? ` · …${h(tok.hint)}` : ''}</span>`;
  };

  function openProfile() {
    if (!info?.signed_in) { openSignIn(); return; }
    const body = openDialog('Your profile');
    const draw = (error = '', done = '') => {
      const tok = info.token;
      body.innerHTML = `
        <form class="la-profile" novalidate>
          <div class="la-profile-top"><span class="la-av big">${h(initial())}</span>
            <div><h3>${h(info.name)}</h3><span class="muted">${h(info.email)}</span></div></div>
          <label for="la-name">Name</label>
          <div class="ag-field"><input id="la-name" maxlength="60" placeholder="${h(info.email.split('@')[0])}" value="${h(info.display_name || '')}"></div>
          <label for="la-tok2">Claude token <span class="muted">${tokenLine(tok)}</span></label>
          <div class="ag-field"><input id="la-tok2" type="password" spellcheck="false" autocapitalize="off" autocomplete="off"
            placeholder="${tok ? 'Leave empty to keep the current one' : 'sk-ant-oat01-…'}"></div>
          <p class="hint">${tok ? '' : 'Get one with <code>claude setup-token</code> on your laptop. '}A new token is checked with Claude before it's kept.</p>
          <p class="form-error">${h(error)}</p>
          ${done ? `<p class="la-done">${h(done)}</p>` : ''}
          <div class="la-profile-actions">
            ${tok ? '<button type="button" class="ag-link danger" data-a="drop">Remove token</button>' : '<span></span>'}
            <button class="primary" type="submit"><span class="spin" aria-hidden="true"></span><span>Save</span></button>
          </div>
        </form>`;
      const form = body.querySelector('form');
      body.querySelector('#la-name').focus();
      body.querySelector('[data-a="drop"]')?.addEventListener('click', async () => {
        if (!confirm('Remove your Claude token from the server? DeviceQAI uses the same one; agent mode stops working until you add one again.')) return;
        try { await call('/token/delete', { method: 'POST' }); info = { ...info, token: null }; emit(); draw('', 'Token removed.'); }
        catch (err) { draw(err.message); }
      });
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = body.querySelector('#la-name').value.trim();
        const token = body.querySelector('#la-tok2').value.trim();
        const btn = form.querySelector('button.primary');
        busy(btn, true, token ? 'Checking with Claude…' : 'Saving…');
        try {
          if (name !== (info.display_name || '')) {
            const r = await call('/profile', { method: 'POST', body: JSON.stringify({ name }) });
            info = { ...info, name: r.name, display_name: r.display_name };
          }
          if (token) {
            const r = await call('/token', { method: 'POST', body: JSON.stringify({ token }) });
            info = { ...info, token: r.token };
          }
          emit();
          closeDialog();
        } catch (err) {
          if (err.status === 401) { closeDialog(); openSignIn(); return; }
          draw(err.message);
        }
      });
    };
    draw();
  }

  // ── chip + menu, next to the environment switch ──────────────────────
  const initial = () => (info?.name || info?.email || '?').trim()[0]?.toUpperCase() || '?';
  const PERSON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20c1.4-3.6 4.3-5.4 7.5-5.4s6.1 1.8 7.5 5.4"/></svg>';
  let chip = null;

  function renderChip() {
    if (!chip) return;
    const inside = !!info?.signed_in;
    const open = chip.classList.contains('open');
    chip.innerHTML = `
      <button type="button" class="la-av${inside ? '' : ' out'}" aria-haspopup="menu" aria-expanded="${open}"
        title="${inside ? h(info.email) : 'Not signed in'}">${inside ? h(initial()) : PERSON}</button>
      <div class="la-menu" role="menu">
        ${inside ? `
          <div class="la-menu-h"><b>${h(info.name)}</b><span>${h(info.email)}</span>${tokenLine(info.token)}</div>
          <button type="button" role="menuitem" data-a="profile">${PERSON}Update profile</button>
          <div class="la-sep"></div>
          <button type="button" role="menuitem" data-a="out"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h4.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H14"/><path d="M10 16.5 5.5 12 10 7.5M5.5 12H15"/></svg>Sign out</button>`
        : `
          <div class="la-menu-h"><b>Not signed in</b><span>${info?.unavailable ? h(info.unavailable) : 'Sign in to use agent mode.'}</span></div>
          <div class="la-menu-pad"><button type="button" class="primary" data-a="in" ${info?.unavailable ? 'disabled' : ''}><span>Sign in</span></button></div>`}
      </div>`;
    const btn = chip.querySelector('.la-av');
    btn.addEventListener('click', (e) => { e.stopPropagation(); setOpen(!chip.classList.contains('open')); });
    chip.querySelector('.la-menu').addEventListener('click', (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (!a) return;
      setOpen(false);
      if (a === 'profile') openProfile();
      else if (a === 'in') openSignIn();
      else if (a === 'out') signOut();
    });
  }
  function setOpen(on) {
    chip.classList.toggle('open', on);
    chip.querySelector('.la-av')?.setAttribute('aria-expanded', String(on));
  }

  function mount() {
    const host = document.querySelector('.top-right');
    if (!host || chip) return;
    chip = document.createElement('div');
    chip.className = 'la-acct';
    chip.id = 'acct';
    host.appendChild(chip);
    document.addEventListener('click', (e) => { if (chip && !chip.contains(e.target)) setOpen(false); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && chip?.classList.contains('open')) setOpen(false); });
    // Signed in or out in another tab
    addEventListener('storage', (e) => { if (e.key === SESSION_KEY) load(); });
    renderChip();
    load();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();

  return {
    call, load, signOut, signInForm, tokenForm, openProfile, openSignIn, ApiError,
    info: () => info,
    setInfo: (next) => { info = next; emit(); },
    onChange: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
    spark,
  };
})();
