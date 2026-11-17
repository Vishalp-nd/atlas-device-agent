"""One turn of an agent chat: Claude Code on the server with the person's own token.

    python -m log_analyzer.agent.turn <chat-dir>

Started by the API (log_analyzer/agent/router.py) in its own process group, so Stop kills it
and Claude together. The chat's last message is the assistant reply this turn fills in; its
progress (steps, the text as it streams) is written to thread.json for the page to poll.

Claude gets no shell, no web, no edits: the log tools of this chat (log_tools.py, its scope
only) and Read/Grep/Glob on the run's report folders. Each turn resumes the chat's Claude
session, kept in the chat's own config folder.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import auth
from .store import Chat

MAX_SECONDS = int(os.environ.get("LA_AGENT_TURN_SECONDS", str(30 * 60)))
# Memory admission: a turn starts only if the server has at least LA_AGENT_MIN_FREE_GB left
# over after the LA_AGENT_TURN_GB one turn (Claude Code + the log tools) may take.
MIN_FREE_GB = float(os.environ.get("LA_AGENT_MIN_FREE_GB", "2"))
TURN_GB = float(os.environ.get("LA_AGENT_TURN_GB", "0.6"))
START_SETTLE = float(os.environ.get("LA_AGENT_START_SETTLE", "5"))
MAX_TURNS = os.environ.get("LA_AGENT_MAX_TURNS", "80")
HERE = Path(__file__).resolve().parent
SECRETS = (".env", ".env.*", "*.key", "*.pem", "*.p12", "auth.db*", "auth.secret", ".netrc",
           ".git-credentials", ".deviceqa/**", "*token*", ".ssh/**", ".aws/**",
           "db_credentials.ini", "meta.json", "scope.json")
DENY = ["Bash", "WebFetch", "WebSearch", "Edit", "Write", "NotebookEdit",
        *(f"{tool}(//**/{name})" for name in SECRETS for tool in ("Read", "Grep"))]

INSTRUCTIONS = """\
# Log Monitoring Agent
You are the agent of Netradyne's Log Monitoring Agent (the Log Analyzer), answering one
person's questions about one analysis: {title}. You run headless on the server, read-only.

What you can see — nothing else exists for you:
- Device logs, through the `logs` tools only: {devices}. Dates: each run's range plus one day
  either side ({dates}). Call `scope` first if unsure. A day's logs are rebuilt from its
  archives the first time you open it (a few seconds), one <service>.log per service
  (ndcentral.log, health.log, conn_mgr.log, …) plus syslogs/. Day folders follow the upload
  date, so a day can hold older lines uploaded late.
