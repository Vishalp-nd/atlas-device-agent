"""LangChain tools over the knowledge graph.

Curated tools cover the known question shapes; run_cypher is the escape hatch
for anything they don't, guarded by cypher_guard plus a Neo4j read transaction.
"""

from __future__ import annotations

import json
from typing import Any

import logfire
from langchain_core.tools import tool

from . import config, cypher_guard, graph_queries as gq, skill_resolver
from .graph_client import GraphUnavailable, QueryRejected, read
from .logging_setup import get_logger

log = get_logger()


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False)


def _guarded(name: str, fn, *args, **kwargs) -> str:
    log.info("[tool:%s] args=%s kwargs=%s", name, args, kwargs)
    with logfire.span(f"tool.{name}", tool_args=args, tool_kwargs=kwargs) as span:
        try:
            result = fn(*args, **kwargs)
        except (GraphUnavailable, QueryRejected) as exc:
            log.warning("[tool:%s] %s", name, exc)
            span.set_attribute("error", str(exc))
            return f"{name} failed: {exc}"
        except Exception as exc:
            log.error("[tool:%s] unexpected: %s", name, exc, exc_info=True)
            span.set_attribute("error", str(exc))
            return f"{name} failed: {exc}"
        if result is None or result == [] or result == {}:
            span.set_attribute("empty_result", True)
            return f"{name}: no matching data in the graph."
        return _dump(result)


@tool
def graph_overview() -> str:
    """Orientation snapshot of the knowledge graph: the company, all product lines
    with SKUs, every service with its feature/flow counts, every feature with its
    applicable SKUs and flow count, and a caveat about the IS_DEPENDENT_ON edges.

    Call this first when you do not yet know what exists in the graph.
    """
    return _guarded("graph_overview", gq.overview)


@tool
def graph_schema() -> str:
    """Node labels with counts, relationship types with counts, the property keys
    present on each label, and the overall graph shape. Use before writing Cypher
    with run_cypher.
    """
    return _guarded("graph_schema", gq.schema_summary)


@tool
def search_graph(text: str) -> str:
    """Case-insensitive substring search over name, description, and display_name
    across ProductLine, Service, Feature, Flow, and DeviceCondition nodes.

    Use this to locate the right node when the user's wording does not match a
    name exactly (e.g. "drowsiness alert", "privacy blackout", "SD card").
    """
    return _guarded("search_graph", gq.search, text)


@tool
def feature_detail(feature_name: str) -> str:
    """Everything about one feature: all properties (applicable SKUs, default
    status, cloud/analytics dependency, validation method, OTA_valid_from, stored
    skill path), its parent service and log path, the product lines it ships on,
    and the full list of its flows.
    """
    return _guarded("feature_detail", gq.feature_detail, feature_name)


@tool
def list_flows(
    feature: str | None = None,
    flow_type: str | None = None,
    automated: int | None = None,
) -> str:
    """List flows, optionally filtered.

    feature: substring of a feature name (e.g. "haptic").
    flow_type: "positive" or "negative".
    automated: 1 for automated flows, 0 for not-yet-automated.
    """
    return _guarded("list_flows", gq.list_flows, feature, flow_type, automated)


@tool
def flow_detail(name: str | None = None, flow_number: int | None = None) -> str:
    """Full detail for one test flow: its properties, the parent feature and that
    feature's SKU/OTA gating, every REQUIRES_CONDITION and EXCLUDES_CONDITION with
    the evidence string, and related flows.

    Identify the flow by name (substring is fine) or by flow_number.
    """
    return _guarded("flow_detail", gq.flow_detail, name, flow_number)


@tool
def applicable_flows(sku: str) -> str:
    """Which flows apply to a device, given a SKU ("D450") or product-line name
    ("bagheera3"). Also returns the features that exist on that product line but
    are NOT valid for it, and each feature's raw OTA_valid_from string.

    OTA_valid_from is free-form text and is not filtered — read it and reason
    about version applicability yourself, stating what you assumed.
    """
    return _guarded("applicable_flows", gq.applicable_flows, sku)


@tool
def list_conditions(category: str | None = None) -> str:
    """Device conditions that gate flows, with how many flows require or exclude
    each. Optionally filter by category (service_config, ignition, power_mode,
    reboot_scenario, privacy, health_monitoring, ...).
    """
    return _guarded("list_conditions", gq.list_conditions, category)


@tool
def flows_for_condition(condition: str) -> str:
    """Every flow that requires or excludes a given device condition, with the
    evidence string. Use for "which tests need privacy mode active?" style asks.
    """
    return _guarded("flows_for_condition", gq.flows_for_condition, condition)


@tool
def coverage_gaps() -> str:
    """Where the graph is thin: features with no flows, flows not yet automated,
    flows with no gating conditions, unused device conditions, and services with
    no features. Use for "what is missing / what should we build next".
    """
    return _guarded("coverage_gaps", gq.coverage_gaps)


