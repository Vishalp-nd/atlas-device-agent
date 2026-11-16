"""Agent mode API, under /log-analyzer/agent.

Sign-in (DeviceQAI's: an emailed code, for allowed @netradyne.com addresses), the person's
Claude token (from `claude setup-token`, kept encrypted and checked live), and the chats: one
per person per run / comparison, each turn a Claude Code run on this server with that
person's token (log_analyzer/agent/turn.py). Sessions are bearer tokens: the UI is served
from another origin.
"""

from __future__ import annotations

import logging
import os
import secrets
import signal
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, Body, HTTPException, Request

from ..config import REPO_ROOT
from . import auth, scope, store

log = logging.getLogger("log_analyzer.agent")
router = APIRouter(prefix="/log-analyzer/agent", tags=["log-analyzer-agent"])
MODELS = [{"id": "sonnet", "label": "Sonnet"}, {"id": "opus", "label": "Opus"},
          {"id": "haiku", "label": "Haiku"}]
MAX_MESSAGE = 20_000
_checking: set[str] = set()
_guard = threading.Lock()


def _err(exc) -> HTTPException:
    return HTTPException(exc.status, {"error": exc.message, "state": exc.state, **exc.extra})


def _token_state(email: str) -> dict | None:
    info = auth.store().token_info(email)
    if not info:
        return None
    return {"status": info["status"], "hint": info.get("hint"), "added": info.get("added"),
            "expires": info.get("expires"), "detail": info.get("detail") or ""}


# ── sign-in ──────────────────────────────────────────────────────────────────

@router.get("/session")
def session(request: Request):
    """Who is signed in (from the bearer token) and whether their Claude token is usable."""
    core, vault, mailer = auth.dqa()
    base = {"domain": core.domain(), "models": MODELS, "claude_cli": bool(vault.claude_bin()),
            "mail": mailer.sending()}
    who = auth.store().session(auth.bearer(request), auth.client_ip(request))
    if who is None or who.kind != "user":
        return {**base, "signed_in": False}
    name = store.profile(who.email).get("name") or ""
    return {**base, "signed_in": True, "email": who.email, "name": name or who.name,
            "display_name": name, "token": _token_state(who.email)}


@router.post("/profile")
def update_profile(request: Request, body: dict = Body(default={})):
    """The person's display name (the Claude token has its own endpoints below)."""
    who = auth.person(request)
    name = " ".join(str(body.get("name") or "").split())
    if len(name) > store.NAME_MAX:
        raise HTTPException(422, f"Keep the name under {store.NAME_MAX} characters.")
    store.put_profile(who.email, name=name)
    return {"name": name or who.name, "display_name": name}


@router.post("/auth/request")
def request_code(request: Request, body: dict = Body(default={})):
    core, _, mailer = auth.dqa()
    email = str(body.get("email") or "").strip()
    if email and "@" not in email:
        email = f"{email}@{core.domain()}"
    try:
        code, resend = auth.store().request_code(email, auth.client_ip(request))
    except core.AuthError as exc:
        raise _err(exc) from None
    try:
        via = mailer.send_code(email, code)
    except mailer.MailError as exc:
        auth.store().forget_send(email)
        log.error("sign-in mail to %s failed: %s", email, exc)
        raise HTTPException(502, {"error": "The sign-in mail couldn't be sent. Try again in a "
                                           "minute, or contact the admin.", "state": "mail"}) from None
    return {"state": "sent", "email": core.norm_email(email), "via": via, "resend_in": resend,
            "expires_in": core.CODE_MINUTES * 60}


@router.post("/auth/verify")
def verify(request: Request, body: dict = Body(default={})):
    core, _, _ = auth.dqa()
    st = auth.store()
    try:
        email = st.verify_code(str(body.get("email") or ""), str(body.get("code") or ""))
    except core.AuthError as exc:
        raise _err(exc) from None
    token = st.open_session(email, "user", auth.client_ip(request),
                            "Log Analyzer · " + request.headers.get("user-agent", "")[:160])
    auth.note(request, email, "sign in", detail="agent mode")
    return {"session": token, "email": email, "token": _token_state(email)}


@router.post("/auth/logout")
def logout(request: Request):
    auth.store().close_session(auth.bearer(request))
    return {"ok": True}


# ── Claude token ─────────────────────────────────────────────────────────────

def _once(email: str, fn):
    with _guard:
        if email in _checking:
            raise HTTPException(409, {"error": "Already checking. One moment.", "state": "busy"})
        _checking.add(email)
    try:
        return fn()
    finally:
        with _guard:
            _checking.discard(email)


@router.post("/token")
def put_token(request: Request, body: dict = Body(default={})):
    """Keep the person's Claude token: checked against Claude first, then stored encrypted."""
    who = auth.person(request)
    _, vault, _ = auth.dqa()

    def run():
        try:
            token = vault.clean(str(body.get("token") or ""))
        except vault.TokenError as exc:
            raise HTTPException(400, {"error": exc.message, "state": exc.state}) from None
        status, detail = vault.check(token)
        if status == "invalid":
            auth.note(request, who.email, "add Claude token", result="refused", detail=detail)
            raise HTTPException(400, {"error": detail, "state": "invalid"})
        st = auth.store()
        replacing = st.token_info(who.email) is not None
        try:
            nonce, sealed = vault.seal(token, who.email)
        except vault.TokenError as exc:
            raise HTTPException(503, {"error": exc.message, "state": exc.state}) from None
        st.token_put(who.email, nonce, sealed, token[-4:], status, detail)
        auth.note(request, who.email, "replace Claude token" if replacing else "add Claude token",
                  result="ok" if status == "ok" else status, detail=detail)
        return {"token": _token_state(who.email)}

    return _once(who.email, run)


