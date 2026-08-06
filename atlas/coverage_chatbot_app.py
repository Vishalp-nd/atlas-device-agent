"""
coverage_chatbot_app.py - Streamlit chatbot for framework coverage Q&A.

This is a thin HTTP client for the Atlas agent's API (atlas/router.py,
mounted under /atlas by atlas/main.py) — it holds no LangChain/LangGraph state
itself. Conversation history and intent tracking live server-side, keyed by a
session_id obtained from POST /atlas/sessions.

Run:
    streamlit run atlas/coverage_chatbot_app.py

Configure the API location with the ATLAS_API_URL env var (default shown below).
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests
import streamlit as st

API_BASE_URL = os.environ.get("ATLAS_API_URL", "http://localhost:8000").rstrip("/")
REQUEST_TIMEOUT = 900


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

        /* Remove dark backdrop strip behind the floating chat input area */
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

        [data-testid="stSidebar"] {
            background: #ffffff !important;
            border-right: 1px solid var(--border);
            box-shadow: 8px 0 24px rgba(0, 166, 81, 0.08);
        }

        [data-testid="stSidebar"] * {
            color: #111111;
        }

        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] label {
            color: #007a3d !important;
        }

        /* Keep sidebar show/hide control visible even when not hovered */
        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="collapsedControl"] *,
        [data-testid="stSidebarCollapsedControl"] *,
        div:has(> button[kind="header"]) {
            opacity: 1 !important;
            visibility: visible !important;
        }

        button[kind="header"],
        button[aria-label="Open sidebar"],
        button[aria-label="Close sidebar"],
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
        button[aria-label="Open sidebar"]:hover,
        button[aria-label="Close sidebar"]:hover,
        [data-testid="collapsedControl"] button:hover,
        [data-testid="stSidebarCollapsedControl"] button:hover,
        button[kind="header"]:focus,
        button[aria-label="Open sidebar"]:focus,
        button[aria-label="Close sidebar"]:focus,
        [data-testid="collapsedControl"] button:focus,
        [data-testid="stSidebarCollapsedControl"] button:focus {
            background: #ffffff !important;
            color: #007a3d !important;
            border-color: rgba(0, 166, 81, 0.95) !important;
            box-shadow: 0 10px 24px rgba(0, 166, 81, 0.18) !important;
            opacity: 1 !important;
        }

        button[kind="header"] svg,
        button[aria-label="Open sidebar"] svg,
        button[aria-label="Close sidebar"] svg,
        [data-testid="collapsedControl"] button svg,
        [data-testid="stSidebarCollapsedControl"] button svg {
            fill: #007a3d !important;
            color: #007a3d !important;
            stroke: #007a3d !important;
            opacity: 1 !important;
        }

        button[kind="header"] svg path,
        button[aria-label="Open sidebar"] svg path,
        button[aria-label="Close sidebar"] svg path,
        [data-testid="collapsedControl"] button svg path,
        [data-testid="stSidebarCollapsedControl"] button svg path {
            fill: #007a3d !important;
            stroke: #007a3d !important;
            opacity: 1 !important;
        }

        button[kind="header"]:before,
        button[aria-label="Open sidebar"]:before,
        button[aria-label="Close sidebar"]:before,
        button[kind="header"]:after,
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
            border: 1px solid var(--border);
            background: var(--panel);
            border-radius: 22px;
            padding: 1.2rem 1.3rem 1.1rem 1.3rem;
            box-shadow: var(--shadow);
            margin-bottom: 0.9rem;
            animation: popIn 420ms ease-out;
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

        /* Uniform chat message cards (user + assistant) */
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

        /* Keep RGB animated border around prompt input */
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


def _post_chat(session_id: str, query: str) -> tuple[str, str]:
    """POST /atlas/chat. If the session has expired server-side, transparently
    get a new one and retry once instead of surfacing a raw 404 to the user."""
    payload = {"session_id": session_id, "query": query}
    resp = requests.post(f"{API_BASE_URL}/atlas/chat", json=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        session_id = _create_session()
        st.session_state.session_id = session_id
        resp = requests.post(
            f"{API_BASE_URL}/atlas/chat",
            json={"session_id": session_id, "query": query},
            timeout=REQUEST_TIMEOUT,
        )
    resp.raise_for_status()
    data = resp.json()
    return data["response"], data["intent"]


def _index_stats() -> dict[str, int]:
    try:
        resp = requests.get(f"{API_BASE_URL}/atlas/index-stats", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {"skills": 0, "testcases": 0}


def _init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = _create_session()
    if "index_stats" not in st.session_state:
        st.session_state.index_stats = _index_stats()


def _append_message(role: str, content: str) -> None:
    """Avoid duplicate consecutive messages from reruns/retries."""
    messages: list[dict[str, str]] = st.session_state.messages
    if messages and messages[-1].get("role") == role and messages[-1].get("content") == content:
        return
    messages.append({"role": role, "content": content})


def _render_header(stats: dict[str, int]) -> None:
    # testcases live in the device-automation repo; the chip only appears when
    # the API found a checkout via DEVICE_AUTOMATION_ROOT.
    chips = [f"<span class=\"chip\">skills: {stats['skills']}</span>"]
    if stats.get("testcases"):
        chips.append(f"<span class=\"chip\">testcases: {stats['testcases']}</span>")
    chips.append("<span class=\"chip\">source: skills + local critical-events DB + Observation data </span>")
    chip_html = "\n".join(chips)

    st.markdown(
        f"""
        <section class="hero">
          <h1>Atlas: Device Agent</h1>
                    <p>Ask coverage, Jenkins, or critical-events data questions — powered by Claude.</p>
          <div class="chip-row">
                        {chip_html}
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="Atlas: Device Agent",
        page_icon="coverage",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()
    _init_state()

    _render_header(st.session_state.index_stats)
    st.markdown("### Ask me anything")

    deduped_messages: list[dict[str, str]] = []
    for msg in st.session_state.messages:
        if deduped_messages and deduped_messages[-1] == msg:
            continue
        deduped_messages.append(msg)

    for msg in deduped_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Ask about coverage, Jenkins, or critical-events insights...")
    if not prompt:
        return

    _append_message("user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        progress = st.empty()
        steps = [
            "Planning...",
            "Getting Data...",
            "Thinking...",
            "Analyzing...",
            "Formulating response...",
            "Discombobulating..."
        ]

        answer = ""
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_post_chat, st.session_state.session_id, prompt)

            tick = 0
            while not future.done():
                dots = "." * ((tick % 3) + 1)
                step = steps[tick % len(steps)]
                progress.markdown(step + dots)
                time.sleep(2.0)
                tick += 1

            try:
                answer, _intent = future.result()
            except requests.RequestException as exc:
                answer = f"Could not reach the Atlas API at {API_BASE_URL}: {exc}"

        progress.empty()
        st.markdown(answer)

    _append_message("assistant", answer)


if __name__ == "__main__":
    main()
