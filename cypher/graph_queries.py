"""Curated read-only queries over the device-QA graph.

Kept separate from the tool wrappers so the Cypher is testable without an LLM.

Schema: (Company)-[:HAS_PRODUCT]->(ProductLine)-[:CONTAINS]->(Service)
        -[:HAS_FEATURE]->(Feature)-[:HAS_FLOW]->(Flow)
        (Flow)-[:REQUIRES_CONDITION|EXCLUDES_CONDITION]->(DeviceCondition)
        (Flow)-[:IS_DEPENDENT_ON]->(Flow)
"""

from __future__ import annotations

from typing import Any

from .graph_client import read, read_one

_FLOW_FIELDS = """
    fl.flow_number AS flow_number, fl.name AS name, fl.flow_type AS flow_type,
    fl.automated AS automated, fl.description AS description,
    fl.flow_skill_path AS flow_skill_path,
    fl.is_cloud_dependent AS is_cloud_dependent,
    fl.is_analytics_dependent AS is_analytics_dependent
"""


def schema_summary() -> dict[str, Any]:
    labels = read(
        """
        MATCH (n) UNWIND labels(n) AS label
        RETURN label, count(*) AS count ORDER BY count DESC
        """,
        max_rows=50,
    )
    rels = read(
        """
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(*) AS count ORDER BY count DESC
        """,
        max_rows=50,
    )
    props = read(
        """
        MATCH (n) UNWIND labels(n) AS label
        WITH label, keys(n) AS ks
        UNWIND ks AS k
        RETURN label, collect(DISTINCT k) AS properties
        ORDER BY label
        """,
        max_rows=50,
    )
    return {
        "node_labels": labels,
        "relationship_types": rels,
        "properties_by_label": props,
        "shape": (
            "(Company)-[:HAS_PRODUCT]->(ProductLine)-[:CONTAINS]->(Service)"
            "-[:HAS_FEATURE]->(Feature)-[:HAS_FLOW]->(Flow); "
            "(Flow)-[:REQUIRES_CONDITION|EXCLUDES_CONDITION]->(DeviceCondition); "
            "(Flow)-[:IS_DEPENDENT_ON]->(Flow)"
        ),
    }


def overview() -> dict[str, Any]:
    product_lines = read(
        """
        MATCH (pl:ProductLine)
        OPTIONAL MATCH (pl)-[:CONTAINS]->(s:Service)
        RETURN pl.name AS name, pl.sku AS sku, count(DISTINCT s) AS services
        ORDER BY name
        """,
        max_rows=50,
    )
    services = read(
        """
        MATCH (s:Service)
        OPTIONAL MATCH (s)-[:HAS_FEATURE]->(f:Feature)
        OPTIONAL MATCH (f)-[:HAS_FLOW]->(fl:Flow)
        RETURN s.name AS name,
               coalesce(s.log_path, s.log_path_to_refer) AS log_path,
               count(DISTINCT f) AS features, count(DISTINCT fl) AS flows
        ORDER BY flows DESC, name
        """,
        max_rows=50,
    )
    features = read(
        """
        MATCH (svc:Service)-[:HAS_FEATURE]->(f:Feature)
        OPTIONAL MATCH (f)-[:HAS_FLOW]->(fl:Flow)
        RETURN f.name AS name, svc.name AS service,
               f.valid_device_skus AS valid_device_skus,
               f.feature_default_status AS default_status,
               count(fl) AS flows
        ORDER BY flows DESC, name
        """,
        max_rows=100,
    )
    dep = read_one(
        """
        MATCH ()-[r:IS_DEPENDENT_ON]->() WITH count(r) AS total
        CALL {
            MATCH (a:Flow)-[:IS_DEPENDENT_ON]->(b:Flow)
            WHERE (b)-[:IS_DEPENDENT_ON]->(a) AND elementId(a) < elementId(b)
            RETURN count(*) AS symmetric_pairs
        }
        RETURN total, symmetric_pairs
        """
    ) or {}

    total = dep.get("total") or 0
    pairs = dep.get("symmetric_pairs") or 0
    return {
        "company": (read_one("MATCH (c:Company) RETURN c.name AS name") or {}).get("name"),
        "product_lines": product_lines,
        "services": services,
        "features": features,
        "is_dependent_on": {
            "edges": total,
            "symmetric_pairs": pairs,
            "caveat": (
                "Every IS_DEPENDENT_ON edge is mirrored (2 x symmetric_pairs == edges), "
                "so this relationship encodes 'related to', NOT execution order. "
                "Do not present it as a prerequisite or ordering constraint."
            ) if total and pairs * 2 == total else None,
        },
    }


