"""Static validation for model-authored Cypher.

Defence in depth. Neo4j's read transaction already rejects every write
(Neo.ClientError.Statement.AccessMode), so this layer exists for the things
access mode does NOT stop:
  - apoc.load.* / apoc.import.* / LOAD CSV fetch arbitrary URLs and files.
    Those are reads, so the server permits them: SSRF and local-file exfil.
  - dbms.* leaks config, and procedure/index admin noise.
It also rejects writes early to give the model a usable error instead of a
server stack trace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WRITE_CLAUSES = (
    "create", "merge", "delete", "detach", "set", "remove", "drop",
    "foreach", "load csv", "using periodic commit",
)

_BLOCKED_PREFIXES = (
    "apoc.load", "apoc.import", "apoc.export", "apoc.cypher", "apoc.periodic",
    "apoc.trigger", "apoc.refactor", "apoc.merge", "apoc.create", "apoc.atomic",
    "apoc.systemdb", "apoc.util.sleep", "apoc.couchbase", "apoc.mongo", "apoc.es",
    "apoc.bolt", "apoc.redis",
    "dbms.", "db.create", "db.drop", "db.index.fulltext.await",
)

_ALLOWED_CALL_PREFIXES = (
    "db.labels", "db.relationshiptypes", "db.propertykeys", "db.schema",
    "db.index.fulltext.querynodes", "db.index.fulltext.queryrelationships",
    "apoc.meta", "apoc.path", "apoc.coll", "apoc.text", "apoc.map", "apoc.agg",
    "apoc.number", "apoc.convert", "apoc.node", "apoc.rel", "apoc.label",
    "apoc.any", "apoc.data", "apoc.date", "apoc.temporal", "apoc.diff",
    "apoc.schema", "apoc.algo", "apoc.neighbors", "apoc.nodes", "apoc.version",
)

_STRIP_STRINGS = re.compile(
    r"'(?:[^'\\]|\\.)*'"      # single-quoted
    r"|\"(?:[^\"\\]|\\.)*\""  # double-quoted
    r"|`(?:[^`\\]|\\.)*`"     # backtick-quoted identifiers
)
_STRIP_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_CALL_TARGET = re.compile(r"\bcall\s+([a-z0-9_.]+)", re.IGNORECASE)
_LIMIT_PRESENT = re.compile(r"\blimit\s+\d+", re.IGNORECASE)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = ""
    has_limit: bool = False


def _scrub(cypher: str) -> str:
    """Strip comments and string/backtick literals so keyword scanning can't be
    tripped by a description containing the word "create"."""
    return _STRIP_STRINGS.sub(" ", _STRIP_COMMENTS.sub(" ", cypher)).lower().strip()


def validate(cypher: str) -> GuardResult:
    raw = (cypher or "").strip()
    if not raw:
        return GuardResult(False, "Empty query.")
    if len(raw) > 8000:
        return GuardResult(False, "Query too long (max 8000 chars).")

    scrubbed = _scrub(raw)

    # Neo4j rejects multi-statement itself, but catching it here gives a clearer error.
    if ";" in scrubbed.rstrip().rstrip(";"):
        return GuardResult(False, "Only a single statement is allowed (no ';').")

    for clause in _WRITE_CLAUSES:
        if re.search(rf"(?<![a-z0-9_]){re.escape(clause)}(?![a-z0-9_])", scrubbed):
            return GuardResult(
                False,
                f"Write/IO clause '{clause.upper()}' is not allowed — this tool is read-only.",
            )

    for match in _CALL_TARGET.finditer(scrubbed):
        target = match.group(1).rstrip(".")
        if any(target.startswith(p) for p in _BLOCKED_PREFIXES):
            return GuardResult(False, f"Procedure '{target}' is blocked.")
        if not any(target.startswith(p) for p in _ALLOWED_CALL_PREFIXES):
            return GuardResult(
                False,
                f"Procedure '{target}' is not on the read-only allowlist.",
            )

    if not re.match(r"^(match|optional\s+match|with|unwind|call|return|show|profile|explain)\b", scrubbed):
        return GuardResult(
            False,
            "Query must start with MATCH, OPTIONAL MATCH, WITH, UNWIND, CALL, RETURN, or SHOW.",
        )

    return GuardResult(True, has_limit=bool(_LIMIT_PRESENT.search(scrubbed)))
