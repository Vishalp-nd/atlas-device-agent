#!/usr/bin/env python3
"""Repair the skill paths stored in the knowledge graph.

Dry-run by default. Nothing is written without --apply.

    python -m cypher.scripts.repair_skill_paths            # show the diff
    python -m cypher.scripts.repair_skill_paths --apply    # write it

Flow paths are matched by the "Flow N:" number in each flow SKILL.md heading
against Flow.flow_number — an exact key, unlike slug similarity. Each match is
cross-checked against the flow name and anything that disagrees is reported
rather than written silently.

Deliberately NOT touched: the 438 symmetric IS_DEPENDENT_ON edges. Turning
"related to" into a real execution order needs domain knowledge this script
does not have; inventing a direction would be worse than leaving it labelled
honestly. See the summary this prints for the recommendation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cypher import config  # noqa: E402
from cypher.graph_client import get_driver, read  # noqa: E402

HAPTIC_FLOWS = REPO_ROOT / ".github/skills/haptic-service-validation/flows"
HEADING = re.compile(r"^#\s+.*?Flow\s+(\d+)\s*:\s*(.+?)\s*$", re.MULTILINE)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def index_flow_skills() -> tuple[dict[int, tuple[Path, str]], list[str]]:
    by_number: dict[int, tuple[Path, str]] = {}
    problems: list[str] = []
    if not HAPTIC_FLOWS.is_dir():
        problems.append(f"flow skill directory not found: {HAPTIC_FLOWS}")
        return by_number, problems
    for md in sorted(HAPTIC_FLOWS.glob("*/SKILL.md")):
        match = HEADING.search(md.read_text(encoding="utf-8", errors="replace"))
        if match is None:
            problems.append(f"no 'Flow N:' heading, skipped: {md.relative_to(REPO_ROOT)}")
            continue
        number, title = int(match.group(1)), match.group(2)
        if number in by_number:
            problems.append(
                f"duplicate Flow {number}: {by_number[number][0].parent.name} "
                f"and {md.parent.name}"
            )
            continue
        by_number[number] = (md, title)
    return by_number, problems


def plan_flow_changes(by_number: dict[int, tuple[Path, str]]) -> tuple[list[dict], list[dict]]:
    changes: list[dict] = []
    skipped: list[dict] = []
    rows = read(
        "MATCH (f:Flow) RETURN f.name AS name, f.flow_number AS flow_number, "
        "f.flow_skill_path AS stored ORDER BY f.flow_number",
        max_rows=1000,
    )
    for row in rows:
        number, name, stored = row["flow_number"], row["name"], row["stored"]
        hit = by_number.get(number)
        if hit is None:
            skipped.append({"flow": name, "flow_number": number,
                            "reason": "no flow SKILL.md with this Flow number"})
            continue
        md, title = hit
        correct = md.relative_to(REPO_ROOT).as_posix()
        if stored == correct:
            continue
        entry = {
            "flow": name,
            "flow_number": number,
            "old": stored,
            "new": correct,
            "old_existed": bool(stored) and (REPO_ROOT / stored.lstrip("/")).is_file(),
        }
        if _norm(title) != _norm(name):
            entry["name_drift"] = f"graph {name!r} vs heading {title!r}"
        changes.append(entry)
    return changes, skipped


def plan_feature_changes() -> tuple[list[dict], list[dict]]:
    changes: list[dict] = []
    no_target: list[dict] = []
    skills_root = REPO_ROOT / ".github/skills"
    rows = read(
        "MATCH (f:Feature) RETURN f.name AS name, f.skill_path AS stored ORDER BY f.name",
        max_rows=1000,
    )
    for row in rows:
        stored = row["stored"] or []
        if isinstance(stored, str):
            stored = [stored]
        repaired: list[str] = []
        dirty = False
        for path in stored:
            if (REPO_ROOT / path.lstrip("/")).is_file():
                repaired.append(path)
                continue
            candidate = skills_root / Path(path.rstrip("/")).parent.name / "SKILL.md"
            if candidate.is_file():
                repaired.append(candidate.relative_to(REPO_ROOT).as_posix())
                dirty = True
            else:
                repaired.append(path)
                no_target.append({
                    "feature": row["name"],
                    "stored_path": path,
                    "reason": f"no '{Path(path.rstrip('/')).parent.name}/' on disk",
                })
        if dirty:
            changes.append({"feature": row["name"], "old": stored, "new": repaired})
    return changes, no_target


def apply_changes(flow_changes: list[dict], feature_changes: list[dict]) -> int:
    settings = config.neo4j_settings()
    written = 0
    with get_driver().session(database=settings["database"]) as session:
        for change in flow_changes:
            session.run(
                "MATCH (f:Flow {name: $name}) SET f.flow_skill_path = $path",
                name=change["flow"], path=change["new"],
            ).consume()
            written += 1
        for change in feature_changes:
            session.run(
                "MATCH (f:Feature {name: $name}) SET f.skill_path = $paths",
                name=change["feature"], paths=change["new"],
            ).consume()
            written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default is dry-run)")
    parser.add_argument("--audit-out", type=Path,
                        help="write the full plan as JSON to this path")
    args = parser.parse_args()

    by_number, problems = index_flow_skills()
    print(f"indexed {len(by_number)} flow skills from {HAPTIC_FLOWS.relative_to(REPO_ROOT)}")
    for problem in problems:
        print(f"  ! {problem}")

    flow_changes, flow_skipped = plan_flow_changes(by_number)
    feature_changes, feature_no_target = plan_feature_changes()

    mispointed = [c for c in flow_changes if c["old_existed"]]
    drift = [c for c in flow_changes if "name_drift" in c]

    print(f"\nFlow.flow_skill_path — {len(flow_changes)} to change")
    print(f"  {len(mispointed)} currently point at a real file that is the WRONG file")
    print(f"  {len(flow_skipped)} unresolvable")
    for change in flow_changes:
        tag = "MIS-POINTED" if change["old_existed"] else "broken"
        print(f"  Flow {change['flow_number']:>2}  {change['flow']}")
        print(f"      {tag:12} {change['old']}")
        print(f"      {'->':12} {change['new']}")
    for skip in flow_skipped:
        print(f"  SKIP Flow {skip['flow_number']}: {skip['reason']}")

    print(f"\nFeature.skill_path — {len(feature_changes)} to change, "
          f"{len(feature_no_target)} with no target on disk")
    for change in feature_changes:
        print(f"  {change['feature']}: {change['old']} -> {change['new']}")
    for miss in feature_no_target:
        print(f"  NO TARGET {miss['feature']}: {miss['stored_path']} ({miss['reason']})")

    if drift:
        print(f"\nName drift, informational — graph name kept as source of truth ({len(drift)}):")
        for change in drift:
            print(f"  Flow {change['flow_number']}: {change['name_drift']}")

    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "applied": args.apply,
        "flow_changes": flow_changes,
        "flow_skipped": flow_skipped,
        "feature_changes": feature_changes,
        "feature_no_target": feature_no_target,
        "index_problems": problems,
    }
    if args.audit_out:
        args.audit_out.parent.mkdir(parents=True, exist_ok=True)
        args.audit_out.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print(f"\nplan written to {args.audit_out}")

    total = len(flow_changes) + len(feature_changes)
    if not args.apply:
        print(f"\nDRY RUN — nothing written. {total} change(s) pending; re-run with --apply.")
        return 0

    if total == 0:
        print("\nNothing to do.")
        return 0

    written = apply_changes(flow_changes, feature_changes)
    print(f"\nAPPLIED — {written} node(s) updated.")

    remaining, _ = plan_flow_changes(by_number)
    feature_remaining, _ = plan_feature_changes()
    print(f"verification: {len(remaining)} flow + {len(feature_remaining)} feature "
          f"change(s) still outstanding (expected 0)")

    print(
        "\nNot addressed by this script:\n"
        "  - IS_DEPENDENT_ON: 438 edges, all mirrored, so it means 'related to', not\n"
        "    execution order. Recommend relabelling it RELATED_TO and modelling real\n"
        "    prerequisites separately as a directed, acyclic relationship.\n"
        f"  - {len(feature_no_target)} feature skill path(s) have no file on disk. Those are\n"
        "    genuine authoring gaps, not path bugs, and were left untouched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