def search(text: str, limit: int = 25) -> list[dict[str, Any]]:
    return read(
        """
        WITH toLower($text) AS q
        MATCH (n)
        WHERE (n:Service OR n:Feature OR n:Flow OR n:DeviceCondition OR n:ProductLine)
          AND (toLower(coalesce(n.name, '')) CONTAINS q
               OR toLower(coalesce(n.description, '')) CONTAINS q
               OR toLower(coalesce(n.display_name, '')) CONTAINS q)
        RETURN labels(n)[0] AS label, n.name AS name,
               left(coalesce(n.description, n.display_name, ''), 240) AS snippet,
               n.flow_number AS flow_number
        ORDER BY label, name
        """,
        {"text": text},
        max_rows=limit,
    )


def feature_detail(name: str) -> dict[str, Any] | None:
    row = read_one(
        """
        WITH toLower($name) AS q
        MATCH (svc:Service)-[:HAS_FEATURE]->(f:Feature)
        WHERE toLower(f.name) = q OR toLower(f.name) CONTAINS q
        OPTIONAL MATCH (pl:ProductLine)-[:CONTAINS]->(svc)
        RETURN f.name AS name, properties(f) AS properties,
               svc.name AS service,
               coalesce(svc.log_path, svc.log_path_to_refer) AS service_log_path,
               collect(DISTINCT pl.name + ' (' + coalesce(pl.sku, '?') + ')') AS product_lines
        """,
        {"name": name},
    )
    if row is None:
        return None
    row["flows"] = read(
        f"""
        WITH toLower($name) AS q
        MATCH (f:Feature)-[:HAS_FLOW]->(fl:Flow)
        WHERE toLower(f.name) = q OR toLower(f.name) CONTAINS q
        RETURN {_FLOW_FIELDS}
        ORDER BY flow_number
        """,
        {"name": name},
        max_rows=200,
    )
    return row


