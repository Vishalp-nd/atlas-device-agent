"""
supervisor_graph.py — Main LangGraph supervisor for the Atlas multi-agent system.

Graph structure:
    START → classify → (conditional edge on intent) → coverage | jenkins | critical_events | unknown → END

Adding a new agent:
  1. Create <agent>_graph.py with build_<agent>_graph() and run_<agent>_agent()
  2. Add a node function _run_<agent>_node() here
  3. Add the node and a branch in _route_intent()

Nodes:
    classify        — Haiku LLM call; sets state["intent"]
    coverage        — invokes the coverage subgraph; sets state["response"]
    jenkins         — invokes the Jenkins subgraph; sets state["response"]
    critical_events — invokes DB + skills critical-events subgraph
    unknown         — Haiku LLM call; generates a contextual help response

LLM: Claude Haiku for classify + unknown (fast, cheap).
     Sub-agents use Claude Sonnet via their own _get_llm().
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TypedDict, List

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from .coverage_agent_graph import run_coverage_agent
from .critical_events_agent_graph import run_critical_events_agent
from .jenkins_agent_graph import run_jenkins_agent

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_HISTORY = 6  # max messages (not turns) passed as context
CRITICAL_EVENTS_SUMMARIZE_AT = 4

# ── Logger setup ──────────────────────────────────────────────────────────────

def _setup_logger() -> logging.Logger:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "atlas_supervisor.log"

    logger = logging.getLogger("atlas.supervisor")
    if logger.handlers:
        return logger  # already configured (e.g. on Streamlit rerun)

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger

log = _setup_logger()

_INTENT_SYSTEM = """\
Given the conversation history (if any) and the latest user message, classify the
LATEST message into exactly one of these intents:
- "coverage": asking which testcases, skills, or flows cover a feature or service
- "jenkins": asking to build, run, trigger, or check a Jenkins job
- "critical_events": usually called cinfo, crit info etc. Asking about critical events data, code trends, error/info split,
  top processes, or analytics from local critical-events database
- "unknown": neither of the above

If the history shows an ongoing Jenkins or coverage interaction and the latest message
looks like a follow-up (e.g. answering a question, providing a missing parameter value),
classify it as the same intent as the ongoing conversation.

Respond with only the intent label — no punctuation, no explanation.\
"""

_UNKNOWN_SYSTEM = """\
You are Atlas, an assistant with the below capabilities.
You can help with exactly three things:
1. Test coverage questions — e.g. "which testcases cover bagheera LPW?"
2. Jenkins builds — e.g. "run the nightly integration job for device 12345"
3. Critical events analytics — e.g. "top error codes for 6.15.rc.1 in last day"

The user sent a message that doesn't clearly match these capabilities.
Respond appropriately: give one concrete example of each capability.
"""

_CRITICAL_EVENTS_SUMMARY_SYSTEM = """\
You are compressing prior conversation context for a critical-events analytics agent.
Summarize the conversation into one compact assistant message that preserves only the
details needed for follow-up analysis.

Keep:
- requested environment: production, staging, or compare/both
- active filters such as version, time window, process, code, device, tenant
- any comparison goal or unresolved question
- important findings already established

Drop:
- greetings, filler, repeated wording
- tool chatter or implementation details

Write 4-7 short bullet-style lines in plain text. Do not invent facts.
"""


class SupervisorState(TypedDict):
    query: str
    history: List[BaseMessage]   # last MAX_HISTORY messages before current query
    last_intent: str             # intent of the previous turn ("" on first turn)
    intent: str
    coverage_prompt: str
    jenkins_prompt: str
    critical_prompt: str
    repo_root: Path
    response: str


# ── LLM factory (Haiku for routing only) ──────────────────────────────────────

def _get_haiku() -> ChatAnthropic:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(str(env_path), override=False)
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set in .env")
    return ChatAnthropic(api_key=api_key, model=_HAIKU_MODEL, temperature=0.0)


# ── Nodes ─────────────────────────────────────────────────────────────────────

def _classify_node(state: SupervisorState) -> dict:
    """Classify intent using history + current query; result stored in state['intent']."""
    query_preview = state["query"][:120].replace("\n", " ")
    try:
        llm = _get_haiku()
        messages = (
            [SystemMessage(content=_INTENT_SYSTEM)]
            + list(state["history"])
            + [HumanMessage(content=state["query"])]
        )
        response = llm.invoke(messages, max_tokens=10)
        label = getattr(response, "content", "").strip().lower().strip("\"'")
        intent = label if label in ("coverage", "jenkins", "critical_events") else "unknown"
    except Exception as exc:
        log.warning("Intent classification failed (%s) — defaulting to 'coverage'", exc)
        intent = "coverage"

    if intent == "unknown" and state["last_intent"] in ("coverage", "jenkins", "critical_events"):
        log.info(
            "INTENT | classified='unknown' — overriding to '%s' (continuation) last='%s' | query: %s",
            state["last_intent"], state["last_intent"], query_preview,
        )
        intent = state["last_intent"]
    else:
        log.info("INTENT | classified='%s' last='%s' | query: %s", intent, state["last_intent"], query_preview)

    return {"intent": intent}


def _relevant_history(state: SupervisorState) -> List[BaseMessage]:
    """Return history only if the intent hasn't changed since the last turn."""
    current = state["intent"]
    previous = state["last_intent"]
    history = list(state["history"])

    if current == previous or previous == "unknown":
        if current == "critical_events" and len(history) >= CRITICAL_EVENTS_SUMMARIZE_AT:
            summarized = _summarize_critical_events_history(history)
            log.info(
                "HISTORY | critical_events summarized %d message(s) into %d message(s)",
                len(history),
                len(summarized),
            )
            return summarized

        log.debug(
            "HISTORY | intent '%s' → '%s' (from unknown/no-op) — passing %d message(s)",
            previous or "none", current, len(history),
        )
        return history

    log.info(
        "HISTORY | intent changed: '%s' → '%s' — history cleared (%d message(s) dropped)",
        previous or "none",
        current,
        len(history),
    )
    return []


