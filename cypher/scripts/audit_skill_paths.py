#!/usr/bin/env python3
"""Report how every skill path stored in the graph resolves against disk.

Read-only, no side effects — the diagnostic that drove repair_skill_paths.py.
Run this whenever you want to know if the graph and the skill tree have
drifted again, without exposing it as a permanent HTTP endpoint.

    python -m cypher.scripts.audit_skill_paths
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cypher import graph_queries as gq, skill_resolver  # noqa: E402
from cypher.graph_client import GraphUnavailable, QueryRejected  # noqa: E402


def main() -> int:
    try:
        stored = gq.all_skill_paths()
    except (GraphUnavailable, QueryRejected) as exc:
        print(f"Cannot reach the graph: {exc}")
        return 1

    by_status: dict[str, int] = {}
    for row in stored:
        kind = "flow" if row["kind"] == "Flow" else "feature"
        resolved = skill_resolver.resolve(
            row.get("stored_path"), kind=kind, name=row.get("name") or ""
        )
        by_status[resolved.status] = by_status.get(resolved.status, 0) + 1
        if resolved.status != "exact":
            label = f"Flow {row['flow_number']}" if row["kind"] == "Flow" else row["kind"]
            print(f"  {resolved.status:9} {label:10} {row['name']:35} {row.get('stored_path')}")
            if resolved.note:
                print(f"            {'':10} {'':35} note: {resolved.note}")

    print(f"\n{len(stored)} total. {by_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