def list_flows(
    feature: str | None = None,
    flow_type: str | None = None,
    automated: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return read(
        f"""
        MATCH (f:Feature)-[:HAS_FLOW]->(fl:Flow)
        WHERE ($feature IS NULL OR toLower(f.name) CONTAINS toLower($feature))
          AND ($flow_type IS NULL OR toLower(fl.flow_type) = toLower($flow_type))
          AND ($automated IS NULL OR fl.automated = $automated)
        RETURN f.name AS feature, {_FLOW_FIELDS}
        ORDER BY feature, flow_number
        """,
        {"feature": feature, "flow_type": flow_type, "automated": automated},
        max_rows=limit,
    )


def flow_detail(name: str | None = None, flow_number: int | None = None) -> dict[str, Any] | None:
    row = read_one(
        f"""
        MATCH (f:Feature)-[:HAS_FLOW]->(fl:Flow)
        WHERE ($flow_number IS NOT NULL AND fl.flow_number = $flow_number)
           OR ($name IS NOT NULL AND (toLower(fl.name) = toLower($name)
                                      OR toLower(fl.name) CONTAINS toLower($name)))
        RETURN {_FLOW_FIELDS}, f.name AS feature, f.valid_device_skus AS feature_skus,
               f.OTA_valid_from AS feature_ota_valid_from
        ORDER BY fl.flow_number
        """,
        {"name": name, "flow_number": flow_number},
    )
    if row is None:
        return None

    # Keyed on name, not flow_number: only `name` carries a uniqueness constraint,
    # and flow numbering restarts per feature.
    key = {"flow": row["name"]}
    row["requires_conditions"] = read(
        """
        MATCH (fl:Flow {name: $flow})-[r:REQUIRES_CONDITION]->(d:DeviceCondition)
        RETURN d.name AS condition, d.display_name AS display_name,
               d.category AS category, d.value AS value, r.evidence AS evidence
        ORDER BY category, condition
        """,
        key,
        max_rows=50,
    )
    row["excludes_conditions"] = read(
        """
        MATCH (fl:Flow {name: $flow})-[r:EXCLUDES_CONDITION]->(d:DeviceCondition)
        RETURN d.name AS condition, d.display_name AS display_name,
               d.category AS category, d.value AS value, r.evidence AS evidence
        ORDER BY category, condition
        """,
        key,
        max_rows=50,
    )
    row["related_flows"] = read(
        """
        MATCH (fl:Flow {name: $flow})-[:IS_DEPENDENT_ON]-(other:Flow)
        RETURN DISTINCT other.flow_number AS flow_number, other.name AS name
        ORDER BY flow_number
        """,
        key,
        max_rows=100,
    )
    row["related_flows_caveat"] = (
        "IS_DEPENDENT_ON is symmetric in this graph — these are related flows, "
        "not prerequisites or an execution order."
    )
    return row


def applicable_flows(sku: str, limit: int = 200) -> dict[str, Any]:
    """Flows for a device SKU or product-line name.

    OTA_valid_from is returned verbatim: it is a free-form string (sometimes a
    JSON map, sometimes "ALL", sometimes prose), so version gating is left to
    the caller rather than guessed at in Cypher.
    """
    line = read_one(
        """
        WITH toLower($sku) AS q
        MATCH (pl:ProductLine)
        WHERE toLower(coalesce(pl.sku, '')) = q OR toLower(pl.name) = q
        RETURN pl.name AS name, pl.sku AS sku
        """,
        {"sku": sku},
    )
    if line is None:
        known = read(
            "MATCH (pl:ProductLine) RETURN pl.name AS name, pl.sku AS sku ORDER BY name",
            max_rows=50,
        )
        return {"error": f"Unknown SKU or product line {sku!r}", "known_product_lines": known}

    flows = read(
        f"""
        WITH toLower($sku) AS q
        MATCH (pl:ProductLine)-[:CONTAINS]->(svc:Service)-[:HAS_FEATURE]->(f:Feature)
        WHERE toLower(coalesce(pl.sku, '')) = q OR toLower(pl.name) = q
        WITH pl, svc, f,
             [x IN coalesce(f.valid_device_skus, []) WHERE toLower(x) IN
                 [toLower(coalesce(pl.sku, '')), toLower(pl.name)]] AS sku_match
        WHERE size(sku_match) > 0
        MATCH (f)-[:HAS_FLOW]->(fl:Flow)
        RETURN svc.name AS service, f.name AS feature,
               f.OTA_valid_from AS feature_ota_valid_from,
               {_FLOW_FIELDS}
        ORDER BY service, feature, flow_number
        """,
        {"sku": sku},
        max_rows=limit,
    )

    excluded = read(
        """
        WITH toLower($sku) AS q
        MATCH (pl:ProductLine)-[:CONTAINS]->(svc:Service)-[:HAS_FEATURE]->(f:Feature)
        WHERE (toLower(coalesce(pl.sku, '')) = q OR toLower(pl.name) = q)
          AND NOT any(x IN coalesce(f.valid_device_skus, []) WHERE toLower(x) IN
                      [toLower(coalesce(pl.sku, '')), toLower(pl.name)])
        RETURN svc.name AS service, f.name AS feature,
               f.valid_device_skus AS valid_device_skus
        ORDER BY service, feature
        """,
        {"sku": sku},
        max_rows=100,
    )

    return {
        "product_line": line,
        "applicable_flows": flows,
        "features_not_applicable": excluded,
        "note": (
            "SKU applicability comes from Feature.valid_device_skus, which mixes SKU codes "
            "(D450) and product-line names (bagheera3); both spellings are matched. "
            "OTA_valid_from is free-form and returned verbatim — no version filtering applied."
        ),
    }


def list_conditions(category: str | None = None) -> list[dict[str, Any]]:
    return read(
        """
        MATCH (d:DeviceCondition)
        WHERE $category IS NULL OR toLower(d.category) = toLower($category)
        OPTIONAL MATCH (d)<-[:REQUIRES_CONDITION]-(rq:Flow)
        OPTIONAL MATCH (d)<-[:EXCLUDES_CONDITION]-(ex:Flow)
        RETURN d.name AS name, d.display_name AS display_name, d.category AS category,
               d.value AS value,
               count(DISTINCT rq) AS required_by_flows,
               count(DISTINCT ex) AS excluded_by_flows
        ORDER BY category, name
        """,
        {"category": category},
        max_rows=200,
    )


def flows_for_condition(condition: str, limit: int = 100) -> list[dict[str, Any]]:
    return read(
        f"""
        WITH toLower($condition) AS q
        MATCH (fl:Flow)-[r:REQUIRES_CONDITION|EXCLUDES_CONDITION]->(d:DeviceCondition)
        WHERE toLower(d.name) = q OR toLower(d.name) CONTAINS q
           OR toLower(coalesce(d.display_name, '')) CONTAINS q
        MATCH (f:Feature)-[:HAS_FLOW]->(fl)
        RETURN d.name AS condition, type(r) AS relationship, r.evidence AS evidence,
               f.name AS feature, {_FLOW_FIELDS}
        ORDER BY relationship, feature, flow_number
        """,
        {"condition": condition},
        max_rows=limit,
    )


def coverage_gaps() -> dict[str, Any]:
    return {
        "features_with_no_flows": read(
            """
            MATCH (svc:Service)-[:HAS_FEATURE]->(f:Feature)
            WHERE NOT (f)-[:HAS_FLOW]->(:Flow)
            RETURN svc.name AS service, f.name AS feature,
                   f.valid_device_skus AS valid_device_skus
            ORDER BY service, feature
            """,
            max_rows=100,
        ),
        "flows_not_automated": read(
            """
            MATCH (f:Feature)-[:HAS_FLOW]->(fl:Flow)
            WHERE fl.automated = 0
            RETURN f.name AS feature, fl.flow_number AS flow_number, fl.name AS name
            ORDER BY feature, flow_number
            """,
            max_rows=200,
        ),
        # Kept separate from flows_not_automated: an unset property means unknown,
        # not "manual", and reporting it as manual would overstate the gap.
        "flows_missing_properties": read(
            """
            MATCH (f:Feature)-[:HAS_FLOW]->(fl:Flow)
            WHERE fl.flow_type IS NULL OR fl.automated IS NULL
               OR fl.description IS NULL OR fl.flow_skill_path IS NULL
            RETURN f.name AS feature, fl.flow_number AS flow_number, fl.name AS name,
                   [k IN ['flow_type', 'automated', 'description', 'flow_skill_path']
                    WHERE fl[k] IS NULL] AS missing
            ORDER BY feature, flow_number
            """,
            max_rows=200,
        ),
        "flows_without_conditions": read(
            """
            MATCH (f:Feature)-[:HAS_FLOW]->(fl:Flow)
            WHERE NOT (fl)-[:REQUIRES_CONDITION|EXCLUDES_CONDITION]->(:DeviceCondition)
            RETURN f.name AS feature, fl.flow_number AS flow_number, fl.name AS name
            ORDER BY feature, flow_number
            """,
            max_rows=200,
        ),
        "unused_conditions": read(
            """
            MATCH (d:DeviceCondition)
            WHERE NOT (d)<-[:REQUIRES_CONDITION|EXCLUDES_CONDITION]-(:Flow)
            RETURN d.name AS name, d.category AS category
            ORDER BY category, name
            """,
            max_rows=100,
        ),
        "services_with_no_features": read(
            """
            MATCH (s:Service)
            WHERE NOT (s)-[:HAS_FEATURE]->(:Feature)
            RETURN s.name AS service ORDER BY service
            """,
            max_rows=50,
        ),
    }


def all_skill_paths() -> list[dict[str, Any]]:
    return read(
        """
        MATCH (f:Feature)-[:HAS_FLOW]->(fl:Flow)
        RETURN 'Flow' AS kind, fl.name AS name, fl.flow_number AS flow_number,
               fl.flow_skill_path AS stored_path, f.name AS parent
        ORDER BY flow_number
        UNION
        MATCH (svc:Service)-[:HAS_FEATURE]->(f:Feature)
        UNWIND coalesce(f.skill_path, []) AS stored_path
        RETURN 'Feature' AS kind, f.name AS name, null AS flow_number,
               stored_path, svc.name AS parent
        """,
        max_rows=500,
    )
