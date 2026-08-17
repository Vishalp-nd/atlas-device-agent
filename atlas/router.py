"""router.py — the Atlas agent's FastAPI surface.

Everything Atlas exposes over HTTP lives in this one router: session-aware
supervisor chat, direct per-sub-agent access (coverage/jenkins/critical-events/observations),
and a small index-stats endpoint for the Streamlit header. Mounted by
atlas/main.py under the "/atlas" prefix.

Route handlers are plain `def` (not `async def`) on purpose: the underlying
run_supervisor/run_*_agent calls are blocking, I/O-heavy (Anthropic, Jenkins,
Postgres, Snowflake). FastAPI runs sync route handlers in a worker thread pool
automatically, so this doesn't stall the event loop for other requests. Making
these `async def` while calling blocking functions directly would.

To add a new agent to the service: add a router module exposing
`router = APIRouter(prefix="/<name>", ...)`, then add one
`app.include_router(...)` line in atlas/main.py.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException

from .config import (
    DEVICE_AUTOMATION_ROOT,
    REPO_ROOT,
    SKILLS_ROOT,
    get_coverage_prompt,
    get_critical_prompt,
    get_jenkins_prompt,
    get_observations_prompt,
)
from .coverage_agent_graph import run_coverage_agent
from .critical_events_agent_graph import run_critical_events_agent
from .jenkins_agent_graph import run_jenkins_agent
from .observations_agent_graph import run_observations_agent
from .result_store import result_store
from .supervisor_graph import MAX_HISTORY, run_supervisor
from .models import (
    AgentQueryRequest,
    AgentQueryResponse,
    AgentQueryWithDownloadsResponse,
    ChatRequest,
    ChatResponse,
    CriticalEventsQueryRequest,
    DownloadRef,
    IndexStatsResponse,
    ObservationsAgentQueryResponse,
    ObservationsQueryRequest,
    SessionCreateResponse,
)
from .session_store import session_store

router = APIRouter(prefix="/atlas", tags=["atlas"])


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.post("/sessions", response_model=SessionCreateResponse)
def create_session() -> SessionCreateResponse:
    return SessionCreateResponse(session_id=session_store.create())


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    if not session_store.delete(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True}


# ── Supervisor-routed chat ────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    state = session_store.get(req.session_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown session_id — call POST /atlas/sessions first",
        )

    history = state.messages[-MAX_HISTORY:] if MAX_HISTORY > 0 else []
    response, intent = run_supervisor(
        query=req.query,
        coverage_prompt=get_coverage_prompt(),
        critical_prompt=get_critical_prompt(),
        observations_prompt=get_observations_prompt(),
        repo_root=REPO_ROOT,
        history=history,
        last_intent=state.last_intent,
    )
    session_store.append_turn(req.session_id, req.query, response, intent)
    return ChatResponse(response=response, intent=intent)


# ── Direct sub-agent access (bypasses the supervisor's classification) ───────
#
# session_id is optional on all three: pass one (from POST /atlas/sessions) for
# multi-turn continuity without paying the supervisor's classification cost;
# omit it for a fully stateless one-shot call, unchanged from before.

def _load_session_history(session_id: str) -> list:
    state = session_store.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown session_id — call POST /atlas/sessions first",
        )
    return state.messages[-MAX_HISTORY:] if MAX_HISTORY > 0 else []


@router.post("/agents/coverage", response_model=AgentQueryResponse)
def coverage_agent(req: AgentQueryRequest) -> AgentQueryResponse:
    history = _load_session_history(req.session_id) if req.session_id else None
    response = run_coverage_agent(req.query, get_coverage_prompt(), REPO_ROOT, history=history)
    if req.session_id:
        session_store.append_turn(req.session_id, req.query, response, "coverage")
    return AgentQueryResponse(response=response)


@router.post("/agents/jenkins", response_model=AgentQueryResponse)
def jenkins_agent(req: AgentQueryRequest) -> AgentQueryResponse:
    history = _load_session_history(req.session_id) if req.session_id else None
    response = run_jenkins_agent(req.query, get_jenkins_prompt(), history=history)
    if req.session_id:
        session_store.append_turn(req.session_id, req.query, response, "jenkins")
    return AgentQueryResponse(response=response)


@router.post("/agents/critical-events", response_model=AgentQueryWithDownloadsResponse)
def critical_events_agent(req: CriticalEventsQueryRequest) -> AgentQueryWithDownloadsResponse:
    history = _load_session_history(req.session_id) if req.session_id else None
    response_text, downloads = run_critical_events_agent(
        req.query,
        get_critical_prompt(),
        REPO_ROOT,
        table_name=req.table_name,
        postgres_section=req.postgres_section,
        history=history,
    )
    if req.session_id:
        session_store.append_turn(req.session_id, req.query, response_text, "critical_events")
    download_refs = [
        DownloadRef(
            id=d["id"],
            filename=d["filename"],
            url=f"/atlas/agents/critical-events/download/{d['id']}",
        )
        for d in downloads
    ]
    return AgentQueryWithDownloadsResponse(response=response_text, downloads=download_refs)


@router.get("/agents/critical-events/download/{result_id}")
def critical_events_download(result_id: str):
    from fastapi.responses import Response
    entry = result_store.get(result_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Result not found or expired")
    data, filename = entry
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/agents/observations", response_model=ObservationsAgentQueryResponse)
def observations_agent(req: ObservationsQueryRequest) -> ObservationsAgentQueryResponse:
    history = _load_session_history(req.session_id) if req.session_id else None
    response_text, downloads = run_observations_agent(
        req.query,
        get_observations_prompt(),
        REPO_ROOT,
        table_name=req.table_name,
        postgres_section=req.postgres_section,
        history=history,
    )
    if req.session_id:
        session_store.append_turn(req.session_id, req.query, response_text, "observations")
    download_refs = [
        DownloadRef(
            id=d["id"],
            filename=d["filename"],
            url=f"/atlas/agents/observations/download/{d['id']}",
        )
        for d in downloads
    ]
    return ObservationsAgentQueryResponse(response=response_text, downloads=download_refs)


@router.get("/agents/observations/download/{result_id}")
def observations_download(result_id: str):
    from fastapi.responses import Response
    entry = result_store.get(result_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Result not found or expired")
    data, filename = entry
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Meta ──────────────────────────────────────────────────────────────────────

@router.get("/index-stats", response_model=IndexStatsResponse)
def index_stats() -> IndexStatsResponse:
    skills = sum(1 for _ in SKILLS_ROOT.rglob("SKILL.md"))
    # test_cases/ stays in the device-automation repo; count them only when a
    # checkout is available via DEVICE_AUTOMATION_ROOT.
    testcases = 0
    if DEVICE_AUTOMATION_ROOT is not None:
        testcases_root = DEVICE_AUTOMATION_ROOT / "pytest_device_validator" / "test_cases"
        testcases = sum(1 for _ in testcases_root.rglob("*.yaml"))
    return IndexStatsResponse(skills=skills, testcases=testcases)
