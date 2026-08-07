#!/usr/bin/env python3
"""Create fulltext indexes over the graph's description fields.

Optional. `search_graph` works without them via CONTAINS, but that is a full scan
and cannot rank or handle word stemming. At 106 nodes it makes no measurable
difference; add these once the graph grows past a few thousand nodes.

    python -m cypher.scripts.create_fulltext_indexes            # dry run
    python -m cypher.scripts.create_fulltext_indexes --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cypher import config  # noqa: E402
from cypher.graph_client import get_driver, read  # noqa: E402

INDEXES = [
    ("kg_flow_text", "Flow", ["name", "description"]),
    ("kg_feature_text", "Feature", ["name", "description"]),
    ("kg_service_text", "Service", ["name", "description"]),
    ("kg_condition_text", "DeviceCondition", ["name", "display_name"]),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    existing = {row["name"] for row in read("SHOW INDEXES YIELD name RETURN name", max_rows=200)}

    planned = []
    for name, label, props in INDEXES:
        if name in existing:
            print(f"  exists   {name}")
            continue
        fields = ", ".join(f"n.{p}" for p in props)
        planned.append(
            f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (n:{label}) ON EACH [{fields}]"
        )
        print(f"  create   {name}  ({label}: {', '.join(props)})")

    if not planned:
        print("\nAll indexes already present.")
        return 0
    if not args.apply:
        print(f"\nDRY RUN — {len(planned)} index(es) pending. Re-run with --apply.")
        return 0

    settings = config.neo4j_settings()
    with get_driver().session(database=settings["database"]) as session:
        for statement in planned:
            session.run(statement).consume()
    print(f"\nCreated {len(planned)} index(es).")
    print("Query them with: CALL db.index.fulltext.queryNodes('kg_flow_text', $q)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
