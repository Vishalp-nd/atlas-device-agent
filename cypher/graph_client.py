"""Neo4j access. Every query runs in a READ transaction, which the server
enforces by rejecting writes with Neo.ClientError.Statement.AccessMode."""

from __future__ import annotations

import threading
from typing import Any

import neo4j
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from neo4j.time import Date, DateTime, Duration, Time

from . import config
from .logging_setup import get_logger

log = get_logger()

_driver: neo4j.Driver | None = None
_driver_lock = threading.Lock()


class GraphUnavailable(RuntimeError):
    pass


class QueryRejected(ValueError):
    pass


def get_driver() -> neo4j.Driver:
    global _driver
    with _driver_lock:
        if _driver is None:
            settings = config.neo4j_settings()
            _driver = GraphDatabase.driver(
                settings["uri"],
                auth=(settings["username"], settings["password"]),
                max_connection_lifetime=1800,
                max_connection_pool_size=20,
                connection_acquisition_timeout=30,
            )
            log.info("neo4j driver created uri=%s db=%s", settings["uri"], settings["database"])
        return _driver


def close_driver() -> None:
    global _driver
    with _driver_lock:
        if _driver is not None:
            _driver.close()
            _driver = None


def _jsonable(value: Any) -> Any:
    if isinstance(value, (DateTime, Date, Time, Duration)):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, neo4j.graph.Node):
        return {"_labels": sorted(value.labels), **{k: _jsonable(v) for k, v in value.items()}}
    if isinstance(value, neo4j.graph.Relationship):
        return {"_type": value.type, **{k: _jsonable(v) for k, v in value.items()}}
    if isinstance(value, neo4j.graph.Path):
        return {"_path_length": len(value)}
    return value


def read(
    cypher: str,
    params: dict[str, Any] | None = None,
    *,
    max_rows: int | None = None,
) -> list[dict[str, Any]]:
    """Run `cypher` in a read transaction and return at most max_rows records."""
    cap = max_rows if max_rows is not None else config.default_max_rows()
    cap = max(1, min(cap, config.MAX_ROWS_HARD_CAP))
    settings = config.neo4j_settings()

    try:
        driver = get_driver()
        with driver.session(
            database=settings["database"], default_access_mode=neo4j.READ_ACCESS
        ) as session:
            with session.begin_transaction(timeout=config.query_timeout_seconds()) as tx:
                result = tx.run(cypher, params or {})
                rows = [_jsonable(record.data()) for record in result.fetch(cap)]
    except Neo4jError as exc:
        code = getattr(exc, "code", "") or ""
        if "AccessMode" in code:
            raise QueryRejected(
                "Query attempted a write. This agent is read-only."
            ) from exc
        if "SyntaxError" in code:
            raise QueryRejected(f"Cypher syntax error: {exc.message}") from exc
        log.error("neo4j error code=%s msg=%s", code, exc)
        raise GraphUnavailable(f"Neo4j error ({code}): {exc.message}") from exc
    except Exception as exc:
        log.error("neo4j connection failure: %s", exc)
        raise GraphUnavailable(f"Cannot reach Neo4j: {exc}") from exc

    return rows


def read_one(cypher: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = read(cypher, params, max_rows=1)
    return rows[0] if rows else None


