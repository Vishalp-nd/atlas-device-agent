"""
Quick verification script for the Jenkins agent.
Run from the repo root, atlas-device-agent/:
    python scripts/verify_jenkins.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from atlas.jenkins_agent_graph import _get_jenkins_client, _make_tools, run_jenkins_agent
from atlas.coverage_chatbot_core import load_agent_system_prompt


def step(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


# ── Step 1: Raw connection ────────────────────────────────────────
step("1. Jenkins connection")
try:
    client = _get_jenkins_client()
    version = client.get_version()
    print(f"Connected. Jenkins version: {version}")
except Exception as e:
    print(f"FAILED: {e}")
    sys.exit(1)


# ── Step 2: list_jenkins_jobs tool ───────────────────────────────
step("2. list_jenkins_jobs tool (no filter)")
tools = {t.name: t for t in _make_tools()}
result = tools["list_jenkins_jobs"].invoke({"filter": ""})
print(result[:800])  # truncate if many jobs


# ── Step 3: list_jenkins_jobs with filter ────────────────────────
filter_word = input("\nEnter a filter keyword to narrow job list (or press Enter to skip): ").strip()
if filter_word:
    step(f"3. list_jenkins_jobs filter='{filter_word}'")
    result = tools["list_jenkins_jobs"].invoke({"filter": filter_word})
    print(result)


# ── Step 4: get_job_parameters ───────────────────────────────────
job_name = input("\nEnter a job name to inspect parameters (or press Enter to skip): ").strip()
if job_name:
    step(f"4. get_job_parameters('{job_name}')")
    result = tools["get_job_parameters"].invoke({"job_name": job_name})
    print(result)


# ── Step 5: Full agent run ────────────────────────────────────────
query = input("\nEnter a natural language query for the Jenkins agent (or press Enter to skip): ").strip()
if query:
    step("5. run_jenkins_agent end-to-end")
    system_prompt = load_agent_system_prompt(REPO_ROOT, "jenkins-agent")
    answer = run_jenkins_agent(query, system_prompt)
    print(answer)