@router.post("/token/test")
def test_token(request: Request):
    who = auth.person(request)
    _, vault, _ = auth.dqa()

    def run():
        st = auth.store()
        sealed = st.token_sealed(who.email)
        if sealed is None:
            raise HTTPException(404, {"error": "No token yet.", "state": "none"})
        try:
            token = vault.open_(*sealed, who.email)
        except vault.TokenError as exc:
            st.token_mark(who.email, "invalid", exc.message)
            return {"token": _token_state(who.email)}
        status, detail = vault.check(token)
        st.token_mark(who.email, status, detail)
        return {"token": _token_state(who.email)}

    return _once(who.email, run)


@router.post("/token/delete")
def delete_token(request: Request):
    who = auth.person(request)
    if auth.store().token_drop(who.email):
        auth.note(request, who.email, "remove Claude token")
    return {"token": None}


# ── chats ────────────────────────────────────────────────────────────────────

def _target(body: dict) -> tuple[str, int]:
    kind = str(body.get("kind") or "")
    if kind not in ("run", "comparison"):
        raise HTTPException(422, "kind must be run or comparison")
    try:
        return kind, int(body.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(422, "id must be a number") from None


def _settle(chat: store.Chat) -> dict:
    """The thread, with a turn whose process died marked failed."""
    t = chat.thread()
    busy = t.get("busy")
    if busy and busy.get("pid") and not store.alive(busy["pid"]):
        last = t["messages"][-1] if t["messages"] else None
        if last and last.get("role") == "assistant" and last.get("status") in ("queued", "running"):
            last.update(status="failed", detail="The turn stopped unexpectedly.", live="",
                        ended=time.time())
            chat.put_last(last)
        chat.set_busy(None)
        t = chat.thread()
    return t


def _public(chat: store.Chat, after: int = 0) -> dict:
    meta = chat.meta()
    t = _settle(chat)
    msgs = t["messages"]
    return {"id": meta["id"], "kind": meta["kind"], "target_id": meta["target_id"],
            "created": meta.get("created"), "scope": scope.summary(chat.scope()),
            "busy": bool(t.get("busy")), "count": len(msgs),
            "messages": [{**m, "i": i} for i, m in enumerate(msgs) if i >= max(after, 0)]}


def _owned(request: Request, chat_id: str) -> tuple[object, store.Chat]:
    who = auth.person(request)
    chat = store.get(chat_id)
    if chat is None or chat.meta().get("email") != who.email:
        raise HTTPException(404, "No such chat.")
    return who, chat


@router.post("/chats")
def open_chat(request: Request, body: dict = Body(default={})):
    """This person's chat about a run / comparison: the latest one, or a new one (new=true)."""
    who = auth.person(request)
    kind, target_id = _target(body)
    if not body.get("new"):
        for chat in store.chats_of(who.email, kind, target_id):
            return _public(chat)
    try:
        sc = scope.build(kind, target_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    chat_id = f"{kind[0]}{target_id}-{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    chat = store.Chat.create(chat_id, {"email": who.email, "kind": kind, "target_id": target_id,
                                       "created": time.time(), "claude_session": None}, sc)
    auth.note(request, who.email, "open log agent chat", target=sc["title"])
    return _public(chat)


@router.get("/chats/{chat_id}")
def read_chat(request: Request, chat_id: str, after: int = 0):
    _, chat = _owned(request, chat_id)
    return _public(chat, after)


@router.post("/chats/{chat_id}/messages")
def send(request: Request, chat_id: str, body: dict = Body(default={})):
    who, chat = _owned(request, chat_id)
    text = str(body.get("text") or "").strip()
    model = str(body.get("model") or "sonnet")
    if not text:
        raise HTTPException(422, "Write a message first.")
    if len(text) > MAX_MESSAGE:
        raise HTTPException(422, f"Keep a message under {MAX_MESSAGE:,} characters.")
    if model not in {m["id"] for m in MODELS}:
        raise HTTPException(422, "Pick a model.")
    tok = auth.store().token_info(who.email)
    if not tok:
        raise HTTPException(409, {"error": "Add your Claude token first.", "state": "token"})
    if tok["status"] == "invalid":
        raise HTTPException(409, {"error": "Your Claude token was rejected. Add it again.", "state": "token"})
    if _settle(chat).get("busy"):
        raise HTTPException(409, "Claude is still answering. Wait, or stop it.")
    now = time.time()
    chat.add_turn({"role": "user", "text": text, "at": now, "model": model},
                  {"role": "assistant", "text": "", "at": now, "model": model, "status": "queued",
                   "steps": [], "live": "", "detail": ""})
    n = len(chat.thread()["messages"]) // 2
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with (chat.dir / "turn.log").open("a") as out:
        proc = subprocess.Popen([sys.executable, "-m", "log_analyzer.agent.turn", str(chat.dir)],
                                cwd=str(REPO_ROOT), env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
    chat.set_busy({"since": now, "pid": proc.pid})
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it
    auth.note(request, who.email, "ask log agent", target=chat.scope()["title"], detail=model,
              job=f"{chat_id}#{n}")
    store.sweep_caches()
    return _public(chat, len(chat.thread()["messages"]) - 2)


@router.post("/chats/{chat_id}/stop")
def stop(request: Request, chat_id: str):
    _, chat = _owned(request, chat_id)
    busy = chat.thread().get("busy") or {}
    if busy.get("pid") and store.alive(busy["pid"]):
        try:
            os.killpg(int(busy["pid"]), signal.SIGTERM)
        except OSError:
            pass
    return _public(chat, max(chat.thread().get("messages", []).__len__() - 2, 0))
