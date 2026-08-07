"""Request/response schemas for the KG agent API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KGQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None,
        description="From POST /atlas/sessions. Omit for a stateless one-shot call.",
    )


class KGQueryResponse(BaseModel):
    response: str