- The analysis reports, with Read/Grep/Glob, under: {reports}. Per OTA: ota_report.json (all
  test-case verdicts, KPIs, config checks) and ota_report.html, devices/<device>.json and .html
  (that device's boots and verdicts), alert_sessions.csv. A comparison also has its
  compare_report.html. Prefer the .json files; the .html is the same data, rendered.

Log lines start `<epoch ms>: <uptime ms>: <TAG>: <level>: <pid>: <tid>: message`; the epoch is
UTC. Use read_log_by_time to look around a moment, search_logs with count_only before pulling
many matches, and narrow by file.

How to answer:
- Ground every claim in what you read: name the device, date, file and line (or the report
  field). Quote the decisive log lines. Say plainly when the logs don't show something.
- Lead with the answer, then the evidence. Keep it tight; tables are fine for comparisons.
- Times in UTC, and say so. Device IDs and OTA versions exactly as written.
- You cannot change anything, rerun the analysis, or reach anything outside the above. If
  asked, say what the person would need to do instead.
"""


def _say(name: str, args: dict) -> str:
    tool = name.split("__")[-1]
    keys = ("pattern", "file", "file_path", "path", "start", "date", "device", "description")
    bits = [str(args[k]) for k in keys if args.get(k)]
    text = " · ".join(bits[:3]).replace(str(Path.cwd()) + "/", "")
    return f"{tool} · {text[:110]}" if text else tool


def _classify(text: str) -> str:
    low = text.lower()
    if any(w in low for w in ("401", "oauth", "invalid api", "unauthor", "token is invalid",
                              "not logged in", "revoked", "expired", "invalid bearer")):
        return "token"
    if any(w in low for w in ("usage limit", "rate limit", "limit reached", "529", "overloaded")):
        return "limit"
    return ""


def main(chat_dir: str) -> int:
    chat = Chat(Path(chat_dir))
    meta = chat.meta()
    email = meta["email"]
    reply = chat.last()
    if reply is None or reply.get("role") != "assistant":
        return 1
    user_msg = chat.thread()["messages"][-2]
    stopping = {"why": ""}

    def save(force: bool = False, **fields) -> None:
        reply.update(fields)
        chat.put_last(reply, force=force)

    def on_term(signum, _frame):
        stopping["why"] = "stopped"
        if proc_holder:
            try:
                proc_holder[0].terminate()
            except OSError:
                pass
        else:
            save(force=True, status="stopped", detail="Stopped.", ended=time.time())
            chat.set_busy(None)
            os._exit(0)

    proc_holder: list = []
    signal.signal(signal.SIGTERM, on_term)

    # Start only when there is a slot under DeviceQAI's caps (per person, server-wide) AND
    # enough free memory for one more turn above the safety margin. Turns start one by one
    # (start_lock), each holding the lock a few seconds after it starts so the next one sees
    # the memory it took. A turn waiting for memory holds no slot.
    need = (MIN_FREE_GB + TURN_GB) * 1024 ** 3
    while True:
        gate = auth.start_lock()
        held = auth.take_slot(email)
        if held is None:
            gate.close()
            save(status="queued", detail="Waiting for a free slot (your other Claude answers, "
                                         "or the server's limit).")
            time.sleep(3)
            continue
        free = auth.free_memory()
        if free >= need:
            break
        for fh in held:
            fh.close()
        gate.close()
        save(status="queued", detail=f"Waiting for server memory: {free / 1024 ** 3:.1f} GB free, "
                                     f"{need / 1024 ** 3:.1f} GB needed to start (an analysis "
                                     "may be running).")
        time.sleep(5)

    _, vault, _ = auth.dqa()
    store = auth.store()
    sealed = store.token_sealed(email)
    if sealed is None:
        save(force=True, status="failed", detail="No Claude token. Add it first.", ended=time.time())
        chat.set_busy(None)
        return 1
    try:
        token = vault.open_(*sealed, email)
    except vault.TokenError as exc:
        save(force=True, status="failed", detail=exc.message, ended=time.time())
        chat.set_busy(None)
        return 1
    exe = vault.claude_bin()
    if not exe:
        save(force=True, status="failed", detail="Claude Code isn't installed on the server.",
             ended=time.time())
        chat.set_busy(None)
        return 1

    scope = chat.scope()
    reports = scope["reports_dirs"]
    scoped = [f"{tool}(/{p}/**)" for p in reports for tool in ("Read", "Grep", "Glob")]
    (chat.dir / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": scoped, "deny": DENY}}, indent=1))
    lib_env = {"LA_SCOPE": str(chat.dir / "scope.json"), "LA_CACHE": str(chat.cache_dir()),
               "LA_AGENT_CACHE_DAYS": os.environ.get("LA_AGENT_CACHE_DAYS", "4"),
               "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    (chat.dir / "mcp.json").write_text(json.dumps({"mcpServers": {"logs": {
        "command": sys.executable, "args": [str(HERE / "log_tools.py")], "env": lib_env}}}))

    devices = sorted({d["device"] for r in scope["runs"] for d in r["devices"]})
    dates = "; ".join(f"run #{r['run_id']} {r['start']} → {r['end']} ({r['environment']})"
                      for r in scope["runs"])
    system = INSTRUCTIONS.format(
        title=scope["title"], dates=dates or "none", reports=", ".join(reports) or "none",
        devices=(", ".join(devices[:30]) + (f" and {len(devices) - 30} more" if len(devices) > 30 else "")))
    allowed = ",".join(["mcp__logs", "TodoWrite", "Task", "Agent", *scoped])
    model = reply.get("model") or "sonnet"
    argv = [exe, "-p", user_msg["text"], "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--model", model, "--max-turns", MAX_TURNS,
            "--strict-mcp-config", "--mcp-config", str(chat.dir / "mcp.json"),
            "--settings", str(chat.dir / "settings.json"),
            "--allowedTools", allowed, "--disallowedTools", ",".join(DENY),
            "--append-system-prompt", system]
    for p in reports:
        argv += ["--add-dir", p]
    if meta.get("claude_session"):
        argv += ["--resume", meta["claude_session"]]

    # Only what Claude needs: no server secrets in its environment.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ") if k in os.environ}
    env.update(HOME=str(chat.home_dir()), CLAUDE_CONFIG_DIR=str(chat.config_dir()),
               CLAUDE_CODE_OAUTH_TOKEN=token, DISABLE_AUTOUPDATER="1", ENABLE_TOOL_SEARCH="false",
               DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1")

    turn_dir = chat.dir / "turns" / str(len(chat.thread()["messages"]) // 2)
    turn_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    save(force=True, status="running", detail="", started=t0, live="", steps=[])
    stream_log = (turn_dir / "stream.jsonl").open("w")
    with (turn_dir / "stderr.txt").open("w") as err:
        proc = subprocess.Popen(argv, cwd=chat.work_dir(), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=err, text=True, bufsize=1)
        proc_holder.append(proc)
        # Let the next queued turn in once this one has taken its memory
        threading.Timer(START_SETTLE, gate.close).start()
        result: dict = {}
        pending: dict[str, int] = {}
        steps: list[dict] = reply["steps"]
        live = ""
        last_push = 0.0
        for line in proc.stdout:
            stream_log.write(line)
            if time.time() - t0 > MAX_SECONDS and not stopping["why"]:
                stopping["why"] = "timeout"
                proc.terminate()
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "system" and ev.get("session_id") and not meta.get("claude_session"):
                meta["claude_session"] = ev["session_id"]
                chat.put_meta(meta)
            elif kind == "stream_event" and not ev.get("parent_tool_use_id"):
                e = ev.get("event") or {}
                if e.get("type") == "message_start":
                    live = ""
                elif e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                    live += e["delta"].get("text", "")
            elif kind == "assistant":
                sub = bool(ev.get("parent_tool_use_id"))
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        pending[block.get("id") or ""] = len(steps)
                        steps.append({"t": round(time.time() - t0, 1), "s": _say(block.get("name") or "",
                                      block.get("input") or {}), "sub": sub})
                    elif block.get("type") == "text" and block.get("text", "").strip() and not sub:
                        steps.append({"t": round(time.time() - t0, 1), "note": block["text"].strip()[:400]})
            elif kind == "user":
                content = (ev.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if block.get("type") == "tool_result" and block.get("is_error"):
                        i = pending.get(block.get("tool_use_id") or "")
                        if i is not None:
                            steps[i]["error"] = True
            elif kind == "result":
                result = ev
            if time.time() - last_push > 0.5:
                save(live=live, steps=steps[-200:])
                last_push = time.time()
        proc.wait()
    stream_log.close()

    if result.get("session_id"):
        meta["claude_session"] = result["session_id"]
        chat.put_meta(meta)
    text = str(result.get("result") or "")
    stats = {"seconds": round(time.time() - t0, 1), "turns": result.get("num_turns"),
             "cost": result.get("total_cost_usd"),
             "tokens_out": (result.get("usage") or {}).get("output_tokens")}
    common = {"live": "", "steps": steps[-200:], "ended": time.time(), **stats}
    if stopping["why"] == "stopped":
        save(force=True, status="stopped", text=text or live, detail="Stopped.", **common)
    elif stopping["why"] == "timeout":
        save(force=True, status="failed", text=text or live,
             detail=f"Stopped after {MAX_SECONDS // 60} min.", **common)
    elif result and not result.get("is_error") and proc.returncode == 0:
        save(force=True, status="done", text=text, detail="", **common)
    else:
        errors = (turn_dir / "stderr.txt").read_text(errors="replace")[-2000:]
        why = _classify(text + "\n" + errors)
        if why == "token":
            store.token_mark(email, "invalid", "Claude rejected this token in agent mode.")
            detail = "Claude rejected your token. Add it again."
        elif why == "limit":
            detail = "Your Claude usage limit is reached. Try again later."
        else:
            tail = (text or errors).strip().splitlines()
            detail = (tail[-1] if tail else f"Claude exited {proc.returncode}.")[:300]
        save(force=True, status="failed", text=text if not why else "", detail=detail, **common)
    store.finish(chat.meta()["id"] + f"#{len(chat.thread()['messages']) // 2}",
                 {"done": "ok", "stopped": "cancelled"}.get(reply["status"], "failed"),
                 reply.get("detail") or "")
    chat.set_busy(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
