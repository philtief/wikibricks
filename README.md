# WikiBricks

**A wiki for your AI agent, on Databricks.** Delta + Vector Search + Unity
Catalog, exposed as native MCP tools. One `databricks bundle deploy` and your
agent has a persistent, versioned, typed-link knowledge store that **grows
from its own answer traces** via a nightly maintenance job.

> **Grounding ideas.**
> 1. Andrej Karpathy's
>    [LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
>    — instead of re-retrieving raw documents at every query, the agent
>    incrementally compiles a structured, interlinked wiki it maintains
>    itself. Knowledge **compounds** instead of getting re-derived.
> 2. *[Context and Memory for Agents on Databricks](https://medium.com/@philipp.tiefenbacher_42173/context-and-memory-for-agents-on-databricks-f3c945cd8681)*
>    — the reusable-memory-pattern argument: agent memory should be built
>    from Databricks-native primitives (Delta, Unity Catalog, Vector Search,
>    Model Serving, MCP) rather than a bespoke side-car, so every deployment
>    inherits governance, lineage, and scale-to-zero for free.
>
> WikiBricks is the concrete intersection: Karpathy's compile-don't-retrieve
> pattern, implemented on the Databricks stack.

## Why

Agents forget. Context windows are not memory, pasted docs are not memory,
embeddings alone are not memory. What you actually want is what humans use:

- **pages** with stable paths and titles
- **typed links** between them (not a generic `related_to` bag)
- **history** — who wrote what, when, and what changed
- **search** by meaning or keyword
- **growth** — the store gets *better* as the agent answers more questions

WikiBricks delivers all five on managed Databricks services. No bespoke vector
DB, no separate MCP server, no model dependency inside the library core —
the agent calling the wiki is the only LLM in the loop.

## The maintenance loop — what makes this a wiki and not a log

Every deployment ships one Lakeflow Job (`wikibricks_curate`) that runs daily.
Two tasks, both optional-to-edit:

1. **`curate`** — deterministic, LLM-free. Proposes new typed edges via Vector
   Search nearest-neighbor + exact-title matching, tagged with `confidence` +
   `origin ∈ {auto-vs, auto-title}`. Auto-commits anything above
   `auto_commit_threshold=0.85`; leaves the rest for the agent to decide on its
   next call. Runs lint (orphans, stale pages, duplicates, broken links) and
   deterministic link repair. This is the library contract.
2. **`promote`** — opt-in, trace-driven. Mines agent session traces, clusters
   recurring questions, has `databricks-claude-sonnet-4-5` synthesize one
   canonical answer per cluster, scores it with an LLM judge, and writes
   passing clusters to `promoted/<slug>` with `cites` edges back to the source
   pages. Drop the `promote` task block in `resources/wiki_curate_job.yml` to
   run fully LLM-free.

Agent traces flow in. Canonical wiki pages come out. The knowledge store
compounds.

**Operational telemetry.** Every write, promote decision, and index sync
appends a row to `wiki_log` with an `op_type`. The useful ones to watch:

| `op_type` | What it means |
|---|---|
| `promote` | A cluster passed the judge and was written to `promoted/<slug>` |
| `promote_reject` | Judge score below `judge_threshold` (default 4.0) — legitimate low quality |
| `promote_parse_fail` | Judge returned non-numeric text — prompt drift, investigate |
| `vs_sync` / `vs_sync_fail` | `sync_index()` triggered a DELTA_SYNC refresh |
| `verify_fix` | Deterministic link repair healed a broken edge |

Before trusting a scheduled promote run, `scripts/diagnose_traces.py
--window-days 7` reports trace volume, query-length percentiles, exact-match
cluster eligibility, and `wiki_log` event counts — the minimum operators
need to know the pipeline has real traffic to work with.

## Quick start

Prerequisites: a Databricks workspace with Unity Catalog, a SQL warehouse, and
a Vector Search endpoint.

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
databricks bundle deploy --target dev
databricks bundle run deploy_wiki_store --target dev
```

MCP endpoint after deploy:
`https://<workspace>/api/2.0/mcp/functions/<catalog>/<schema>`. Point any MCP
client at it.

Override per target:

```bash
databricks bundle deploy --target dev \
  --var="catalog=my_catalog" --var="schema=wiki" \
  --var="warehouse_id=abc123" --var="vs_endpoint=my-vs-endpoint"
```

The Streamlit app reads the same config from env vars so one image ships to
any workspace:

| Variable | Default |
|---|---|
| `WIKIBRICKS_WAREHOUSE_ID` | `41754a8563a43a49` |
| `WIKIBRICKS_VS_INDEX` | `agent_marketplace_catalog.wiki.pages_index` |
| `WIKIBRICKS_LLM_MODEL` | `databricks-claude-sonnet-4-5` |

`resources/app.yml` wires these from the bundle's `catalog` / `schema` /
`warehouse_id` vars automatically.

## Core API

```python
from wikibricks import WikiClient

wiki = WikiClient(warehouse_id="abc123")

wiki.write_page(
    "topics/vector-search",
    title="Vector Search",
    content={"summary": "...", "body": "..."},
    tags=["retrieval"],
)

# Agent-in-the-loop: WikiBricks proposes, agent chooses, WikiBricks commits.
candidates = wiki.propose_edges("topics/vector-search", min_similarity=0.70)
wiki.commit_edges([c for c in candidates if my_agent_approves(c)])

wiki.graph_neighbors("topics/vector-search", depth=2)   # 1–3 hop SQL BFS
wiki.search("what index modes exist", mode="HYBRID")    # HYBRID / ANN / FULL_TEXT
wiki.history("topics/vector-search")                    # versioned writes
```

Full surface: [`src/wikibricks/client.py`](src/wikibricks/client.py).

## MCP tools

Seven UC functions auto-exposed as MCP tools — agents discover them on
connect.

| Tool | Description |
|---|---|
| `fn_wiki_search(question, mode)` | HYBRID / ANN / FULL_TEXT over `pages` |
| `fn_wiki_read(page_path)` | Read a page by path |
| `fn_wiki_history(page_path)` | Full version history |
| `fn_wiki_log(num_entries)` | Recent operation log |
| `fn_wiki_index()` | Page catalog |
| `fn_wiki_schema()` | Conventions (page types, link types, tag vocabulary) |
| `fn_wiki_write_help()` | How to write good wiki pages |

Auth: OAuth, `unity-catalog` scope. UC permissions enforced.

## Evaluation

Two external benchmarks, both honest:

- **HotpotQA retrieval pilot** — 500 queries, HYBRID recall@10 ≈ **89%** on a
  66,569-page corpus. Retrieval-only, not a leaderboard metric. See
  [`docs/hotpotqa_evaluation.md`](docs/hotpotqa_evaluation.md).
- **2WikiMultiHopQA (official v1.1 eval)** — 350-query ablation. Best variant
  (Sonnet 4.6 + HYBRID + K=10) reaches Joint F1 **21.2** — on par with the
  2020 paper's own open-retrieval baseline (~20). Modern task-tuned SOTA is
  50–65 and outside WikiBricks' scope. See
  [`docs/twowiki_evaluation.md`](docs/twowiki_evaluation.md).

## Development

```bash
uv sync
uv run pytest                       # 305 tests, no workspace needed
uv run ruff check src tests scripts
uv build                            # → dist/wikibricks-0.1.4-py3-none-any.whl
```

## What this is not

- Not a multi-hop QA system — the agent does the reasoning.
- Not a vector DB product — the index is Databricks Vector Search.
- Not a SaaS — it's a Databricks Asset Bundle that deploys into your workspace.
- Not scratch memory — per-session conversation state belongs elsewhere.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
