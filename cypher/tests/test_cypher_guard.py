"""Guard tests. These are the security boundary for model-authored Cypher."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cypher.cypher_guard import validate  # noqa: E402

ALLOWED = [
    "MATCH (n:Flow) RETURN n.name LIMIT 10",
    "match (f:Feature)-[:HAS_FLOW]->(fl) return count(fl) as c",
    "OPTIONAL MATCH (n:Service) RETURN n LIMIT 5",
    "WITH 1 AS x RETURN x",
    "UNWIND [1,2,3] AS n RETURN n",
    "PROFILE MATCH (n) RETURN count(n)",
    "EXPLAIN MATCH (n) RETURN n LIMIT 1",
    "SHOW CONSTRAINTS",
    "CALL db.labels() YIELD label RETURN label",
    "CALL apoc.meta.stats() YIELD labelCount RETURN labelCount",
    "MATCH (n) RETURN n LIMIT 1;",  # single trailing semicolon tolerated
]

BLOCKED = [
    "CREATE (n:Evil) RETURN n",
    "MATCH (n:Flow) SET n.x = 1 RETURN n",
    "MATCH (n) DETACH DELETE n",
    "MERGE (n:Thing {a:1}) RETURN n",
    "MATCH (n) REMOVE n.name RETURN n",
    "DROP CONSTRAINT flow_name_unique",
    "LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row",
    "MATCH (n) FOREACH (x IN [1] | SET n.y = x)",
    "MATCH (n) RETURN n LIMIT 1; MATCH (m) RETURN m",
    # apoc.load.* is a READ op, so Neo4j access mode does NOT block it — SSRF/exfil.
    "CALL apoc.load.json('http://169.254.169.254/latest/meta-data/') YIELD value RETURN value",
    "CALL apoc.load.csv('file:///etc/passwd') YIELD list RETURN list",
    "CALL apoc.cypher.runWrite('CREATE (n:X)', {}) YIELD value RETURN value",
    "CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DELETE n', {}) YIELD batches RETURN batches",
    "CALL dbms.listConfig() YIELD name RETURN name",
    "CALL apoc.export.csv.all('out.csv', {}) YIELD file RETURN file",
    "",
    "   ",
    "DELETE FROM flows",
]


@pytest.mark.parametrize("query", ALLOWED)
def test_allows_reads(query):
    verdict = validate(query)
    assert verdict.ok, f"should allow: {query!r} — got {verdict.reason}"


@pytest.mark.parametrize("query", BLOCKED)
def test_blocks_writes_and_io(query):
    assert not validate(query).ok, f"should block: {query!r}"


def test_keywords_inside_string_literals_are_not_writes():
    # A description containing "create"/"delete" must not trip the scanner.
    query = (
        "MATCH (f:Flow) WHERE f.description CONTAINS 'create' "
        "OR f.description CONTAINS 'delete the file' RETURN f.name LIMIT 5"
    )
    assert validate(query).ok


def test_keywords_inside_comments_are_ignored():
    assert validate("MATCH (n) RETURN n LIMIT 1 // CREATE something").ok
    assert validate("/* DELETE all */ MATCH (n) RETURN n LIMIT 1").ok


def test_backtick_identifier_with_keyword():
    assert validate("MATCH (n) RETURN n.`create date` LIMIT 1").ok


def test_limit_detection():
    assert validate("MATCH (n) RETURN n LIMIT 10").has_limit
    assert not validate("MATCH (n) RETURN n").has_limit


def test_overlong_query_rejected():
    assert not validate("MATCH (n) RETURN n " + "// pad" * 3000).ok


def test_must_start_with_read_clause():
    assert not validate("RETURNS n").ok
    assert not validate("GRANT ROLE admin TO user").ok
