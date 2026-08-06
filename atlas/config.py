"""config.py — shared paths and cached system-prompt loaders for the Atlas agent API."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .coverage_chatbot_core import load_agent_system_prompt

# atlas/config.py -> parents[1] == atlas-device-agent/ (repo root)
REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_ROOT = REPO_ROOT / "prompts"
SKILLS_ROOT = REPO_ROOT / "skills"
PIPELINE_ROOT = REPO_ROOT / "pipeline"
ENV_PATH = REPO_ROOT / ".env"
DB_CREDENTIALS_PATH = REPO_ROOT / "db_credentials.ini"

# Optional pointer at a device-automation checkout, used only for the
# testcase-count stat (test_cases/ stays in that repo).
DEVICE_AUTOMATION_ROOT = (
    Path(os.environ["DEVICE_AUTOMATION_ROOT"]).expanduser()
    if os.environ.get("DEVICE_AUTOMATION_ROOT")
    else None
)


@lru_cache(maxsize=1)
def get_coverage_prompt() -> str:
    return load_agent_system_prompt(REPO_ROOT, "coverage-chatbot")


@lru_cache(maxsize=1)
def get_jenkins_prompt() -> str:
    return load_agent_system_prompt(REPO_ROOT, "jenkins-agent")


@lru_cache(maxsize=1)
def get_critical_prompt() -> str:
    return load_agent_system_prompt(REPO_ROOT, "critical-events-insights")


@lru_cache(maxsize=1)
def get_observations_prompt() -> str:
    return load_agent_system_prompt(REPO_ROOT, "observations-insights")
