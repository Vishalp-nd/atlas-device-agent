"""DeviceQAI sign-in for agent mode, from the device-automation checkout.

The sign-in itself (email code, allowlist, sessions), each person's Claude token (encrypted,
checked live) and the run caps are DeviceQAI's own (device-automation/deviceqa_auth): the
same store (DQA_AUTH_DIR) and token key (DQA_TOKEN_KEY_FILE) as the DeviceQAI platforms, so a
person allowed there, with a token there, is signed in to the same thing here.

The UI and this API are on different origins, so the session travels as a bearer token
(Authorization header) instead of DeviceQAI's cookie.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import sys
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Request

from ..config import REPO_ROOT

# The device-automation checkout with deviceqa_auth (DEVICEQA_ROOT; else DEVICE_AUTOMATION_ROOT,
# else the one next to this repo — /device-automation in Docker, see the Makefile)
DA_ROOT = Path(os.environ.get("DEVICEQA_ROOT") or os.environ.get("DEVICE_AUTOMATION_ROOT")
               or REPO_ROOT.parent / "device-automation").expanduser()
PLATFORM = "logs"  # the activity log's platform column
ENGINE = "setup-token"


class Unavailable(HTTPException):
    def __init__(self, why: str):
        super().__init__(503, why)


@lru_cache(maxsize=1)
def dqa():
    """The deviceqa_auth package (core, vault, mailer), or Unavailable."""
    if not (DA_ROOT / "deviceqa_auth" / "core.py").is_file():
        raise Unavailable(f"Agent mode needs a device-automation checkout with deviceqa_auth "
                          f"(DEVICEQA_ROOT; looked in {DA_ROOT}).")
    if str(DA_ROOT) not in sys.path:
        sys.path.append(str(DA_ROOT))
    from deviceqa_auth import core, mailer, vault  # noqa: PLC0415

    return core, vault, mailer


@lru_cache(maxsize=1)
def store():
    """DeviceQAI's sign-in store, minus its allowlist: in agent mode any address on the
    company domain (DQA_ALLOWED_DOMAIN, default netradyne.com) may sign in — the emailed code
    proves the mailbox is theirs. Only this process sees it this way; the DeviceQAI
    platforms keep their allowlist (and never see these sessions: they are bearer tokens
    sent to this API only)."""
    core, _, _ = dqa()

    class DomainAuth(core.Auth):
        def allowed(self, email: str) -> bool:
            email = core.norm_email(email)
            return bool(core.EMAIL_RE.match(email)) and email.endswith("@" + core.domain())

    return DomainAuth(core.auth_dir())


def bearer(request: Request) -> str:
    raw = request.headers.get("authorization", "")
    return raw[7:].strip() if raw.lower().startswith("bearer ") else ""


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def person(request: Request):
    """The signed-in person (deviceqa_auth.core.Who, kind 'user'), or 401."""
    who = store().session(bearer(request), client_ip(request))
    if who is None or who.kind != "user":
        raise HTTPException(401, "Sign in to use agent mode.")
    return who


def note(request: Request, email: str, action: str, target: str = "", detail: str = "",
         result: str = "", job: str = "") -> None:
    """One row in DeviceQAI's activity log (the admin page), platform 'logs'."""
    try:
        store().record(email=email, verified=True, engine=ENGINE, action=action,
                       service="log-analyzer", target=target, detail=detail, result=result,
                       job=job, ip=client_ip(request), platform=PLATFORM)
    except Exception:  # noqa: BLE001 — the log never breaks the action it logs
        pass


def _limit(name: str, default: int) -> int:
    core, _, _ = dqa()
    try:
        return max(1, int(core.setting(name, str(default))))
    except ValueError:
        return default


def free_memory() -> float:
    """Bytes of memory a new process can still use: the host's MemAvailable, or what is left
    under this container's cgroup limit if that is lower. inf when neither can be read."""
    free = float("inf")
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                free = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass
    try:
        limit = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if limit != "max":
            used = int(Path("/sys/fs/cgroup/memory.current").read_text().strip())
            free = min(free, int(limit) - used)
    except (OSError, ValueError):
        pass
    return free


def start_lock():
    """Held (blocking) while a turn decides whether it may start, and for a few seconds after
    it starts, so queued turns are let in one by one and each sees the memory the previous
    one took. Shared by every turn process. Returns the open lock file (close to release)."""
    core, _, _ = dqa()
    slots = core.auth_dir() / "run-slots"
    slots.mkdir(parents=True, exist_ok=True)
    fh = (slots / "agent-start.lock").open("a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def take_slot(email: str):
    """A run slot under DeviceQAI's caps, shared with its server runs through the same lock
    files: DQA_RUNS_PER_USER (2) per person, DQA_RUNS_MAX (10) on the server. Returns the
    held lock files (released when they are closed, or when the process exits) or None."""
    core, _, _ = dqa()
    slots = core.auth_dir() / "run-slots"
    slots.mkdir(parents=True, exist_ok=True)

    def grab(prefix: str, n: int):
        for i in range(n):
            fh = (slots / f"{prefix}-{i}.lock").open("a+")
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fh
            except OSError:
                fh.close()
        return None

    who = hashlib.sha256(email.encode()).hexdigest()[:12]
    mine = grab(f"u-{who}", _limit("DQA_RUNS_PER_USER", 2))
    if mine is None:
        return None
    every = grab("all", _limit("DQA_RUNS_MAX", 10))
    if every is None:
        mine.close()
        return None
    return mine, every
