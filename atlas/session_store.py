"""session_store.py — in-memory, thread-safe chat session store.

Each session holds the raw LangChain message history and the last classified
intent, mirroring what coverage_chatbot_app.py used to keep in st.session_state.
There is no LangGraph checkpointer or database backing this — sessions live only
for the lifetime of this process and are keyed by an opaque session_id so a
single-worker server can serve multiple concurrent users without their
histories colliding.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

_DEFAULT_MAX_AGE_SECONDS = 24 * 3600


@dataclass
class SessionState:
    messages: list[BaseMessage] = field(default_factory=list)
    last_intent: str = ""
    last_active: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._max_age_seconds = max_age_seconds

    def _purge_stale_locked(self) -> None:
        cutoff = time.time() - self._max_age_seconds
        stale = [sid for sid, state in self._sessions.items() if state.last_active < cutoff]
        for sid in stale:
            del self._sessions[sid]

    def create(self) -> str:
        session_id = uuid.uuid4().hex
        with self._lock:
            self._purge_stale_locked()
            self._sessions[session_id] = SessionState()
        return session_id

    def get(self, session_id: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def append_turn(self, session_id: str, query: str, response: str, intent: str) -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return
            state.messages.append(HumanMessage(content=query))
            state.messages.append(AIMessage(content=response))
            state.last_intent = intent
            state.last_active = time.time()


session_store = SessionStore()
