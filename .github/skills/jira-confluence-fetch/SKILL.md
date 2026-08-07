---
name: jira-confluence-fetch
description: "Use when: fetching OS release changelogs from Atlassian Confluence, retrieving JIRA ticket details by key, extracting release notes/features/enhancements/bug-fixes from a Confluence page. Runs jira_analyzer.py to produce raw_confluence_data and jira_tickets_data files."
argument-hint: "page URL, release name, or ticket keys (e.g., 'https://...atlassian.net/wiki/spaces/BSP/pages/123/Title' or 'BG4-769 BG4-754')"
---

# JIRA & Confluence Data Fetch

Fetch raw data from Atlassian Confluence release pages and/or JIRA ticket details using `jira_analyzer.py`. This skill handles credential loading, API calls, and file output — the caller just provides a release name or ticket keys.

## When to Use

- Fetch a Confluence page by its **URL** (any page link the user provides)
- Fetch a Confluence release page by **title** (release notes, features, enhancements, bug fixes, changelog)
- Retrieve detailed JIRA ticket data by ticket key(s) (e.g., BG4-769)
- Any workflow that needs raw Confluence or JIRA data before analysis (e.g., release-analyzer agent)

## Prerequisites

- Python 3 with `requests` and `python-dotenv` installed
- Credentials in `pytest_device_validator/jira_config.ini` (preferred):
  ```ini
  [JIRA]
  server = https://netradyne.atlassian.net
  username = your-email@netradyne.com
  api_token = your-api-token-here
  ```
  `jira_analyzer.py` reads `jira_config.ini` first and falls back to a
  `pytest_device_validator/.env` file (`JIRA_URL` / `JIRA_EMAIL` /
  `JIRA_API_TOKEN`) if the INI is missing or incomplete.
- Network access to Atlassian (Confluence + JIRA REST APIs)

## Script Location

```
pytest_device_validator/src/jira_analyzer.py
```

Always `cd` into the `src/` directory before running:
```bash
cd pytest_device_validator/src
```

## Input Modes

### Mode 1a: Confluence Page by URL (preferred)

Fetches a specific Confluence page directly by its URL. Use this when the user provides a page link.

**Input**: A full Confluence page URL

```bash
python3 jira_analyzer.py 'https://netradyne.atlassian.net/wiki/spaces/BSP/pages/123456789/OS+Release+-+D470.05.02.00'
```

**Supported URL format**: `https://<domain>/wiki/spaces/<SPACE>/pages/<PAGE_ID>/...`

The script extracts the page ID from the URL and fetches that exact page — no search needed, no BSP space restriction.

**Output file**: `raw_confluence_data_<Page_Title>.txt`

### Mode 1b: Confluence Page by Title

Searches the BSP Confluence space for pages matching the given title.

**Input**: A release name or page title (e.g., `OS Release - D470.05.02.00`, `D470.05.02.00`)

```bash
python3 jira_analyzer.py 'OS Release - D470.05.02.00'
```

**Output file**: `raw_confluence_data_OS_Release_-_D470.05.02.00.txt`
- Contains the full page content: title, page ID, URL, last modified info, and raw text
- Spaces in the release name are replaced with `_` in the filename

**What to extract from the output file**:
- Release Notes / summary section
- Features — new capabilities
- Enhancements — improvements to existing functionality
- Bug Fixes — issues resolved
- JIRA ticket keys (regex: `[A-Z]+-\d+`) for follow-up ticket fetch

### Mode 2: JIRA Ticket(s)

Fetches detailed data for one or more JIRA tickets.

**Input**: One or more ticket keys as separate arguments

```bash
python3 jira_analyzer.py 'BG4-769' 'BG4-754' 'BG4-788'
```

⚠️ **BATCH all tickets in ONE command** — do not make separate calls per ticket.

**Output file**:
- `jira_tickets_data.json` — raw ticket data (key, summary, status, type, priority, assignee, reporter, labels, components, fixVersions, description, URL)

⚠️ **Summaries are NOT generated automatically.** After this completes, run `jira_ticket_summary.py` separately (see the **jira-ticket-summary** skill) to produce `jira_tickets_data_SUMMARY.md`.

**What each ticket contains**:
| Field | Description |
|-------|-------------|
| `key` | Ticket key (e.g., BG4-769) |
| `summary` | One-line title |
| `type` | Bug, Story, Task, etc. |
| `status` | Current status |
| `priority` | Priority level |
| `components` | Affected components (maps to test files) |
| `labels` | Labels for categorization |
| `fixVersions` | Release versions this is fixed in |
| `description` | Full ticket description |
| `url` | Direct link to the JIRA ticket |

## Typical Two-Step Flow

For release analysis, run both modes in sequence:

### Step 1 — Fetch Confluence page
```bash
cd pytest_device_validator/src
# By URL (preferred — when user provides a link):
python3 jira_analyzer.py 'https://netradyne.atlassian.net/wiki/spaces/BSP/pages/123456/OS+Release+-+D470.05.02.00'
# Or by title:
python3 jira_analyzer.py 'OS Release - D470.05.02.00'
```
Read the generated `raw_confluence_data_*.txt` file from disk.

### Step 2 — Extract ticket keys and fetch details
Read the Confluence data file. Extract all unique JIRA ticket keys using regex `[A-Z]+-\d+`. Then fetch them all in one batch:
```bash
python3 jira_analyzer.py 'BG4-769' 'BG4-754' 'BG4-788' 'BG4-792'
```
This produces `jira_tickets_data.json`.

### Step 3 — Generate summaries (separate script)
Run the summarizer as a separate step:
```bash
python3 jira_ticket_summary.py jira_tickets_data.json
```
This produces `jira_tickets_data_SUMMARY.md`. Read it from disk for downstream analysis.

## Output File Locations

All output files are written to `pytest_device_validator/src/` (the working directory):

| File | When Created |
|------|--------------|
| `raw_confluence_data_<PAGE_TITLE>.txt` | Mode 1a/1b (Confluence fetch) |
| `jira_tickets_data.json` | Mode 2 (ticket fetch) |

Note: `jira_tickets_data_SUMMARY.md` is generated separately by the **jira-ticket-summary** skill.

## Important Rules

1. **Always read output files from disk** — do NOT rely on terminal/console output for downstream analysis
2. **Batch all tickets in one command** — never call jira_analyzer.py separately per ticket
3. **Run from `pytest_device_validator/src/`** — the script resolves paths relative to its own location
4. **Check credentials first** — if the script fails with a connection error, verify `pytest_device_validator/jira_config.ini` (or `.env`) has valid JIRA tokens

## Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `Secrets file not found` | Missing credentials in `.env` | Add credentials to `pytest_device_validator/jira_config.ini` ([JIRA] section) or `.env` |
| `Failed to connect to Jira` | Invalid credentials or network | Verify API token and network access |
| `No pages found` | Release name doesn't match any Confluence page | Try the direct page URL instead, or adjust the title format |
| `Failed to fetch ticket` | Invalid ticket key | Verify the ticket key exists in JIRA |
