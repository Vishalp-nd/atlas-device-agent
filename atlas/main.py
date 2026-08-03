"""main.py — Atlas Device Agent: the root FastAPI service.

This file stays generic on purpose — it only mounts routers and exposes a
top-level health check. Agent-specific logic lives in the routers.

Serve with:
    python -m atlas serve
or:
    uvicorn atlas.main:app --reload
(run from the repo root, atlas-device-agent/, so `atlas` resolves as a
top-level package)
"""

from __future__ import annotations

from fastapi import FastAPI

from .router import router as atlas_router

app = FastAPI(title="Atlas Device Agent", version="1.0.0")

app.include_router(atlas_router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
