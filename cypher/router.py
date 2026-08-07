"""FastAPI surface for the KG agent, mounted under /cypher by atlas/main.py.

Handler is sync `def` on purpose — the Neo4j and Anthropic calls beneath it
block, and FastAPI runs sync handlers in a worker thread so they don't stall
the event loop. Making it `async def` while calling blocking code would.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import config
from .agent_graph import run_kg_agent
from .models import KGQueryRequest, KGQueryResponse

router = APIRouter(prefix="/cypher", tags=["cypher"])

# Session history is shared with the atlas agents so a session_id works across both.
try:
    from atlas.session_store import session_store
    from atlas.supervisor_graph import MAX_HISTORY
except Exception:  # pragma: no cover - standalone use without the atlas package
    session_store = None
    MAX_HISTORY = 6


def _history(session_id: str | None) -> list | None:
    if not session_id:
        return None
    if session_store is None:
        raise HTTPException(status_code=503, detail="Session store unavailable")
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=404, detail="Unknown session_id — call POST /atlas/sessions first"
        )
    return state.messages[-MAX_HISTORY:] if MAX_HISTORY > 0 else []


@router.post("/query", response_model=KGQueryResponse)
def query(req: KGQueryRequest) -> KGQueryResponse:
    history = _history(req.session_id)
    try:
        answer = run_kg_agent(req.query, config.get_kg_prompt(), history=history)
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if req.session_id and session_store is not None:
        session_store.append_turn(req.session_id, req.query, answer, "knowledge_graph")
    return KGQueryResponse(response=answer)
