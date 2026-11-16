"""Agent chats on disk, one folder each under RUNS_DIR/agent/<chat id>/ (0700):

    meta.json     whose chat, about what, its Claude session id
    scope.json    what it may see (scope.build), fixed when the chat starts
    thread.json   the messages, and the turn running now (busy)
    claude/ home/ work/   Claude Code's config (its sessions, for --resume), HOME and cwd
    turns/<n>/    each turn's raw stream and stderr

Rebuilt log days live apart, in LA_AGENT_CACHE_DIR/<chat id>/ (default OUTPUT/agent_cache):
on disk, not /dev/shm, so chats never take the RAM the analysis needs.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

from ..config import REPO_ROOT, RUNS_DIR

ROOT = RUNS_DIR / "agent"
CACHE_ROOT = Path(os.environ.get("LA_AGENT_CACHE_DIR", REPO_ROOT / "OUTPUT" / "agent_cache"))
CACHE_HOURS = float(os.environ.get("LA_AGENT_CACHE_HOURS", "2"))


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, default=str))
    os.replace(tmp, path)


class Chat:
    def __init__(self, folder: Path):
        self.dir = Path(folder)

    @classmethod
    def create(cls, chat_id: str, meta: dict, scope: dict) -> "Chat":
        ROOT.mkdir(parents=True, exist_ok=True)
        folder = ROOT / chat_id
        folder.mkdir(mode=0o700)
        chat = cls(folder)
        _write(folder / "scope.json", scope)
        _write(folder / "meta.json", {**meta, "id": chat_id})
        _write(folder / "thread.json", {"messages": [], "busy": None})
        return chat

    # ── files ──────────────────────────────────────────────────────────────
    @contextmanager
    def _locked(self):
        with (self.dir / ".lock").open("a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield

    def meta(self) -> dict:
        return json.loads((self.dir / "meta.json").read_text())

    def put_meta(self, meta: dict) -> None:
        with self._locked():
            _write(self.dir / "meta.json", meta)

    def scope(self) -> dict:
        return json.loads((self.dir / "scope.json").read_text())

    def thread(self) -> dict:
        try:
            return json.loads((self.dir / "thread.json").read_text())
        except (OSError, ValueError):
            return {"messages": [], "busy": None}

    def last(self) -> dict | None:
        msgs = self.thread()["messages"]
        return msgs[-1] if msgs else None

    def put_last(self, message: dict, force: bool = True) -> None:  # noqa: ARG002
        with self._locked():
            t = self.thread()
            if t["messages"]:
                t["messages"][-1] = message
                _write(self.dir / "thread.json", t)

    def add_turn(self, user: dict, reply: dict) -> None:
        with self._locked():
            t = self.thread()
            t["messages"] += [user, reply]
            t["busy"] = {"since": time.time(), "pid": None}
            _write(self.dir / "thread.json", t)

    def set_busy(self, busy: dict | None) -> None:
        with self._locked():
            t = self.thread()
            t["busy"] = busy
            _write(self.dir / "thread.json", t)

    def touch(self) -> None:
        (self.dir / "thread.json").touch()

    # ── folders Claude uses ───────────────────────────────────────────────
    def _sub(self, name: str) -> Path:
        p = self.dir / name
        p.mkdir(mode=0o700, exist_ok=True)
        return p

    def config_dir(self) -> Path:
        return self._sub("claude")

    def home_dir(self) -> Path:
        return self._sub("home")

    def work_dir(self) -> Path:
        return self._sub("work")

    def cache_dir(self) -> Path:
        p = CACHE_ROOT / self.dir.name
        p.mkdir(parents=True, exist_ok=True)
        return p


def chats_of(email: str, kind: str, target_id: int) -> list[Chat]:
    """This person's chats about this run / comparison, newest first."""
    if not ROOT.is_dir():
        return []
    found = []
    for folder in ROOT.iterdir():
        try:
            m = json.loads((folder / "meta.json").read_text())
        except (OSError, ValueError):
            continue
        if m.get("email") == email and m.get("kind") == kind and m.get("target_id") == target_id:
            found.append((m.get("created") or 0, Chat(folder)))
    return [c for _, c in sorted(found, key=lambda x: x[0], reverse=True)]


def get(chat_id: str) -> Chat | None:
    if not chat_id or "/" in chat_id or chat_id.startswith("."):
        return None
    folder = ROOT / chat_id
    return Chat(folder) if (folder / "meta.json").is_file() else None


def alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def sweep_caches() -> None:
    """Drop the rebuilt days of chats nobody has used for CACHE_HOURS."""
    if not CACHE_ROOT.is_dir():
        return
    cutoff = time.time() - CACHE_HOURS * 3600
    for folder in CACHE_ROOT.iterdir():
        chat = get(folder.name)
        if chat is None:
            shutil.rmtree(folder, ignore_errors=True)
            continue
        busy = chat.thread().get("busy")
        try:
            used = (chat.dir / "thread.json").stat().st_mtime
        except OSError:
            used = 0
        if not (busy and alive(busy.get("pid"))) and used < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
