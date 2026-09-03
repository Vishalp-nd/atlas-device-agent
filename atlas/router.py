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

import pandas as pd
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
from .critical_events_dashboard_service import (
    DashboardDataAccessError,
    DashboardConfig,
    load_ota_daily_counts,
    load_ota_date_bounds,
    load_ota_detail,
    load_ota_devices,
    load_ota_priority_counts,
    load_ota_priority_code_breakdown,
    load_ota_summary,
    load_ota_top_code_details,
    load_ota_top_codes,
    load_ota_top_devices,
    load_ota_top_processes,
    load_ota_type_counts,
)
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
    CriticalEventsDashboardBoundsResponse,
    CriticalEventsDashboardDevicesResponse,
    CriticalEventsDashboardFilterRequest,
    CriticalEventsDashboardResponse,
    CriticalEventsDashboardSummaryRequest,
    CriticalEventsQueryRequest,
    DownloadRef,
    IndexStatsResponse,
    ObservationsAgentQueryResponse,
    ObservationsQueryRequest,
    SessionCreateResponse,
)
from .session_store import session_store

router = APIRouter(prefix="/atlas", tags=["atlas"])
_DASHBOARD_CONFIG = DashboardConfig(repo_root=REPO_ROOT)


def _dashboard_or_503(loader):
    try:
        return loader()
    except DashboardDataAccessError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _parse_dashboard_ts(value: str | None):
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise HTTPException(status_code=422, detail=f"Invalid dashboard timestamp: {value}")
    return parsed


def _frame_rows(frame):
    if frame.empty:
        return []
    normalized = frame.copy()
    normalized = normalized.where(normalized.notna(), None)
    for column in normalized.columns:
        if str(normalized[column].dtype).startswith("datetime64"):
            normalized[column] = normalized[column].apply(lambda value: value.isoformat() if value is not None else None)
    return normalized.to_dict(orient="records")


def _coerce_chat_response(response: object) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, tuple) and response:
        first = response[0]
        if isinstance(first, str):
            return first
    return str(response)


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
    response, intent, downloads = run_supervisor(
        query=req.query,
        coverage_prompt=get_coverage_prompt(),
        critical_prompt=get_critical_prompt(),
        observations_prompt=get_observations_prompt(),
        repo_root=REPO_ROOT,
        history=history,
        last_intent=state.last_intent,
    )
    response_text = _coerce_chat_response(response)
    session_store.append_turn(req.session_id, req.query, response_text, intent)
    download_refs = [
        DownloadRef(
            id=d["id"],
            filename=d["filename"],
            url=(
                f"/atlas/agents/critical-events/download/{d['id']}"
                if intent == "critical_events"
                else f"/atlas/agents/observations/download/{d['id']}"
            ),
        )
        for d in downloads
    ]
    return ChatResponse(response=response_text, intent=intent, downloads=download_refs)


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


@router.post("/dashboard/critical-events/summary", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_summary(req: CriticalEventsDashboardSummaryRequest) -> CriticalEventsDashboardResponse:
    frame = _dashboard_or_503(lambda: load_ota_summary(_DASHBOARD_CONFIG, req.ota_versions))
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.get("/dashboard/critical-events/{ota_version}/date-bounds", response_model=CriticalEventsDashboardBoundsResponse)
def critical_events_dashboard_date_bounds(ota_version: str) -> CriticalEventsDashboardBoundsResponse:
    min_ts, max_ts = _dashboard_or_503(lambda: load_ota_date_bounds(_DASHBOARD_CONFIG, ota_version))
    return CriticalEventsDashboardBoundsResponse(
        min_timestamp=min_ts.isoformat() if min_ts is not None else None,
        max_timestamp=max_ts.isoformat() if max_ts is not None else None,
    )


@router.post("/dashboard/critical-events/{ota_version}/devices", response_model=CriticalEventsDashboardDevicesResponse)
def critical_events_dashboard_devices(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardDevicesResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    device_ids = _dashboard_or_503(
        lambda: load_ota_devices(
            _DASHBOARD_CONFIG,
            ota_version,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    )
    return CriticalEventsDashboardDevicesResponse(device_ids=device_ids)


@router.post("/dashboard/critical-events/{ota_version}/type-counts", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_type_counts(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_type_counts(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.post("/dashboard/critical-events/{ota_version}/priority-counts", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_priority_counts(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_priority_counts(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.post("/dashboard/critical-events/{ota_version}/daily-counts", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_daily_counts(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_daily_counts(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.post("/dashboard/critical-events/{ota_version}/top-processes", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_top_processes(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_top_processes(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.post("/dashboard/critical-events/{ota_version}/top-codes", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_top_codes(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_top_codes(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.post("/dashboard/critical-events/{ota_version}/top-code-details", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_top_code_details(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_top_code_details(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.post("/dashboard/critical-events/{ota_version}/priority-code-breakdown", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_priority_code_breakdown(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_priority_code_breakdown(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.post("/dashboard/critical-events/{ota_version}/top-devices", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_top_devices(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_top_devices(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


@router.post("/dashboard/critical-events/{ota_version}/detail", response_model=CriticalEventsDashboardResponse)
def critical_events_dashboard_detail(
    ota_version: str,
    req: CriticalEventsDashboardFilterRequest,
) -> CriticalEventsDashboardResponse:
    start_ts = _parse_dashboard_ts(req.start_ts)
    end_ts = _parse_dashboard_ts(req.end_ts)
    frame = _dashboard_or_503(
        lambda: load_ota_detail(_DASHBOARD_CONFIG, ota_version, req.device_ids, start_ts, end_ts, req.limit)
    )
    return CriticalEventsDashboardResponse(rows=_frame_rows(frame))


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
