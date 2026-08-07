#!/usr/bin/env python3
"""Check Neo4j connectivity, version, and node/relationship counts.

    python -m cypher.scripts.check_health
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cypher import config  # noqa: E402
from cypher.graph_client import GraphUnavailable, QueryRejected, read_one  # noqa: E402


def main() -> int:
    try:
        settings = config.neo4j_settings()
    except EnvironmentError as exc:
        print(f"misconfigured: {exc}")
        return 1

    try:
        info = read_one(
            "CALL dbms.components() YIELD name, versions, edition "
            "RETURN versions[0] AS version, edition AS edition"
        )
        counts = read_one(
            "MATCH (n) WITH count(n) AS nodes "
            "CALL { MATCH ()-[r]->() RETURN count(r) AS rels } "
            "RETURN nodes, rels"
        )
    except (GraphUnavailable, QueryRejected) as exc:
        print(f"unreachable: {settings['uri']} — {exc}")
        return 1

    print(f"ok: {settings['uri']} (db={settings['database']})")
    print(f"  neo4j {info.get('version')} {info.get('edition')}")
    print(f"  {counts.get('nodes')} nodes, {counts.get('rels')} relationships")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
