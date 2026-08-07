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

import logfire
from fastapi import FastAPI

from .router import router as atlas_router

app = FastAPI(title="Atlas Device Agent", version="1.0.0")

app.include_router(atlas_router)

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
