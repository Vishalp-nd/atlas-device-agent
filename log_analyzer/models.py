"""models.py — Pydantic request/response schemas for the Log Analyzer API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    mode: Literal["ota", "devices"]
    environment: Literal["stag", "prod"] = "stag"
    """Staging or production: the logs bucket, the device db (OTA -> devices) and the local log cache."""
    ota_version: str | None = None
    """mode=ota: analyse up to 200 devices currently on this OTA; only its boots are reported."""
    devices: list[str] = []
    """mode=devices: device ids to analyse (every OTA found in their logs is reported)."""
    start: str
    """Inclusive UTC start date, YYYY-MM-DD."""
    end: str
    """Inclusive UTC end date, YYYY-MM-DD."""
    product: str | None = None
    """OTA package product folder override (default: inferred from the version)."""
    max_devices: int | None = Field(default=None, ge=1)
    workers: int | None = Field(default=None, ge=1, le=64)
    extract_logs: bool = False
    """Also extract downloaded archives into <day>/logs (other tools need that; the report doesn't)."""
    requested_by: str | None = None


class Run(BaseModel):
    id: int
    mode: str
    environment: str
    ota_version: str | None
    requested_devices: list[str]
    devices: list[str]
    start_date: str
    end_date: str
    product: str | None
    options: dict
    status: str
    stage: str | None
    progress: dict
    error: str | None
    device_errors: dict
    pid: int | None
    requested_by: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_s: float | None
    output_dir: str | None
    log_path: str | None


class RunReport(BaseModel):
    ota_version: str
    devices: int
    boots: int
    pass_: int = Field(alias="pass")
    fail: int
    not_triggered: int
    config_keys: int
    config_keys_failing: int
    config_error: str | None
    summary: dict = {}
    """devices_given / _active / _on_ota / _detailed_logs, drive_minutes_total, devices_over_6h / _24h, devices_low_power, ..."""
    url: str

    model_config = {"populate_by_name": True}


class RunDetail(BaseModel):
    run: Run
    reports: list[RunReport]
    steps: list[dict]
    """Per step: count, total_s, avg_s, max_s, slowest_device."""
    devices: list[dict]
    """Per device: download_s, analyse_s, days."""


class RunList(BaseModel):
    runs: list[Run]


class RunLog(BaseModel):
    text: str
    offset: int
    """Byte offset to pass as ?offset= for the next chunk."""
    done: bool


class OtaSuggestions(BaseModel):
    otas: list[str]


class Environment(BaseModel):
    key: str
    label: str
    bucket: str
    device_db: str
    log_root: str


class Environments(BaseModel):
    environments: list[Environment]
    default: str
