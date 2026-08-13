"""Settings, paths, and prompt loader for the KG agent."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_ROOT = REPO_ROOT / "prompts"
ENV_PATH = REPO_ROOT / ".env"
LOG_DIR = REPO_ROOT / "logs"
PROMPT_NAME = "cypher-graph"

MAX_ROWS_HARD_CAP = 1000

# override=False so real env vars (container, CI) beat the file — same as atlas/.
if ENV_PATH.exists():
    load_dotenv(str(ENV_PATH), override=False)


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise EnvironmentError(f"{name} not set. Add it to {ENV_PATH} or the environment.")
    return value


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, min(int(raw), maximum))
    except ValueError:
        return default


def neo4j_settings() -> dict[str, str]:
    # Resolved lazily so a missing NEO4J_* var surfaces on /health, not at import.
    return {
        "uri": _require("NEO4J_URI"),
        "username": _require("NEO4J_USERNAME"),
        "password": _require("NEO4J_PASSWORD"),
        "database": (os.getenv("NEO4J_DATABASE") or "neo4j").strip(),
    }


def anthropic_settings() -> dict[str, str]:
    return {
        "api_key": _require("ANTHROPIC_API_KEY"),
        "model": (os.getenv("CLAUDE_MODEL") or "claude-sonnet-5").strip(),
    }


def default_max_rows() -> int:
    return _int_env("CYPHER_MAX_ROWS", 200, minimum=1, maximum=MAX_ROWS_HARD_CAP)


def query_timeout_seconds() -> int:
    return _int_env("CYPHER_TIMEOUT_SECONDS", 15, minimum=1, maximum=120)


def skill_search_roots() -> list[Path]:
    roots = [
        REPO_ROOT / ".github" / "skills",
        REPO_ROOT / "skills" / "device-skills",
        REPO_ROOT / "skills" / "utility-skills",
        REPO_ROOT / "skills" / "cinfo-skills",
    ]
    extra = (os.getenv("DEVICE_AUTOMATION_ROOT") or "").strip()
    if extra:
        base = Path(extra).expanduser()
        roots += [base / ".github" / "skills", base]
    return [r for r in roots if r.is_dir()]


def path_prefix_roots() -> list[Path]:
    """Roots a stored repo-relative skill path may hang off."""
    roots = [REPO_ROOT]
    extra = (os.getenv("DEVICE_AUTOMATION_ROOT") or "").strip()
    if extra:
        base = Path(extra).expanduser()
        roots += [base, base.parent]
    return [r for r in roots if r.is_dir()]


_FALLBACK_PROMPT = (
    "You are the device-QA knowledge-graph assistant. Answer questions about "
    "product lines, services, features, test flows, and the device conditions "
    "that gate them, using only the graph tools provided. Never invent nodes, "
    "flows, or coverage that the tools did not return."
)


@lru_cache(maxsize=1)
def get_kg_prompt() -> str:
    path = PROMPTS_ROOT / f"{PROMPT_NAME}.agent.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _FALLBACK_PROMPT
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return text.strip() or _FALLBACK_PROMPT
