"""models.py — Pydantic request/response schemas for the Atlas agent API."""

from __future__ import annotations

from pydantic import BaseModel


class SessionCreateResponse(BaseModel):
    session_id: str


class ChatRequest(BaseModel):
    session_id: str
    query: str


class ChatResponse(BaseModel):
    response: str
    intent: str
    downloads: list[DownloadRef] = []


class AgentQueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    """Optional. Pass a session_id (from POST /atlas/sessions) for multi-turn
    continuity without going through the supervisor's classification. Omit
    for a fully stateless one-shot call (unchanged default behavior)."""


class AgentQueryResponse(BaseModel):
    response: str


class AgentQueryWithDownloadsResponse(BaseModel):
    response: str
    downloads: list[DownloadRef] = []


class CriticalEventsQueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    table_name: str = "criticalinfo_snowflakes_data"
    postgres_section: str = "IRAVATH_DB"


class ObservationsQueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    table_name: str = "public.extracteddata"
    postgres_section: str = "IRAVATH_DB"


class DownloadRef(BaseModel):
    id: str
    filename: str
    url: str


class ObservationsAgentQueryResponse(BaseModel):
    response: str
    downloads: list[DownloadRef] = []


class IndexStatsResponse(BaseModel):
    skills: int
    testcases: int
