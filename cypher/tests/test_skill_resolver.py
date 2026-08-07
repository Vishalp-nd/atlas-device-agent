"""Resolver tests, run against the real skill tree on disk."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from cypher import skill_resolver  # noqa: E402

HAPTIC = REPO_ROOT / ".github/skills/haptic-service-validation"

pytestmark = pytest.mark.skipif(
    not HAPTIC.is_dir(), reason="haptic skill tree not present"
)


def test_exact_path_resolves():
    stored = ".github/skills/haptic-service-validation/flows/sigabrt-crash/SKILL.md"
    result = skill_resolver.resolve(stored, kind="flow", name="SIGABRT Crash Respawn")
    assert result.status == "exact"
    assert result.path is not None and result.path.is_file()


def test_missing_flows_segment_is_corrected():
    stored = ".github/skills/haptic-service-validation/sigabrt-crash/SKILL.md"
    result = skill_resolver.resolve(stored, kind="flow", name="SIGABRT Crash Respawn")
    assert result.found
    assert "flows" in result.path.parts


def test_flow_prefers_flow_skill_over_same_named_topic_skill():
    """The bug that made 5 flows silently read the wrong document: a stored path
    that exists, but points at a topic skill sharing the flow's slug."""
    stored = ".github/skills/haptic-service-validation/health-stats-payload/SKILL.md"
    assert (REPO_ROOT / stored).is_file()  # the wrong file really does exist

    as_flow = skill_resolver.resolve(stored, kind="flow", name="Health Stats Payload")
    assert as_flow.status == "corrected"
    assert "flows" in as_flow.path.parts

    as_feature = skill_resolver.resolve(stored, kind="feature", name="whatever")
    assert "flows" not in as_feature.path.parts


def test_missing_leading_dot_in_github_prefix():
    result = skill_resolver.resolve(
        "github/skills/haptic-service-validation/SKILL.md",
        kind="feature",
        name="haptic_feedback",
    )
    assert result.found
    assert result.relative.startswith(".github/")


def test_resolution_by_node_name_when_path_is_useless():
    result = skill_resolver.resolve(
        ".github/skills/nonexistent-xyz/SKILL.md", kind="flow", name="sigabrt-crash"
    )
    assert result.found
    assert result.status in {"slug", "fuzzy"}


def test_unresolvable_reports_missing_not_a_wrong_guess():
    result = skill_resolver.resolve(
        ".github/skills/totally-unrelated-qqq/SKILL.md", kind="flow", name="zzz nothing"
    )
    assert not result.found
    assert result.status == "missing"


def test_none_and_empty_are_missing():
    assert skill_resolver.resolve(None, kind="flow", name="").status == "missing"
    assert skill_resolver.resolve("", kind="flow", name="").status == "missing"


def test_read_skill_returns_content():
    result = skill_resolver.resolve(
        ".github/skills/haptic-service-validation/flows/sigabrt-crash/SKILL.md",
        kind="flow",
        name="SIGABRT Crash Respawn",
    )
    assert "SIGABRT" in skill_resolver.read_skill(result)


def test_refresh_index_counts_files():
    assert skill_resolver.refresh_index() > 50
