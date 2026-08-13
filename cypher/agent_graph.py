"""LangGraph ReAct loop for the knowledge-graph agent."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, TypedDict

import logfire
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from . import config
from .logging_setup import get_logger
from .tools import ALL_TOOLS

log = get_logger()

MAX_ITERATIONS = 12


class KGAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: int


def _get_llm() -> ChatAnthropic:
    settings = config.anthropic_settings()
    kwargs = {"api_key": settings["api_key"], "model": settings["model"]}
    if settings["model"] != "claude-sonnet-5":
        kwargs["temperature"] = 0.0
    return ChatAnthropic(**kwargs)


@lru_cache(maxsize=1)
def build_kg_graph():
    llm = _get_llm().bind_tools(ALL_TOOLS)
    tool_node = ToolNode(ALL_TOOLS)

    def call_llm(state: KGAgentState) -> dict:
        iteration = state["iterations"] + 1
        response = llm.invoke(state["messages"])
        calls = getattr(response, "tool_calls", None)
        if calls:
            log.info("[llm] iter=%d tool_calls=%s", iteration, [c["name"] for c in calls])
        else:
            log.info("[llm] iter=%d final answer (%d chars)",
                     iteration, len(str(getattr(response, "content", ""))))
        return {"messages": [response], "iterations": iteration}

    def route(state: KGAgentState) -> str:
        if state["iterations"] >= MAX_ITERATIONS:
            log.warning("[route] hit MAX_ITERATIONS=%d", MAX_ITERATIONS)
            return END
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END

    graph = StateGraph(KGAgentState)
    graph.add_node("llm", call_llm)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("llm")
    graph.add_conditional_edges("llm", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "llm")
    return graph.compile()


def _text_of(message: BaseMessage) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ]
        return "".join(parts).strip()
    return str(content).strip()


def run_kg_agent(
    query: str,
    system_prompt: str | None = None,
    history: list[BaseMessage] | None = None,
) -> str:
    log.info("[run] query=%r history=%d", query[:200], len(history or []))
    # Parent span so the LLM calls (via instrument_anthropic) and tool calls
    # (via the spans in tools.py) nest under one trace instead of appearing as
    # unrelated siblings under the FastAPI request span.
    with logfire.span("kg_agent.run", query=query[:300], history_messages=len(history or [])) as span:
        state: KGAgentState = {
            "messages": [
                SystemMessage(content=system_prompt or config.get_kg_prompt()),
                *(history or []),
                HumanMessage(content=query),
            ],
            "iterations": 0,
        }
        final = build_kg_graph().invoke(state)
        answer = _text_of(final["messages"][-1])

        if not answer:
            # Ran out of iterations mid-tool-call; surface that instead of a blank reply.
            answer = (
                "I could not finish this within the tool-call budget. "
                "Try narrowing the question to one feature or flow."
            )
        span.set_attribute("iterations", final["iterations"])
        span.set_attribute("answer_chars", len(answer))

    log.info("[run] done iterations=%d answer_chars=%d", final["iterations"], len(answer))
    return answer