def _summarize_critical_events_history(history: List[BaseMessage]) -> List[BaseMessage]:
    """Compress critical-events history into one summary message plus the latest turn."""
    if len(history) < CRITICAL_EVENTS_SUMMARIZE_AT:
        return history

    latest_tail = history[-2:] if len(history) >= 2 else history[-1:]
    earlier = history[:-len(latest_tail)] if latest_tail else history
    if not earlier:
        return history

    transcript_parts = []
    for message in earlier:
        role = "user" if isinstance(message, HumanMessage) else "assistant"
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = " ".join(str(part) for part in content)
        transcript_parts.append(f"{role}: {str(content).strip()}")

    try:
        llm = _get_haiku()
        response = llm.invoke(
            [
                SystemMessage(content=_CRITICAL_EVENTS_SUMMARY_SYSTEM),
                HumanMessage(content="\n".join(transcript_parts)),
            ],
            max_tokens=220,
        )
        summary = getattr(response, "content", "").strip()
        if not summary:
            raise ValueError("empty summary")
        return [
            SystemMessage(content=f"Conversation summary for critical-events follow-up:\n{summary}"),
            *latest_tail,
        ]
    except Exception as exc:
        log.warning("HISTORY | critical_events summarization failed: %s", exc)
        return history


def _coverage_node(state: SupervisorState) -> dict:
    """Invoke the coverage agent; passes history only on same-intent continuation."""
    response = run_coverage_agent(
        state["query"],
        state["coverage_prompt"],
        state["repo_root"],
        history=_relevant_history(state),
    )
    return {"response": response}


def _jenkins_node(state: SupervisorState) -> dict:
    """Invoke the Jenkins agent; passes history only on same-intent continuation."""
    response = run_jenkins_agent(
        state["query"],
        state["jenkins_prompt"],
        history=_relevant_history(state),
    )
    return {"response": response}


def _critical_events_node(state: SupervisorState) -> dict:
    """Invoke the critical-events agent; passes history on same-intent continuation."""
    response = run_critical_events_agent(
        state["query"],
        state["critical_prompt"],
        state["repo_root"],
        history=_relevant_history(state),
    )
    return {"response": response}


def _unknown_node(state: SupervisorState) -> dict:
    """Generate a natural language help response for unrecognised queries."""
    try:
        llm = _get_haiku()
        response = llm.invoke(
            [
                SystemMessage(content=_UNKNOWN_SYSTEM),
                HumanMessage(content=f"User said: {state['query']}"),
            ],
            max_tokens=120,
        )
        answer = getattr(response, "content", "").strip()
    except Exception:
        answer = (
            "I can help with test coverage questions (e.g. 'which testcases cover bagheera LPW?') "
            "or Jenkins builds (e.g. 'run the nightly job for device 12345'). What would you like to do?"
        )
    return {"response": answer}


# ── Routing ───────────────────────────────────────────────────────────────────

def _route_intent(state: SupervisorState) -> str:
    """Conditional edge: route to the node matching state['intent']."""
    return state["intent"]  # "coverage" | "jenkins" | "critical_events" | "unknown"


# ── Graph construction ────────────────────────────────────────────────────────

def build_supervisor_graph():
    graph = StateGraph(SupervisorState)

    graph.add_node("classify", _classify_node)
    graph.add_node("coverage", _coverage_node)
    graph.add_node("jenkins", _jenkins_node)
    graph.add_node("critical_events", _critical_events_node)
    graph.add_node("unknown", _unknown_node)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        _route_intent,
        {
            "coverage": "coverage",
            "jenkins": "jenkins",
            "critical_events": "critical_events",
            "unknown": "unknown",
        },
    )
    graph.add_edge("coverage", END)
    graph.add_edge("jenkins", END)
    graph.add_edge("critical_events", END)
    graph.add_edge("unknown", END)

    return graph.compile()


# ── Public entry point ────────────────────────────────────────────────────────

def run_supervisor(
    query: str,
    coverage_prompt: str,
    jenkins_prompt: str,
    repo_root: Path,
    critical_prompt: str,
    history: list | None = None,
    last_intent: str = "",
) -> tuple[str, str]:
    """Run the supervisor graph. Returns (response, intent) so the caller can
    persist the intent and pass it back on the next turn."""
    log.info("TURN | last_intent='%s' history_len=%d | query: %s",
             last_intent or "none", len(history or []), query[:120].replace("\n", " "))
    graph = build_supervisor_graph()
    final = graph.invoke({
        "query": query,
        "history": history or [],
        "last_intent": last_intent,
        "intent": "",
        "coverage_prompt": coverage_prompt,
        "jenkins_prompt": jenkins_prompt,
        "critical_prompt": critical_prompt,
        "repo_root": repo_root,
        "response": "",
    })
    return final["response"], final["intent"]
