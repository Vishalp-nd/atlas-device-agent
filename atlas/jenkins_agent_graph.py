"""
jenkins_agent_graph.py — LangGraph ReAct agent for triggering Jenkins builds.

Flow: User query → Claude identifies the job → fetches parameters → builds.

Tools:
  list_jenkins_jobs    — list jobs matching a filter string
  get_job_parameters   — return the parameters a job requires
  build_jenkins_job    — trigger the build with resolved parameters

LLM: Claude Sonnet via langchain-anthropic (shared _get_llm with coverage agent).
Jenkins client: python-jenkins, credentials from .env.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, TypedDict

import jenkins
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

MAX_ITERATIONS = 10


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


def _get_jenkins_client() -> jenkins.Jenkins:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(str(env_path), override=False)
    url = os.getenv("JENKINS_URL", "").strip()
    user = os.getenv("JENKINS_USER", "").strip()
    token = os.getenv("JENKINS_API_TOKEN", "").strip()
    missing = [k for k, v in {"JENKINS_URL": url, "JENKINS_USER": user, "JENKINS_API_TOKEN": token}.items() if not v]
    if missing:
        raise EnvironmentError(
            f"Jenkins credentials missing in .env: {', '.join(missing)}. "
            "Add JENKINS_URL, JENKINS_USER, and JENKINS_API_TOKEN."
        )
    return jenkins.Jenkins(url, username=user, password=token)


def _make_tools() -> list:

    @tool
    def list_jenkins_jobs(filter: str = "") -> str:
        """List Jenkins job names matching an optional filter string.

        Call this first to identify which job the user wants to build.
        Pass a short keyword from the query (e.g. 'integration', 'nightly', device serial).
        Returns a newline-separated list of matching job names.
        """
        try:
            client = _get_jenkins_client()
            jobs = client.get_all_jobs()
            names = [j["fullname"] for j in jobs]
            if filter:
                fl = filter.lower()
                names = [n for n in names if fl in n.lower()]
            return "\n".join(names) if names else f"No jobs found matching '{filter}'."
        except EnvironmentError as exc:
            return str(exc)
        except Exception as exc:
            return f"Failed to list jobs: {exc}"

    @tool
    def get_job_parameters(job_name: str) -> str:
        """Return the parameters defined for a Jenkins job as a JSON array.

        Call this after identifying the job name. Each parameter has:
        - name: parameter key
        - type: ChoiceParameterDefinition, StringParameterDefinition, etc.
        - default: default value (empty string if none)
        - description: what the parameter controls

        Use this to know what values are required before calling build_jenkins_job.
        """
        try:
            client = _get_jenkins_client()
            info = client.get_job_info(job_name)
            prop = next(
                (p for p in info.get("property", []) if "parameterDefinitions" in p),
                None,
            )
            if not prop:
                return json.dumps([])
            params = [
                {
                    "name": p["name"],
                    "type": p["type"],
                    "default": (p.get("defaultParameterValue") or {}).get("value", ""),
                    "description": p.get("description", ""),
                }
                for p in prop["parameterDefinitions"]
            ]
            return json.dumps(params, indent=2)
        except EnvironmentError as exc:
            return str(exc)
        except Exception as exc:
            return f"Failed to get parameters for '{job_name}': {exc}"

    @tool
    def build_jenkins_job(job_name: str, parameters: str) -> str:
        """Trigger a Jenkins build for the given job with the provided parameters.

        Parameters must be a JSON object string mapping parameter names to values,
        e.g. '{"DEVICE_SERIAL": "12345", "BRANCH": "main"}'.
        Pass an empty JSON object '{}' if the job has no parameters.

        Returns the build queue item number, job URL, and live report link
        (extracted from the job description field if present).
        """
        try:
            client = _get_jenkins_client()
            try:
                params_dict = json.loads(parameters) if parameters.strip() else {}
            except json.JSONDecodeError as exc:
                return f"Invalid parameters JSON: {exc}"

            queue_item = client.build_job(job_name, parameters=params_dict)
            job_info = client.get_job_info(job_name)
            job_url = job_info.get("url", "")
            report_link = (job_info.get("description") or "").strip()
            lines = [
                "Build triggered successfully.",
                f"Job: {job_name}",
                f"Queue item: {queue_item}",
                f"Job URL: {job_url}",
            ]
            if report_link:
                lines.append(f"Report: {report_link}")
            return "\n".join(lines)
        except EnvironmentError as exc:
            return str(exc)
        except Exception as exc:
            return f"Failed to trigger build for '{job_name}': {exc}"

    return [list_jenkins_jobs, get_job_parameters, build_jenkins_job]


class JenkinsAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: int


def build_jenkins_graph():
    tools = _make_tools()
    llm = _get_llm().bind_tools(tools)
    tool_node = ToolNode(tools)

    def call_llm(state: JenkinsAgentState) -> dict:
        response = llm.invoke(state["messages"])
        return {
            "messages": [response],
            "iterations": state["iterations"] + 1,
        }

    def route(state: JenkinsAgentState) -> str:
        if state["iterations"] >= MAX_ITERATIONS:
            return END
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph = StateGraph(JenkinsAgentState)
    graph.add_node("llm", call_llm)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "llm")
    return graph.compile()


def run_jenkins_agent(
    query: str,
    system_prompt: str,
    history: list[BaseMessage] | None = None,
) -> str:
    """Run the Jenkins agent and return the final answer string.

    `history` (if given) is inserted between the system prompt and the new
    query, letting callers get multi-turn continuity without going through
    the supervisor's intent classification.
    """
    graph = build_jenkins_graph()
    initial_state: JenkinsAgentState = {
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
