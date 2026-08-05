---
name: jira-ticket-summary
description: "Use when: summarizing JIRA tickets for test case analysis, generating test-focused summaries from jira_tickets_data.json, producing jira_tickets_data_SUMMARY.md with Changes and Test Relevance sections. Uses Azure OpenAI LLM via jira_ticket_summary.py."
argument-hint: "path to jira_tickets_data.json"
---

# JIRA Ticket Summary

Generate concise, test-focused summaries of JIRA tickets using Azure OpenAI. Reads a `jira_tickets_data.json` file and produces a structured markdown summary (`jira_tickets_data_SUMMARY.md`) with sections optimized for writing and updating test cases.

## When to Use

- After fetching JIRA ticket data with `jira-confluence-fetch` skill (jira_analyzer.py)
- When you have a `jira_tickets_data.json` and need test-relevant summaries
- Before modifying or creating test cases based on JIRA ticket content
- Any time you need to understand what changed and what to test from JIRA tickets

**Note**: This script runs independently from `jira_analyzer.py`. After fetching tickets, run this as a separate step.

## Prerequisites

- Python 3 with `langchain_openai` and `python-dotenv` installed
- Azure OpenAI credentials in `pytest_device_validator/.env`:
  ```ini
  ENDPOINT_URL=https://<resource>.openai.azure.com/
  AZURE_OPENAI_API_KEY=your-api-key
  DEPLOYMENT_NAME=gpt-4
  API_VERSION=2024-02-15-preview
  ```
- A valid `jira_tickets_data.json` file (produced by `jira_analyzer.py`)

## Script Location

```
pytest_device_validator/src/jira_ticket_summary.py
```

## Procedure

### Step 1 — Verify input file exists

Confirm `jira_tickets_data.json` exists at the expected path (usually `pytest_device_validator/src/jira_tickets_data.json`).

### Step 2 — Run the summarizer

```bash
cd pytest_device_validator/src
python3 jira_ticket_summary.py jira_tickets_data.json
```

Or call it programmatically (this is what jira_analyzer.py does internally):
```python
from jira_ticket_summary import summarize_jira_tickets
output_file = summarize_jira_tickets("jira_tickets_data.json")
```

### Step 3 — Read the output file from disk

The script generates `jira_tickets_data_SUMMARY.md` in the current working directory.

⚠️ **Always read this file from disk** — never use terminal output as the data source for downstream analysis.

## Output Format

The generated `jira_tickets_data_SUMMARY.md` contains one entry per ticket:

```markdown
# JIRA Ticket Summaries

**Generated**: YYYY-MM-DD HH:MM:SS
**Source**: `path/to/jira_tickets_data.json`
**Tickets**: N

---

**Ticket**: BG4-769 – Frame noise from one camera appearing in another camera feed

**About**: Brief 2-3 sentence explanation of the issue/feature and its impact.

**Changes**:
- Bullet list of what was fixed, added, or modified
- Specific code/config/driver changes

**Test Relevance**:
- What to validate (commands, APIs, config keys, expected values)
- Edge cases to cover
- Device/environment specifics

---

**Ticket**: BG4-754 – ...
[repeats for each ticket]
```

## Key Sections for Test Case Writing

When reading `jira_tickets_data_SUMMARY.md`, focus on these sections per ticket:

| Section | Use For |
|---------|---------|
| **About** | Understanding context — what the test should cover |
| **Changes** | What was modified — determines if test needs rework, enhancement, or is new |
| **Test Relevance** | Direct input for `acceptance_criteria` in test cases — commands, assertions, config keys |

## How It Maps to Test Cases

The **Test Relevance** bullets from the summary directly translate to `acceptance_criteria` entries in test cases:

The **Changes** section helps determine the `pre_steps` and `services`:
- If changes mention config keys → need config override INI + `pre_steps: reboot`
- If changes mention a specific service → add to `services` list
- If changes are driver/firmware level → may need `pre_steps: reboot`

## Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `Secrets file not found` | Missing Azure OpenAI credentials | Add ENDPOINT_URL, AZURE_OPENAI_API_KEY, DEPLOYMENT_NAME to `pytest_device_validator/.env` |
| `FileNotFoundError` on input | JSON file doesn't exist | Run `jira_analyzer.py` with ticket keys first to generate it |
| LLM timeout / rate limit | Azure OpenAI throttling | Retry after a few seconds; check API quota |

## Important Rules

1. **Read output from disk only** — `jira_tickets_data_SUMMARY.md` is the authoritative source, not console output
2. **One run per batch** — the script processes all tickets in the JSON file at once
3. **Separate execution** — `jira_analyzer.py` no longer calls this automatically; always run it as a separate step
4. **Idempotent** — running again overwrites `jira_tickets_data_SUMMARY.md` with fresh summaries
