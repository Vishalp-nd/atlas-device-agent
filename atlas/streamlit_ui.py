from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
import streamlit as st

API_BASE_URL = os.environ.get("ATLAS_API_URL", "http://172.16.18.142:8501").rstrip("/")
REQUEST_TIMEOUT = 900


def _normalize_download_links(text: str) -> str:
    if not text:
        return text
    api_base = API_BASE_URL.rstrip("/")
    text = text.replace("(/atlas/agents/observations/download/", f"({api_base}/atlas/agents/observations/download/")
    text = re.sub(
        r"\(https?://(?:localhost|127\.0\.0\.1)(?::\d+)?/atlas/agents/observations/download/",
        f"({api_base}/atlas/agents/observations/download/",
        text,
    )
    return text


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800&family=IBM+Plex+Mono:wght@400;600&display=swap');

        :root {
            --bg-0: #ffffff;
            --bg-1: #f5fff9;
            --panel: #ffffff;
            --text: #111111;
            --muted: #2f5a3f;
            --accent: #00a651;
            --accent-2: #2ecf7a;
            --border: rgba(0, 166, 81, 0.28);
            --shadow: 0 16px 36px rgba(0, 166, 81, 0.14);
        }

        .stApp {
            font-family: 'Outfit', sans-serif;
            color: var(--text);
            background:
              radial-gradient(1000px 620px at -10% -10%, #f2fff6 0%, transparent 60%),
              radial-gradient(820px 620px at 120% 10%, #ecfff4 0%, transparent 58%),
              linear-gradient(160deg, var(--bg-0) 0%, var(--bg-1) 100%);
        }

        .stApp .stChatFloatingInputContainer,
        .stApp .stChatFloatingInputContainer > div,
        .stApp [data-testid="stBottomBlockContainer"],
        .stApp [data-testid="stBottomBlockContainer"] > div {
            background: #ffffff !important;
            background-image: none !important;
            box-shadow: none !important;
        }

        header[data-testid="stHeader"],
        [data-testid="stToolbar"],
        footer,
        [data-testid="stStatusWidget"] {
            background: #ffffff !important;
            color: #111111 !important;
            border-color: var(--border) !important;
        }

        [data-testid="stMainBlockContainer"] {
            padding-top: 1.75rem !important;
        }

        [data-testid="stSidebar"] {
            background: #ffffff !important;
            border-right: 1px solid var(--border);
            box-shadow: 8px 0 24px rgba(0, 166, 81, 0.08);
        }

        [data-testid="stSidebarNav"] {
            display: none !important;
        }

        [data-testid="stSidebar"] * {
            color: #111111;
        }

        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] label {
            color: #007a3d !important;
        }

        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="collapsedControl"] *,
        [data-testid="stSidebarCollapsedControl"] *,
        div:has(> button[kind="header"]) {
            opacity: 1 !important;
            visibility: visible !important;
        }

        button[kind="header"],
        button[kind="headerNoPadding"],
        button[aria-label="Open sidebar"],
        button[aria-label="Close sidebar"],
        [data-testid="stExpandSidebarButton"],
        [data-testid="stBaseButton-headerNoPadding"],
        [data-testid="collapsedControl"] button,
        [data-testid="stSidebarCollapsedControl"] button {
            background: #ffffff !important;
            color: #007a3d !important;
            border: 1.5px solid rgba(0, 166, 81, 0.72) !important;
            box-shadow: 0 8px 20px rgba(0, 166, 81, 0.12) !important;
            opacity: 1 !important;
            visibility: visible !important;
            transition: none !important;
            border-radius: 10px !important;
        }

        button[kind="header"]:hover,
        button[kind="headerNoPadding"]:hover,
        button[aria-label="Open sidebar"]:hover,
        button[aria-label="Close sidebar"]:hover,
        [data-testid="stExpandSidebarButton"]:hover,
        [data-testid="stBaseButton-headerNoPadding"]:hover,
        [data-testid="collapsedControl"] button:hover,
        [data-testid="stSidebarCollapsedControl"] button:hover,
        button[kind="header"]:focus,
        button[kind="headerNoPadding"]:focus,
        button[aria-label="Open sidebar"]:focus,
        button[aria-label="Close sidebar"]:focus,
        [data-testid="stExpandSidebarButton"]:focus,
        [data-testid="stBaseButton-headerNoPadding"]:focus,
        [data-testid="collapsedControl"] button:focus,
        [data-testid="stSidebarCollapsedControl"] button:focus {
            background: #ffffff !important;
            color: #007a3d !important;
            border-color: rgba(0, 166, 81, 0.95) !important;
            box-shadow: 0 10px 24px rgba(0, 166, 81, 0.18) !important;
            opacity: 1 !important;
        }

        button[kind="header"] svg,
        button[kind="headerNoPadding"] svg,
        button[aria-label="Open sidebar"] svg,
        button[aria-label="Close sidebar"] svg,
        [data-testid="stExpandSidebarButton"] svg,
        [data-testid="stBaseButton-headerNoPadding"] svg,
        [data-testid="collapsedControl"] button svg,
        [data-testid="stSidebarCollapsedControl"] button svg {
            fill: #007a3d !important;
            color: #007a3d !important;
            stroke: #007a3d !important;
            opacity: 1 !important;
        }

        button[kind="header"] svg path,
        button[kind="headerNoPadding"] svg path,
        button[aria-label="Open sidebar"] svg path,
        button[aria-label="Close sidebar"] svg path,
        [data-testid="stExpandSidebarButton"] svg path,
        [data-testid="stBaseButton-headerNoPadding"] svg path,
        [data-testid="collapsedControl"] button svg path,
        [data-testid="stSidebarCollapsedControl"] button svg path {
            fill: #007a3d !important;
            stroke: #007a3d !important;
            opacity: 1 !important;
        }

        button[kind="header"] span,
        button[kind="headerNoPadding"] span,
        [data-testid="stExpandSidebarButton"] span,
        [data-testid="stBaseButton-headerNoPadding"] span {
            color: #007a3d !important;
        }

        button[kind="header"]:before,
        button[kind="headerNoPadding"]:before,
        button[aria-label="Open sidebar"]:before,
        button[aria-label="Close sidebar"]:before,
        button[kind="header"]:after,
        button[kind="headerNoPadding"]:after,
        button[aria-label="Open sidebar"]:after,
        button[aria-label="Close sidebar"]:after,
        [data-testid="collapsedControl"] button:before,
        [data-testid="collapsedControl"] button:after,
        [data-testid="stSidebarCollapsedControl"] button:before,
        [data-testid="stSidebarCollapsedControl"] button:after {
            opacity: 1 !important;
        }

        [data-testid="stSidebar"] button,
        .stButton > button {
            background: #ffffff !important;
            color: #007a3d !important;
            border: 1.5px solid rgba(0, 166, 81, 0.7) !important;
            box-shadow: 0 8px 20px rgba(0, 166, 81, 0.12);
        }

        [data-testid="stSidebar"] button:hover,
        .stButton > button:hover {
            border-color: rgba(0, 166, 81, 0.95) !important;
            box-shadow: 0 10px 24px rgba(0, 166, 81, 0.18);
        }

        .hero {
            border: 2px solid rgba(126, 232, 170, 0.95);
            background: var(--panel);
            border-radius: 22px;
            padding: 1.2rem 1.3rem 1.1rem 1.3rem;
            box-shadow: 0 0 0 1px rgba(214, 255, 229, 0.9), 0 16px 34px rgba(0, 166, 81, 0.1);
            margin-bottom: 0.9rem;
            animation: popIn 420ms ease-out;
        }

        .agent-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 1rem;
            margin-top: 1rem;
        }

        .agent-card {
            position: relative;
            border: 2px solid transparent;
            background: var(--panel);
            background:
              linear-gradient(#ffffff, #ffffff) padding-box,
                            linear-gradient(120deg, rgba(214, 255, 229, 0.95), rgba(46, 207, 122, 0.82), rgba(0, 166, 81, 0.62), rgba(132, 255, 188, 0.9), rgba(214, 255, 229, 0.95)) border-box;
                        background-size: 100% 100%, 220% 220%;
            border-radius: 22px;
            padding: 1.15rem 1.15rem 1rem 1.15rem;
                        box-shadow: 0 0 0 1px rgba(46, 207, 122, 0.08), 0 16px 34px rgba(0, 166, 81, 0.12);
            min-height: 220px;
                        animation: greenBorderFlow 8s ease-in-out infinite;
                        transition: box-shadow 220ms ease, transform 220ms ease;
        }

        .agent-card:hover {
                        box-shadow: 0 0 0 1px rgba(46, 207, 122, 0.16), 0 18px 36px rgba(0, 166, 81, 0.16);
            transform: translateY(-1px);
        }

        .agent-card h3 {
            margin: 0 0 0.45rem 0;
            font-size: 1.15rem;
            font-weight: 800;
            letter-spacing: -0.02em;
        }

        .agent-card p {
            margin: 0;
            color: var(--muted);
            line-height: 1.45;
        }

        .agent-card .chip-row {
            margin-top: 0.9rem;
            margin-bottom: 1rem;
        }

        .dashboard-link {
            margin-top: 0.35rem;
        }

        .dashboard-link a {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 150px;
            padding: 0.72rem 1rem;
            border-radius: 14px;
            border: 1.5px solid rgba(0, 166, 81, 0.7);
            background: #ffffff;
            color: #007a3d !important;
            text-decoration: none !important;
            font-weight: 700;
            box-shadow: 0 8px 20px rgba(0, 166, 81, 0.12);
        }

        .dashboard-link a:hover {
            border-color: rgba(0, 166, 81, 0.95);
            box-shadow: 0 10px 24px rgba(0, 166, 81, 0.18);
        }

        .hero h1 {
            margin: 0;
            letter-spacing: -0.02em;
            font-weight: 800;
            font-size: clamp(1.35rem, 2.6vw, 2rem);
        }

        .hero p {
            margin: 0.45rem 0 0;
            color: var(--muted);
            line-height: 1.35;
        }

        .chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.8rem;
        }

        .chip {
            border: 1px solid var(--border);
            border-radius: 999px;
            padding: 0.3rem 0.58rem;
            background: #ffffff;
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.75rem;
            color: #1a1a1a;
        }

        [data-testid="stChatMessage"] p,
        [data-testid="stChatMessage"] li,
        [data-testid="stChatMessage"] span,
        [data-testid="stChatMessage"] div:not(pre):not(code) {
            color: #111111 !important;
        }

        [data-testid="stChatMessage"] pre,
        [data-testid="stChatMessage"] pre *,
        [data-testid="stChatMessage"] code,
        [data-testid="stChatMessage"] code * {
            color: #d4d4d4 !important;
            font-family: 'IBM Plex Mono', monospace !important;
        }

        [data-testid="stChatMessage"] pre {
            background: #111827 !important;
            border: 1px solid rgba(148, 163, 184, 0.22) !important;
            border-radius: 12px !important;
            padding: 0.95rem 1rem !important;
            overflow-x: auto !important;
        }

        [data-testid="stChatMessage"] :not(pre) > code {
            background: #e8fff1 !important;
            color: #0f5132 !important;
            border-radius: 6px !important;
            padding: 0.12rem 0.35rem !important;
        }

        [data-testid="stChatMessage"] {
            background: #ffffff !important;
            border: 1px solid var(--border) !important;
            border-radius: 14px !important;
            box-shadow: 0 10px 24px rgba(0, 166, 81, 0.12) !important;
            padding: 0.75rem 0.9rem !important;
            margin-bottom: 0.65rem !important;
            width: 100% !important;
            max-width: 100% !important;
        }

        [data-testid="stChatMessage"][aria-label="assistant"] {
            margin-right: auto !important;
            margin-left: 0 !important;
        }

        [data-testid="stChatMessage"][aria-label="user"],
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
            width: fit-content !important;
            max-width: min(82%, 980px) !important;
            margin-left: auto !important;
            margin-right: 0 !important;
        }

        [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"],
        [data-testid="stChatMessage"] [data-testid="stVerticalBlock"] {
            background: transparent !important;
        }

        [data-testid="stChatInput"] textarea,
        [data-testid="stChatInput"] input,
        [data-testid="stChatInput"] * {
            color: #111111 !important;
            caret-color: #111111 !important;
        }

        [data-testid="stChatInput"] textarea::placeholder,
        [data-testid="stChatInput"] input::placeholder {
            color: rgba(17, 17, 17, 0.62) !important;
            opacity: 1 !important;
        }

        [data-testid="stChatInput"] {
            border: 2px solid transparent;
            border-radius: 12px;
            background:
              linear-gradient(#ffffff, #ffffff) padding-box,
              linear-gradient(110deg, #ff4d4d, #ffd166, #2ecf7a, #4cc9f0, #7b61ff, #ff4d4d) border-box;
            background-size: 100% 100%, 260% 260%;
            animation: rgbBorderFlow 6s linear infinite;
            box-shadow: 0 0 0 1px rgba(0, 166, 81, 0.10), 0 10px 24px rgba(0, 166, 81, 0.16);
        }

        [data-testid="stChatInput"] > div,
        [data-testid="stChatInput"] textarea {
            background: #ffffff !important;
        }

        [data-testid="stChatInput"]:focus-within {
            box-shadow: 0 0 0 2px rgba(0, 166, 81, 0.24), 0 12px 28px rgba(0, 166, 81, 0.22);
        }

        @keyframes rgbBorderFlow {
            0% { background-position: 0% 50%; }
            100% { background-position: 260% 50%; }
        }

        @keyframes greenBorderFlow {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 200% 50%; }
        }

        @keyframes popIn {
            from { opacity: 0; transform: translateY(10px) scale(0.99); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }

        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(12px); }
            to { opacity: 1; transform: translateY(0); }
        }

        @media (max-width: 800px) {
            .hero { padding: 1rem; border-radius: 16px; }
            .hero h1 { font-size: 1.2rem; }
            .hero p { font-size: 0.93rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _create_session() -> str:
    resp = requests.post(f"{API_BASE_URL}/atlas/sessions", timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["session_id"]


def _index_stats() -> dict[str, int]:
    try:
        resp = requests.get(f"{API_BASE_URL}/atlas/index-stats", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {"skills": 0, "testcases": 0}


def _state_key(page_key: str, suffix: str) -> str:
    return f"{page_key}_{suffix}"


def _init_state(page_key: str, load_index_stats: bool) -> None:
    messages_key = _state_key(page_key, "messages")
    session_key = _state_key(page_key, "session_id")
    error_key = _state_key(page_key, "backend_error")
    stats_key = _state_key(page_key, "index_stats")
    if messages_key not in st.session_state:
        st.session_state[messages_key] = []
    if session_key not in st.session_state:
        st.session_state[session_key] = None
    if error_key not in st.session_state:
        st.session_state[error_key] = None
    if load_index_stats and stats_key not in st.session_state:
        st.session_state[stats_key] = _index_stats()


def _ensure_session(page_key: str) -> str:
    session_key = _state_key(page_key, "session_id")
    error_key = _state_key(page_key, "backend_error")
    session_id = st.session_state.get(session_key)
    if session_id:
        return session_id
    try:
        session_id = _create_session()
    except requests.RequestException as exc:
        st.session_state[error_key] = str(exc)
        raise
    st.session_state[session_key] = session_id
    st.session_state[error_key] = None
    return session_id


def _render_backend_warning(page_key: str, agent_name: str) -> None:
    error_key = _state_key(page_key, "backend_error")
    error = st.session_state.get(error_key)
    if error:
        st.warning(f"{agent_name} backend is currently unreachable at {API_BASE_URL}. You can still navigate the UI, but chat requests will fail until the service is back up. Last error: {error}")


def _append_message(page_key: str, role: str, content: str) -> None:
    messages_key = _state_key(page_key, "messages")
    messages: list[dict[str, str]] = st.session_state[messages_key]
    if messages and messages[-1].get("role") == role and messages[-1].get("content") == content:
        return
    messages.append({"role": role, "content": content})


def _render_header(title: str, subtitle: str, chips: list[str]) -> None:
    chip_html = "\n".join(f"<span class=\"chip\">{chip}</span>" for chip in chips)
    st.markdown(
        f"""
        <section class="hero">
          <h1>{title}</h1>
          <p>{subtitle}</p>
          <div class="chip-row">
            {chip_html}
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_messages(page_key: str) -> None:
    messages_key = _state_key(page_key, "messages")
    deduped_messages: list[dict[str, str]] = []
    for msg in st.session_state[messages_key]:
        if deduped_messages and deduped_messages[-1] == msg:
            continue
        deduped_messages.append(msg)

    for msg in deduped_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _run_with_progress(request_fn: Any) -> str:
    steps = [
        "Planning...",
        "Getting Data...",
        "Thinking...",
        "Analyzing...",
        "Formulating response...",
        "Discombobulating...",
    ]
    progress = st.empty()
    answer = ""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(request_fn)
        tick = 0
        while not future.done():
            dots = "." * ((tick % 3) + 1)
            step = steps[tick % len(steps)]
            progress.markdown(step + dots)
            time.sleep(2.0)
            tick += 1
        answer = future.result()
    progress.empty()
    return answer


def configure_app() -> None:
    st.set_page_config(
        page_title="Atlas: Device Agent",
        page_icon="coverage",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()


def _render_sidebar_nav() -> None:
    with st.sidebar:
        st.page_link("streamlit_app.py", label="Dashboard")
        st.markdown("### Agents")
        st.page_link("pages/1_Atlas.py", label="Atlas", icon=":material/precision_manufacturing:")
        st.page_link("pages/2_Cypher.py", label="Cypher", icon=":material/account_tree:")


def render_dashboard() -> None:
    _render_sidebar_nav()
    st.markdown(
        """
        <style>
        [data-testid="stBottomBlockContainer"],
        [data-testid="stChatInput"] {
            display: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _render_header("Agent Console", "", [])
    st.markdown("### Select an agent")

    st.markdown(
        """
        <div class="agent-grid">
            <section class="agent-card">
                <h3>Atlas</h3>
                <p>Coverage, Jenkins, critical-events, and observation-driven Q&A with the current Atlas chat interface.</p>
                <div class="chip-row">
                    <span class="chip">coverage</span>
                    <span class="chip">jenkins</span>
                    <span class="chip">critical-events</span>
                </div>
                <div class="dashboard-link"><a href="./Atlas" target="_self">Open Atlas</a></div>
            </section>
            <section class="agent-card">
                <h3>Cypher</h3>
                <p>Neo4j knowledge graph Q&A assistant.</p>
                <div class="chip-row">
                    <span class="chip">neo4j</span>
                    <span class="chip">workflows</span>
                    <span class="chip">knowledge graph</span>
                </div>
                <div class="dashboard-link"><a href="./Cypher" target="_self">Open Cypher</a></div>
            </section>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_atlas_page() -> None:
    _render_sidebar_nav()
    page_key = "atlas"
    _init_state(page_key, load_index_stats=True)
    stats = st.session_state[_state_key(page_key, "index_stats")]
    chips = [f"skills: {stats['skills']}"]
    if stats.get("testcases"):
        chips.append(f"testcases: {stats['testcases']}")
    chips.append("source: skills + local critical-events DB + Observation data")
    _render_header(
        "Atlas: Device Agent",
        "Ask coverage, Jenkins, or critical-events data questions — powered by Claude.",
        chips,
    )
    _render_backend_warning(page_key, "Atlas")
    st.markdown("### Ask me anything")
    _render_messages(page_key)

    prompt = st.chat_input(
        "Ask about coverage, Jenkins, or critical-events insights...",
        key="atlas_chat_input",
    )
    if not prompt:
        return

    _append_message(page_key, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    def _request() -> str:
        session_key = _state_key(page_key, "session_id")
        session_id = _ensure_session(page_key)
        payload = {"session_id": session_id, "query": prompt}
        resp = requests.post(f"{API_BASE_URL}/atlas/chat", json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            session_id = _create_session()
            st.session_state[session_key] = session_id
            resp = requests.post(
                f"{API_BASE_URL}/atlas/chat",
                json={"session_id": session_id, "query": prompt},
                timeout=REQUEST_TIMEOUT,
            )
        resp.raise_for_status()
        data = resp.json()
        return _normalize_download_links(data["response"])

    with st.chat_message("assistant"):
        try:
            answer = _run_with_progress(_request)
        except requests.RequestException as exc:
            answer = f"Could not reach the Atlas API at {API_BASE_URL}: {exc}"
        st.markdown(answer)

    _append_message(page_key, "assistant", answer)


def render_cypher_page() -> None:
    _render_sidebar_nav()
    page_key = "cypher"
    _init_state(page_key, load_index_stats=False)
    _render_header(
        "Cypher: Knowledge Graph",
        "Ask knowledge-graph questions over the Neo4j-backed Cypher agent.",
        ["source: Neo4j knowledge graph"],
    )
    _render_backend_warning(page_key, "Cypher")
    st.markdown("### Ask me anything")
    _render_messages(page_key)

    prompt = st.chat_input(
        "Ask about the knowledge graph, entities, relationships, or graph-backed insights...",
        key="cypher_chat_input",
    )
    if not prompt:
        return

    _append_message(page_key, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    def _request() -> str:
        session_key = _state_key(page_key, "session_id")
        session_id = _ensure_session(page_key)
        payload = {"session_id": session_id, "query": prompt}
        resp = requests.post(f"{API_BASE_URL}/cypher/query", json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            session_id = _create_session()
            st.session_state[session_key] = session_id
            resp = requests.post(
                f"{API_BASE_URL}/cypher/query",
                json={"session_id": session_id, "query": prompt},
                timeout=REQUEST_TIMEOUT,
            )
        resp.raise_for_status()
        return resp.json()["response"]

    with st.chat_message("assistant"):
        try:
            answer = _run_with_progress(_request)
        except requests.RequestException as exc:
            answer = f"Could not reach the Cypher API at {API_BASE_URL}: {exc}"
        st.markdown(answer)

    _append_message(page_key, "assistant", answer)