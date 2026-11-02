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

import logging
import os

import logfire
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from log_analyzer.config import UI_DIR as LOG_ANALYZER_UI_DIR
from log_analyzer.router import router as log_analyzer_router

from .router import router as atlas_router

app = FastAPI(title="Atlas Device Agent", version="1.0.0")

app.include_router(atlas_router)
# Log Analyzer: its own API under /log-analyzer and a standalone UI (not Streamlit) at /log-analyzer/ui/
app.include_router(log_analyzer_router)
# The Log Analyzer UI may be served from elsewhere (another port, a file) and call this
# backend; LOG_ANALYZER_CORS_ORIGINS: comma-separated origins, default any
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("LOG_ANALYZER_CORS_ORIGINS", "*").split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.mount("/log-analyzer/ui", StaticFiles(directory=LOG_ANALYZER_UI_DIR, html=True), name="log-analyzer-ui")


@app.get("/", include_in_schema=False)
def root_redirect():
    """Nothing lives at /; send browsers to the Log Analyzer UI."""
    return RedirectResponse("/log-analyzer/ui/")

# configure() raises LogfireConfigError with no token/local auth (e.g. a fresh
# EC2 host before `logfire auth` or LOGFIRE_TOKEN is set); instrument_fastapi()
# separately requires the `fastapi` extra. Split so a failure in one doesn't
# get mislabeled as the other, and neither takes the whole service down.
try:
    logfire.configure()
except Exception as exc:
    logging.getLogger("atlas.main").warning("logfire.configure() failed: %s", exc)
else:
    try:
        logfire.instrument_fastapi(app)
        # No client arg: patches anthropic.Anthropic/AsyncAnthropic globally, so
        # every agent's ChatAnthropic call gets a span with token usage/cost,
        # not just calls made through a client we instantiate ourselves.
        logfire.instrument_anthropic()
    except Exception as exc:
        logging.getLogger("atlas.main").warning("logfire instrumentation failed: %s", exc)

# The knowledge-graph agent needs the `neo4j` driver, which only lands in the
# image at build time. Mount it optionally so a repo pull + container restart
# without a rebuild degrades to "/cypher unavailable" instead of taking the
# whole service — and the four atlas agents — down with an ImportError.
_cypher_import_error: str | None = None
try:
    from cypher.router import router as cypher_router
except ImportError as exc:
    _cypher_import_error = str(exc)
    logging.getLogger("atlas.main").warning(
        "knowledge-graph agent not mounted: %s "
        "(rebuild the image so requirements.txt installs `neo4j`)",
        exc,
    )
else:
    app.include_router(cypher_router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {
        "status": "ok",
        "knowledge_graph_agent": "mounted" if _cypher_import_error is None else "unavailable",
        **({"knowledge_graph_error": _cypher_import_error} if _cypher_import_error else {}),
    }