@tool
def read_skill(
    stored_path: str | None = None,
    flow_name: str | None = None,
    feature_name: str | None = None,
) -> str:
    """Read the SKILL.md validation document behind a flow or feature — pass
    criteria, fail signals, log evidence, test-case IDs, validation steps.

    Pass a flow_name or feature_name (preferred: the path is looked up in the
    graph and resolved tolerantly), or a stored_path directly. Stored paths in
    the graph have drifted from disk, so the reply reports how the file was
    matched; if that status is "fuzzy", say so rather than asserting the content
    definitely belongs to the flow asked about.
    """
    with logfire.span(
        "tool.read_skill", flow_name=flow_name, feature_name=feature_name, stored_path=stored_path
    ) as span:
        kind = "flow" if flow_name else ("feature" if feature_name else None)
        name = flow_name or feature_name or ""
        path = stored_path

        try:
            if flow_name and not path:
                row = gq.flow_detail(name=flow_name)
                if row is None:
                    span.set_attribute("error", "flow not found")
                    return f"read_skill: no flow matching {flow_name!r}."
                path, name = row.get("flow_skill_path"), row.get("name") or flow_name
            elif feature_name and not path:
                row = gq.feature_detail(feature_name)
                if row is None:
                    span.set_attribute("error", "feature not found")
                    return f"read_skill: no feature matching {feature_name!r}."
                stored = (row.get("properties") or {}).get("skill_path") or []
                if isinstance(stored, str):
                    stored = [stored]
                path, name = (stored[0] if stored else None), row.get("name") or feature_name
        except (GraphUnavailable, QueryRejected) as exc:
            span.set_attribute("error", str(exc))
            return f"read_skill failed: {exc}"

        resolved = skill_resolver.resolve(path, kind=kind, name=name)
        span.set_attribute("match_status", resolved.status)
        span.set_attribute("resolved_path", resolved.relative)
        log.info("[tool:read_skill] name=%s stored=%s -> %s (%s)",
                 name, path, resolved.relative, resolved.status)

        if not resolved.found:
            return _dump({
                "status": resolved.status,
                "stored_path": path,
                "note": resolved.note,
                "similar_directories": resolved.candidates,
            })

        header = {
            "resolved_path": resolved.relative,
            "match_status": resolved.status,
            "stored_path": path,
        }
        if resolved.note:
            header["note"] = resolved.note
        return _dump(header) + "\n\n---\n\n" + skill_resolver.read_skill(resolved)


@tool
def find_skill_files(query: str) -> str:
    """Find SKILL.md files on disk by substring of their path. Use when a skill
    document is expected but read_skill could not resolve it.
    """
    with logfire.span("tool.find_skill_files", query=query) as span:
        hits = skill_resolver.find_by_query(query)
        span.set_attribute("hits", len(hits))
        return _dump(hits) if hits else f"No SKILL.md path matching {query!r}."


@tool
def run_cypher(query: str, max_rows: int = 100) -> str:
    """Run a single read-only Cypher query for questions the other tools cannot answer.

    Read-only: no CREATE/MERGE/SET/DELETE/REMOVE/DROP/FOREACH/LOAD CSV, one
    statement only, and procedure calls are restricted to a read allowlist.
    Call graph_schema first so labels and property names are correct.
    Always include a LIMIT.
    """
    with logfire.span("tool.run_cypher", cypher=query, max_rows=max_rows) as span:
        verdict = cypher_guard.validate(query)
        if not verdict.ok:
            log.warning("[tool:run_cypher] rejected: %s | %s", verdict.reason, query)
            span.set_attribute("rejected", verdict.reason)
            return f"Rejected: {verdict.reason}"

        cap = max(1, min(max_rows, config.MAX_ROWS_HARD_CAP))
        log.info("[tool:run_cypher] max_rows=%d query=%s", cap, query)
        try:
            rows = read(query, max_rows=cap)
        except (GraphUnavailable, QueryRejected) as exc:
            span.set_attribute("error", str(exc))
            return f"run_cypher failed: {exc}"

        span.set_attribute("returned_rows", len(rows))
        payload: dict[str, Any] = {"returned_rows": len(rows), "rows": rows}
        if len(rows) == cap and not verdict.has_limit:
            payload["warning"] = (
                f"Hit the {cap}-row cap and the query has no LIMIT — results may be truncated."
            )
        return _dump(payload)


ALL_TOOLS = [
    graph_overview,
    graph_schema,
    search_graph,
    feature_detail,
    list_flows,
    flow_detail,
    applicable_flows,
    list_conditions,
    flows_for_condition,
    coverage_gaps,
    read_skill,
    find_skill_files,
    run_cypher,
]
