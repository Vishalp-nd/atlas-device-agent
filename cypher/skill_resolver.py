"""Resolve a skill path stored in the graph to a real SKILL.md on disk.

Stored paths drift from reality (missing `flows/` segment, slug renames, a
`github/` prefix that lost its dot), so nothing trusts them blindly. Resolution
degrades through exact → corrected → slug → fuzzy and reports which rung it
landed on, so the agent can say "I matched this loosely" instead of pretending.
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

_MAX_SKILL_CHARS = 60_000
_INDEX_TTL_SECONDS = 60

_lock = threading.Lock()
_index: dict | None = None
_index_built_at = 0.0


@dataclass
class ResolvedSkill:
    status: str                      # exact | corrected | slug | fuzzy | missing
    path: Path | None = None
    relative: str = ""
    note: str = ""
    candidates: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.path is not None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _build_index() -> dict:
    by_slug: dict[str, list[Path]] = {}
    all_relative: set[str] = set()
    for root in config.skill_search_roots():
        for md in root.rglob("SKILL.md"):
            by_slug.setdefault(md.parent.name.lower(), []).append(md)
            all_relative.add(_relative(md))
    return {"by_slug": by_slug, "all_relative": sorted(all_relative)}


def _get_index() -> dict:
    global _index, _index_built_at
    with _lock:
        if _index is None or (time.time() - _index_built_at) > _INDEX_TTL_SECONDS:
            _index = _build_index()
            _index_built_at = time.time()
        return _index


def refresh_index() -> int:
    global _index, _index_built_at
    with _lock:
        _index = _build_index()
        _index_built_at = time.time()
        return sum(len(v) for v in _index["by_slug"].values())


def _relative(path: Path) -> str:
    for root in config.path_prefix_roots():
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def _literal(stored: str) -> Path | None:
    cleaned = (stored or "").strip().lstrip("/")
    if not cleaned:
        return None
    variants = [cleaned]
    if cleaned.startswith("github/"):
        variants.append("." + cleaned)
    if not cleaned.endswith("SKILL.md"):
        variants = [f"{v.rstrip('/')}/SKILL.md" for v in variants]
    for root in config.path_prefix_roots():
        for variant in variants:
            candidate = root / variant
            if candidate.is_file():
                return candidate
    return None


def _pick(paths: list[Path], *, prefer_flows: bool) -> Path | None:
    if not paths:
        return None
    in_flows = [p for p in paths if "flows" in p.parts]
    outside = [p for p in paths if "flows" not in p.parts]
    ordered = (in_flows + outside) if prefer_flows else (outside + in_flows)
    return ordered[0] if ordered else None


def resolve(stored_path: str | None, *, kind: str | None = None, name: str = "") -> ResolvedSkill:
    """kind="flow" prefers a `flows/<slug>/SKILL.md` match, which is what makes
    the 5 flows whose stored path points at a same-named topic skill resolve
    to the actual flow skill instead."""
    index = _get_index()
    by_slug = index["by_slug"]
    prefer_flows = kind == "flow"

    literal = _literal(stored_path or "")
    stored_slug = Path((stored_path or "").rstrip("/")).parent.name.lower()
    slug_matches = by_slug.get(stored_slug, [])

    if literal is not None:
        better = _pick([p for p in slug_matches if p != literal], prefer_flows=True)
        if prefer_flows and "flows" not in literal.parts and better is not None:
            return ResolvedSkill(
                "corrected", better, _relative(better),
                note=(f"stored path resolves to a topic skill "
                      f"({_relative(literal)}); used the flow skill instead"),
            )
        return ResolvedSkill("exact", literal, _relative(literal))

    picked = _pick(slug_matches, prefer_flows=prefer_flows)
    if picked is not None:
        return ResolvedSkill(
            "corrected", picked, _relative(picked),
            note=f"stored path does not exist; matched by directory name '{stored_slug}'",
        )

    name_slug = _slugify(name)
    if name_slug:
        picked = _pick(by_slug.get(name_slug, []), prefer_flows=prefer_flows)
        if picked is not None:
            return ResolvedSkill(
                "slug", picked, _relative(picked),
                note=f"matched by node name slug '{name_slug}'",
            )

    haystack = list(by_slug)
    for probe in (stored_slug, name_slug):
        if not probe:
            continue
        close = difflib.get_close_matches(probe, haystack, n=3, cutoff=0.72)
        if close:
            picked = _pick(by_slug[close[0]], prefer_flows=prefer_flows)
            if picked is not None:
                return ResolvedSkill(
                    "fuzzy", picked, _relative(picked),
                    note=f"no exact match; closest directory was '{close[0]}' (verify this is right)",
                    candidates=close,
                )

    return ResolvedSkill(
        "missing",
        note=f"no SKILL.md found for stored path {stored_path!r}",
        candidates=difflib.get_close_matches(stored_slug or name_slug, haystack, n=5, cutoff=0.5),
    )


def read_skill(resolved: ResolvedSkill) -> str:
    if not resolved.found or resolved.path is None:
        return f"Not found: {resolved.note}"
    text = resolved.path.read_text(encoding="utf-8", errors="replace")
    if len(text) > _MAX_SKILL_CHARS:
        text = text[:_MAX_SKILL_CHARS] + "\n\n[truncated]"
    return text


def find_by_query(query: str, limit: int = 15) -> list[str]:
    index = _get_index()
    needle = (query or "").lower().strip()
    if not needle:
        return []
    hits = [rel for rel in index["all_relative"] if needle in rel.lower()]
    if hits:
        return hits[:limit]
    close = difflib.get_close_matches(_slugify(query), list(index["by_slug"]), n=limit, cutoff=0.5)
    return [_relative(p) for slug in close for p in index["by_slug"][slug]][:limit]
