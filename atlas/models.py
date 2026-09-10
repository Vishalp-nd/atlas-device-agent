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
    clickhouse_section: str = "CLICKHOUSE_DB"


class CriticalEventsDashboardFilterRequest(BaseModel):
    ota_version: str
    device_ids: list[str] = []
    start_ts: str | None = None
    end_ts: str | None = None
    limit: int | None = None


class CriticalEventsDashboardSummaryRequest(BaseModel):
    ota_versions: list[str] = []


class CriticalEventsDashboardResponse(BaseModel):
    rows: list[dict[str, object]]


class CriticalEventsDashboardBoundsResponse(BaseModel):
    min_timestamp: str | None
    max_timestamp: str | None


class CriticalEventsDashboardDevicesResponse(BaseModel):
    device_ids: list[str]


class AllowedOtaVersionsResponse(BaseModel):
    ota_versions: list[str]
    limit: int


class AllowedOtaVersionAddRequest(BaseModel):
    ota_version: str


class AllowedOtaVersionRemoveRequest(BaseModel):
    ota_version: str


class GpsAvailabilityResponse(BaseModel):
    """Days that observation_data actually holds, oldest first."""
    rows: list[dict[str, object]]
    min_day: str | None = None
    max_day: str | None = None


class GpsProductLinesResponse(BaseModel):
    product_lines: list[str]


class GpsReportRequest(BaseModel):
    product_line: str
    start: str
    """Inclusive UTC start date, YYYY-MM-DD."""
    end: str
    """Inclusive UTC end date, YYYY-MM-DD -- the whole day is covered."""
    force: bool = False
    """Regenerate even when reports for this range already exist on the backend."""


class GpsReportResponse(BaseModel):
    download: DownloadRef
    reused: bool
    """True when an already-generated report set was served instead of re-querying."""
    output_dir: str
    folder_name: str
    report_names: list[str]
    size_bytes: int


class GpsReportStatusResponse(BaseModel):
    """Whether the backend already holds a zip for a (product line, range)."""
    cached: bool
    output_dir: str
    folder_name: str
    report_names: list[str]
    size_bytes: int | None = None


class ObservationsQueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    table_name: str = "observation_data"
    clickhouse_section: str = "CLICKHOUSE_DB"


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
