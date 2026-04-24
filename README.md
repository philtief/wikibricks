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
a Vector Search endpoint. Databricks CLI configured (`databricks configure` or
a profile in `~/.databrickscfg`).

**1. Clone and configure for your workspace.**

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
cp databricks.override.example.yml databricks.override.yml
# edit databricks.override.yml with your host, profile, catalog, warehouse_id
```

`databricks.override.yml` is gitignored. The Databricks CLI merges it on top
of `databricks.yml` automatically — no extra flags. Fields you will typically
edit:

| Setting | Where | What to put |
|---|---|---|
| Workspace host | `workspace.host` | `https://<your-workspace>.cloud.databricks.com` |
| CLI profile | `workspace.profile` | Name from `~/.databrickscfg` |
| UC catalog | `variables.catalog` | A catalog you can CREATE SCHEMA in |
| SQL warehouse | `variables.warehouse_id` | A warehouse you have CAN_USE on |

Optional per-workspace tweaks (edit `databricks.yml` directly or add them to
the override file): `vs_endpoint`, `llm_model`, `embed_model`,
`auto_commit_threshold`, `cluster_threshold`, `judge_threshold`,
`seed_domain` (`sample` | `custom` | `none`).

**2. Deploy.**

```bash
databricks bundle deploy --target dev
databricks bundle run deploy_wiki_store --target dev
```

The `deploy_wiki_store` notebook creates the schema, Delta tables, Vector
Search index, and UC functions. Subsequent `bundle deploy` calls push code
changes only.

**3. Point your agent at the MCP endpoint.**

`https://<workspace>/api/2.0/mcp/functions/<catalog>/<schema>` — OAuth,
`unity-catalog` scope, UC permissions enforced.

### Ad-hoc overrides (no override file)

For one-off deploys, skip the override file and pass vars on the CLI:

```bash
databricks bundle deploy --target dev \
  --var="catalog=my_catalog" \
  --var="warehouse_id=abc123" \
  --var="vs_endpoint=my-vs-endpoint"
```

### App runtime env vars

The Streamlit app reads workspace config from env. `resources/app.yml` wires
these from the bundle vars automatically, so editing the override file is
usually enough.

| Variable | Default | Required |
|---|---|---|
| `WIKIBRICKS_WAREHOUSE_ID` | *(none)* | yes |
| `WIKIBRICKS_VS_INDEX` | *(none — e.g. `<catalog>.<schema>.pages_index`)* | yes |
| `WIKIBRICKS_LLM_MODEL` | `databricks-claude-sonnet-4-5` | no |

For local `streamlit run` (outside the bundle), export them in your shell
first — the app fails fast with a clear error if they're missing.

### Customizing content

The shipped `sample` seed loads 5 meta-pages that describe WikiBricks itself —
useful as a smoke test, not a real wiki. Two options to seed real content:

1. **Custom JSONL.** Set `seed_domain=custom` in the override file and export
   `WIKIBRICKS_CUSTOM_PAGES=/path/to/your/pages.jsonl` before running
   `deploy_wiki_store`. One JSON object per line with fields `path`, `title`,
   `page_type` (`concept` | `entity` | `synthesis` | `comparison`), `content`
   (object with `summary` + `body`), `tags`, `created_by`.
2. **Empty store + Python writes.** Set `seed_domain=none` and call
   `WikiClient.write_page` / `bulk_write_pages` from your own script. Useful
   when content comes from an upstream pipeline.

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

# Direct promote: synthesize a chat answer into a canonical page with cites
# edges back to every source. Works in-session — no waiting for the nightly
# promote job.
wiki.promote_answer(
    query="What index modes does Vector Search support?",
    answer="HYBRID, ANN, and FULL_TEXT. HYBRID combines the other two.",
    source_pages=[wiki.read_page("topics/vector-search")],
)
```

Full surface: [`src/wikibricks/client.py`](src/wikibricks/client.py).

### Two write paths, one contract

| Path | Who calls it | When |
|---|---|---|
| `WikiClient.promote_answer(...)` (Python) | App / notebook / SDK-capable agent | Immediate, in-session |
| `notebooks/promote_from_traces.py` (LLM) | Scheduled `promote` task | Nightly, trace-driven |
| `make_agent_tools(...)` → `wiki_promote_answer` | MCP / LangChain / Agent Framework tool | Immediate, from any agent |

All three land in the same `pages` table with the same `cites` edges and the
same `promote` row in `wiki_log`. Pick whichever fits your agent runtime —
the library contract is identical.

## MCP tools

Seven UC functions auto-exposed as MCP tools — agents discover them on
connect.

| Tool | Description |
|---|---|
| `fn_wiki_search(question, num_results)` | HYBRID Vector Search over `pages` |
| `fn_wiki_read(page_path)` | Read a page by path |
| `fn_wiki_history(page_path)` | Full version history |
| `fn_wiki_log(num_entries)` | Recent operation log |
| `fn_wiki_index()` | Page catalog |
| `fn_wiki_schema()` | Conventions (page types, link types, tag vocabulary) |
| `fn_wiki_write_help()` | How to write good wiki pages |

Auth: OAuth, `unity-catalog` scope. UC permissions enforced.

### Agent-side write tools (for MCP / LangChain / Agent Framework)

UC SQL functions can't perform DML, so writes are exposed as plain Python
callables agents can register as tools. Same guarantees as
`WikiClient.write_page` / `promote_answer` — just packaged for tool-calling
runtimes:

```python
from wikibricks import make_agent_tools

tools = make_agent_tools(warehouse_id="abc123")

# Register tools["wiki_write_page"] and tools["wiki_promote_answer"] with
# your agent framework. Schemas come from their docstrings + type hints.

tools["wiki_promote_answer"](
    question="What is a Delta table?",
    answer="A Delta table is ...",
    source_paths=["topics/delta", "topics/acid"],
)
# → {"path": "promoted/what-is-a-delta-table", "cited": 2}
```

`wiki_promote_answer` is the direct promote path for MCP-hosted agents: it
creates a `synthesis` page at `promoted/<slug>`, links `cites` edges back
to every resolved source, and logs an `op_type=promote` row — same rows
the nightly `promote` job would produce, just written the moment the
agent decides the answer is worth keeping.

## Development

```bash
uv sync
uv run pytest                       # no workspace needed
uv run ruff check src tests scripts
uv build                            # → dist/wikibricks-*.whl
```

Coding agents should read [`AGENTS.md`](AGENTS.md) for repo conventions,
hard rules, and the release checklist.

## What this is not

- Not a multi-hop QA system — the agent does the reasoning.
- Not a vector DB product — the index is Databricks Vector Search.
- Not a SaaS — it's a Databricks Asset Bundle that deploys into your workspace.
- Not scratch memory — per-session conversation state belongs elsewhere.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
