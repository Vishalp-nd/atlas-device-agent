# Agent mode

A chat with Claude about **one** run or comparison, opened with the **Agent mode** button
next to *Run again* / *Open comparison report* (finished runs and comparisons only). The
run's details move to the left, the chat takes the right; the new-analysis form and
Report Comparison are hidden until you leave (× or *Exit agent mode*).

## Who can use it

Sign-in is DeviceQAI's own (`deviceqa_auth` in a device-automation checkout): an emailed
6-digit code for `@netradyne.com` addresses on the DeviceQAI allowlist, then the person's
Claude token from `claude setup-token`, checked live and stored encrypted. Every answer is a
Claude Code run **on this server with that person's token**, so it counts against their own
Claude plan. Runs share DeviceQAI's caps: `DQA_RUNS_PER_USER` (2) per person and
`DQA_RUNS_MAX` (10) on the server; the rest queue.

Add people on DeviceQAI's `/admin` page, or:

```bash
docker exec -w /deviceqa atlas-agent-api python -m deviceqa_auth allow someone@netradyne.com
#            ^ /device-automation when DEVICEQA_HOST is the same checkout as DEVICE_AUTOMATION_HOST
```

Until `LOGIN_MAIL_SEND=1` (and `SMTP_*`) is set in that checkout's
`pytest_device_validator/.env`, codes are printed to the backend's console
(`docker logs atlas-agent-api`), as on DeviceQAI.

## What the agent can see

Nothing but this run (or both runs of a comparison):

- **Logs**, through its own tools only (`log_tools.py`, an MCP server): `scope`,
  `list_log_files`, `search_logs` (grep, regex or plain, per day or across days, counts),
  `read_log` (by line) and `read_log_by_time` (by epoch-ms window). Each takes a device and a
  date and refuses anything outside the run's devices and **its dates ± 1 day** — the same
  days the analysis reads. A device-day is rebuilt from its archives exactly as the analysis
  does (`lib/log_archive.merge_archives`), on first use, into the chat's cache on disk
  (`LA_AGENT_CACHE_DIR`, default `OUTPUT/agent_cache`; at most `LA_AGENT_CACHE_DAYS` = 4 days
  per chat, dropped after `LA_AGENT_CACHE_HOURS` = 2 h unused). ~600 MB and ~3 s per day.
- **Reports**, read-only: the run's `reports/` folder (and a comparison's `compare/<id>/`).

No shell, no web, no edits, no other files; `.env`, keys and credentials are denied by rule,
and Claude's environment carries no server secrets.

## Server setup

The `atlas-agent-api` image installs Claude Code (Dockerfile). `make docker-run-detached`
mounts what agent mode needs:

| Make variable | Default | What |
|---|---|---|
| `DEVICEQA_HOST` | `DEVICE_AUTOMATION_HOST` | device-automation checkout **with `deviceqa_auth`** |
| `DQA_AUTH_DIR` | `~/.deviceqa` | DeviceQAI's sign-in store — use the platforms' one to share people and tokens |
| `DQA_TOKEN_KEY_FILE` | `~/.deviceqa-token.key` | key that encrypts tokens — the platforms' one, or a new one is made in the store |

```bash
make docker-run-detached DEVICEQA_HOST=/path/to/device-automation \
     DQA_AUTH_DIR=/data/nd_test_bot_ws/deviceqa-auth \
     DQA_TOKEN_KEY_FILE=/data/nd_test_bot_ws/deviceqa-token.key
```

The container runs as root: if the DeviceQAI platforms run as another user on the same
store, make sure both can read and write it.

## Files

```
log_analyzer/agent/
  router.py     /log-analyzer/agent API: sign-in, token, chats (bearer-token sessions)
  auth.py       loads deviceqa_auth; sessions, activity log, run slots
  scope.py      the devices / dates / report folders of a run or comparison
  store.py      chats on disk: OUTPUT/log_analyzer_runs/agent/<chat>/ (0700)
  turn.py       one answer: `claude -p … --resume <session>` with the person's token
  log_tools.py  the log tools (MCP, stdio)
log_analyzer/ui/agent.js, agent.css   the chat
```
