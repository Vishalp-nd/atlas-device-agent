"""
coverage_chatbot_core.py - Loads the coverage chatbot system prompt from the
agent definition file (prompts/coverage-chatbot.agent.md).
"""

from __future__ import annotations

from pathlib import Path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def load_agent_system_prompt(repo_root: Path, agent_name: str = "coverage-chatbot") -> str:
    """Load chatbot behavior from prompts/<agent_name>.agent.md."""
    agent_path = repo_root / "prompts" / f"{agent_name}.agent.md"
    text = _read_text(agent_path)
    if not text:
        return (
            "You are a framework coverage assistant for pytest_device_validator. "
            "Map user questions to skills, flows, and testcases with evidence."
        )
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]
    return text.strip() or (
        "You are a framework coverage assistant for pytest_device_validator. "
        "Map user questions to skills, flows, and testcases with evidence."
    )
