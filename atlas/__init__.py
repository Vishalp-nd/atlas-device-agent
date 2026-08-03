"""atlas/ — the Atlas Device Agent: LangGraph supervisor + sub-agents over HTTP.

Hosts the Atlas chat agent (supervisor routing to coverage, Jenkins, and
critical-events sub-agents) as a FastAPI service, plus a Streamlit chat UI.
The critical-events data pipeline that feeds the local Postgres lives in
pipeline/ at the repo root.

Contains:
  - main.py   — the root FastAPI app mounting the Atlas router.
  - router.py — API routes for the supervisor and direct sub-agent access.

Run the API service (from the repo root, atlas-device-agent/):
    python -m atlas serve

Run the Streamlit UI:
    streamlit run atlas/coverage_chatbot_app.py
"""
