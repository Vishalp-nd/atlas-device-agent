# cypher — device-QA knowledge-graph agent

Answers questions about the Neo4j graph that models the device QA workflow.
Read-only: it cannot modify the graph, trigger tests, or touch devices.

```
(Company)-[:HAS_PRODUCT]->(ProductLine)-[:CONTAINS]->(Service)
    -[:HAS_FEATURE]->(Feature)-[:HAS_FLOW]->(Flow)
(Flow)-[:REQUIRES_CONDITION|EXCLUDES_CONDITION]->(DeviceCondition)
(Flow)-[:IS_DEPENDENT_ON]->(Flow)
```

## Layout

| File | Role |
|---|---|
| `config.py` | env/settings, skill roots, cached prompt loader |
| `graph_client.py` | driver lifecycle; every query runs in a READ transaction |
| `cypher_guard.py` | static validation of model-authored Cypher |
| `graph_queries.py` | the curated Cypher, LLM-free and unit-testable |
| `tools.py` | LangChain tool wrappers + the `run_cypher` escape hatch |
| `agent_graph.py` | LangGraph ReAct loop (`llm ⇄ tools`, max 12 iterations) |
| `skill_resolver.py` | tolerant SKILL.md path resolution |
| `router.py` | FastAPI surface, mounted at `/cypher` by `atlas/main.py` |
| `scripts/repair_skill_paths.py` | dry-run-by-default graph path repair |
| `scripts/audit_skill_paths.py` | reports skill-path drift, read-only |
| `scripts/check_health.py` | Neo4j connectivity/version/count check |
| `scripts/create_fulltext_indexes.py` | optional fulltext indexes for search |

System prompt: `prompts/cypher-graph.agent.md` (frontmatter stripped, bind-mount editable).

## Endpoint

One route, on purpose — everything else the agent can do (schema inspection,
overview, coverage gaps, raw Cypher) is a *tool* it calls internally, not a
separate HTTP surface. Ask for it in plain language instead of hitting a
dedicated endpoint.

| Method | Path | Purpose |
|---|---|---|
| POST | `/cypher/query` | ask a question; optional `session_id` for follow-ups |

`session_id` comes from `POST /atlas/sessions` and is shared with the atlas agents.

```bash
curl -s -X POST localhost:8000/cypher/query \
  -H 'content-type: application/json' \
  -d '{"query":"How do I validate haptic recovery from a SIGABRT crash?"}' | jq -r .response
```

For connectivity/ops checks, run the scripts directly instead of curling an
endpoint — see Maintenance below.

## Read-only enforcement

Three independent layers, because one regex is not a security boundary:

1. **Neo4j READ transaction** — the real boundary. The server rejects every write
   with `Neo.ClientError.Statement.AccessMode`. Verified against this instance.
2. **`cypher_guard`** — covers what access mode does *not*: `apoc.load.*`,
   `apoc.import.*` and `LOAD CSV` are *reads*, so the server permits them, and
   they fetch arbitrary URLs and local files (SSRF / exfiltration). Also blocks
   `dbms.*` and restricts `CALL` to a read allowlist. Keyword scanning strips
   string literals and comments first, so a description containing "create"
   doesn't trip it.
3. **Row cap + query timeout** — `CYPHER_MAX_ROWS` (default 200, hard cap 1000)
   and `CYPHER_TIMEOUT_SECONDS` (default 15).

Neo4j Community has no fine-grained RBAC, so a read-only DB user is not an
option here; if this ever moves to Enterprise, add one as a fourth layer.

## Config

Required in `.env`: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`,
`ANTHROPIC_API_KEY`.
Optional: `NEO4J_DATABASE` (default `neo4j`), `CLAUDE_MODEL`, `CYPHER_MAX_ROWS`,
`CYPHER_TIMEOUT_SECONDS`, `DEVICE_AUTOMATION_ROOT`.

Missing Neo4j settings raise a clear `EnvironmentError` from `/cypher/query`
(503) rather than crashing app startup; `python -m cypher.scripts.check_health`
reports the same thing from the command line.

## Known data caveats

The agent is instructed about each of these; they are properties of the graph,
not bugs in the agent.

- **Coverage is uneven.** Only `haptic_feedback` has flows (51). The other 12
  features have zero — a modelling gap, not proof anything is untested.
- **`IS_DEPENDENT_ON` is not an ordering.** All 438 edges are mirrored
  (219 symmetric pairs), so it means "related to". It cannot yield an execution
  order. Recommended fix: relabel to `RELATED_TO` and model real prerequisites
  separately as a directed acyclic relationship. Not done here — inventing a
  direction needs domain knowledge.
- **`OTA_valid_from` is free-form** — sometimes a per-SKU JSON map, sometimes
  `"ALL"`, sometimes prose. Never version-filtered in Cypher.
- **`valid_device_skus` mixes conventions** — SKU codes (`D450`) and product-line
  names (`bagheera3`). `applicable_flows` matches both.
- **`flow_number` is not unique-constrained** (only `name` is), and numbering
  restarts per feature, so sub-queries key on `name`.
- **5 feature skill paths have no file on disk** (the `bagheera-*-privacy`
  variants). Genuine authoring gaps, deliberately left in place.
- **Flows 48–51 lack `flow_type` and `automated`.** Reported under
  `flows_missing_properties`, kept separate from `flows_not_automated`.

## Maintenance

```bash
python -m cypher.scripts.check_health                        # is Neo4j reachable?
python -m cypher.scripts.audit_skill_paths                   # where has it drifted?
python -m cypher.scripts.repair_skill_paths                  # dry run
python -m cypher.scripts.repair_skill_paths --apply
python -m cypher.scripts.create_fulltext_indexes --apply     # optional
python -m pytest cypher/tests -q
```

Back the graph up before any `--apply`.
