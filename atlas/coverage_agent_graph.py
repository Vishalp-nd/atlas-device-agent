"""
coverage_agent_graph.py — LangGraph ReAct loop for coverage Q&A using Claude.

Flow: User query → Claude decides which skills to read → reads them one at a
time via tools → iterates until answer is ready (max 10 tool calls).

Tools:
  list_skills  — returns skill names + one-line descriptions for routing
  read_skill   — reads the full SKILL.md for a chosen skill

LLM: Claude via langchain-anthropic, model and API key from .env.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

MAX_ITERATIONS = 10


def _discover_skill_files(skills_root: Path) -> dict[str, Path]:
    """Return map of skill key -> SKILL.md path for nested skills layouts."""
    skills: dict[str, Path] = {}
    if not skills_root.exists():
        return skills
    for skill_md in sorted(skills_root.rglob("SKILL.md")):
        key = skill_md.parent.relative_to(skills_root).as_posix()
        skills[key] = skill_md
    return skills


def _get_llm() -> ChatAnthropic:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(str(env_path), override=False)
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-5").strip()
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set in .env")
    kwargs = {"api_key": api_key, "model": model}
    if model != "claude-sonnet-5":
        kwargs["temperature"] = 0.0
    return ChatAnthropic(**kwargs)


def _make_tools(repo_root: Path) -> list:
    skills_root = repo_root / "skills" / "device-skills"

    @tool
    def list_skills() -> str:
        """List every available skill name and its one-line description.

        Call this first to decide which skill(s) own the primary feature in
        the user's query before calling read_skill.
        """
        lines: list[str] = []
        for skill_name, skill_md in _discover_skill_files(skills_root).items():
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            desc = ""
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    m = re.search(
                        r'^description:\s*["\']?(.+?)["\']?\s*$',
                        parts[1],
                        re.MULTILINE,
                    )
                    if m:
                        desc = m.group(1).strip()[:150]
            lines.append(f"- {skill_name}: {desc}" if desc else f"- {skill_name}")
        return "\n".join(lines) if lines else "No skills found."

    @tool
    def read_skill(skill_name: str) -> str:
        """Read the full SKILL.md for one skill.

        Returns flow definitions, testcase tables, log patterns, and config
        keys. Use the exact skill name from list_skills. Only call this for
        skills whose description suggests they own the specific
        feature+condition combination the user asked about.
        """
        skill_files = _discover_skill_files(skills_root)
        skill_path = skill_files.get(skill_name)
        if skill_path is None:
            query = skill_name.lower()
            basename_matches = [
                key for key in skill_files if Path(key).name.lower() == query
            ]
            if len(basename_matches) == 1:
                skill_path = skill_files[basename_matches[0]]
            elif len(basename_matches) > 1:
                return (
                    f"Skill name '{skill_name}' is ambiguous. "
                    f"Matches: {', '.join(sorted(basename_matches))}. "
                    "Use the full name from list_skills."
                )

        if skill_path is None:
            candidates = [
                key for key in skill_files
                if query in key.lower() or query in Path(key).name.lower()
            ]
            if len(candidates) == 1:
                skill_path = skill_files[candidates[0]]
            elif len(candidates) > 1:
                return (
                    f"Skill '{skill_name}' matched multiple entries: "
                    f"{', '.join(sorted(candidates))}. "
                    "Use the full name from list_skills."
                )
            else:
                return (
                    f"Skill '{skill_name}' not found. "
                    "Call list_skills to see available names."
                )
        return skill_path.read_text(encoding="utf-8", errors="replace")

    return [list_skills, read_skill]


class CoverageAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: int


def build_coverage_graph(repo_root: Path):
    tools = _make_tools(repo_root)
    llm = _get_llm().bind_tools(tools)
    tool_node = ToolNode(tools)

    def call_llm(state: CoverageAgentState) -> dict:
        response = llm.invoke(state["messages"])
        return {
            "messages": [response],
            "iterations": state["iterations"] + 1,
        }

    def route(state: CoverageAgentState) -> str:
        if state["iterations"] >= MAX_ITERATIONS:
            return END
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph = StateGraph(CoverageAgentState)
    graph.add_node("llm", call_llm)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "llm")
    return graph.compile()


def run_coverage_agent(
    query: str,
    system_prompt: str,
    repo_root: Path,
    history: list[BaseMessage] | None = None,
) -> str:
    """Run the coverage agent and return the final answer string.

    `history` (if given) is inserted between the system prompt and the new
    query, letting callers get multi-turn continuity without going through
    the supervisor's intent classification.
    """
    graph = build_coverage_graph(repo_root)
    initial_state: CoverageAgentState = {
        "messages": [
            SystemMessage(content=system_prompt),
            *(history or []),
            HumanMessage(content=query),
        ],
        "iterations": 0,
    }
    final_state = graph.invoke(initial_state)
    last = final_state["messages"][-1]
    return getattr(last, "content", str(last)).strip() or "No response."
