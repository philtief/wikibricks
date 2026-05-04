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
Three tasks, the last two optional-to-edit:

1. **`curate`** — deterministic, LLM-free. Proposes new typed edges via Vector
   Search nearest-neighbor + exact-title matching, tagged with `confidence` +
   `origin ∈ {auto-vs, auto-title}`. Auto-commits anything above
   `auto_commit_threshold=0.85`; leaves the rest for the agent to decide on its
   next call. Runs lint (orphans, stale pages, duplicates, broken links),
   deterministic link repair, and a Phase 4 health check that flags pages
   `oversize` / `empty` / `ok`. This is the library contract.
2. **`segregate`** — opt-in, LLM-driven. Picks up pages flagged
   `health_status='oversize'` by curate's Phase 4 and splits each into a
   parent (summary + Markdown ToC) plus N chunk children, joined by
   `parent_id` + `chunk_index`. Deterministic chunking lives in
   `src/wikibricks/segregate_logic.py`; the LLM (`${var.llm_model}`) is asked
   only for a 1–2 sentence summary and one short title per chunk. Reassembly
   is via `fn_wiki_read_full(parent_path)`. Drop the `segregate` task block
   in `resources/wiki_curate_job.yml` to run fully LLM-free; curate stays
   green on its own.
3. **`promote`** — opt-in, trace-driven. Mines agent session traces, clusters
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
| `segregate` | An oversize page was split into a parent + N chunk children |
| `segregate_skip` | An oversize page could not be split (single paragraph too large) |

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
`seed_domain` (`sample` | `hotpot` | `custom` | `none`).

**2. Deploy.**

```bash
databricks bundle deploy --target dev
databricks bundle run deploy_wiki_store --target dev
```

The `deploy_wiki_store` notebook creates the schema, Delta tables, Vector
Search index, and UC functions. Subsequent `bundle deploy` calls push code
changes only.

**2b. (Optional) Choose the MCP tool surface.**

By default `deploy_wiki_store` exposes all 8 UC functions as MCP tools.
Some agents do better with a smaller, focused tool list — weaker models
get distracted, and a read-only agent has no need for `fn_wiki_write_help`.
Pass `enabled_uc_functions` to deploy a subset:

```bash
databricks bundle deploy --target dev \
  --var="enabled_uc_functions=fn_wiki_search,fn_wiki_read_full,fn_wiki_index"
databricks bundle run deploy_wiki_store --target dev
```

Available names: `fn_wiki_search`, `fn_wiki_read`, `fn_wiki_read_full`,
`fn_wiki_history`, `fn_wiki_log`, `fn_wiki_index`, `fn_wiki_schema`,
`fn_wiki_write_help`. Empty (the default) deploys all 8. Re-running with
a different non-empty subset both creates the listed functions and drops
any previously deployed function not in the new set — managed MCP
surfaces every UC function in the schema, so this keeps your agent's
tool list in sync with the variable. Empty switches back to "keep
whatever is deployed" (no drops).

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
useful as a smoke test, not a real wiki. Three options to seed real content:

1. **Custom JSONL.** Set `seed_domain=custom` in the override file and export
   `WIKIBRICKS_CUSTOM_PAGES=/path/to/your/pages.jsonl` before running
   `deploy_wiki_store`. One JSON object per line with fields `path`, `title`,
   `page_type` (`concept` | `entity` | `synthesis` | `comparison`), `content`
   (object with `summary` + `body`), `tags`, `created_by`.
2. **Empty store + Python writes.** Set `seed_domain=none` and call
   `WikiClient.write_page` / `bulk_write_pages` from your own script. Useful
   when content comes from an upstream pipeline.
3. **HotpotQA corpus.** Set `seed_domain=hotpot` to reuse the ~66k-page
   Wikipedia subset used by the retrieval benchmark — handy as a realistic
   test corpus.

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

Eight UC functions auto-exposed as MCP tools — agents discover them on
connect.

| Tool | Description |
|---|---|
| `fn_wiki_search(question, num_results)` | HYBRID Vector Search over `pages` |
| `fn_wiki_read(page_path)` | Read a page by path |
| `fn_wiki_read_full(parent_path)` | Read a parent + all chunk children, ordered by `chunk_index` |
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

## Recorder — auto-record Claude Code sessions to your wiki

Optional consumer-side package that turns every Claude Code session into a
wiki page. Install with the `recorder` extra:

```bash
uv pip install -e ".[recorder]"
```

Three console scripts get wired up:

| Script | Purpose |
|---|---|
| `wiki-init` | Interactive setup: writes `~/.wikibricks-recorder.toml` with one or more `[wikis.<name>]` sections. Three flows: personal, team-create (emits a non-secret `wikibricks-team.toml` to share), team-join (consumes the shared toml + your own CLI profile). |
| `wiki-target` | Switch which configured wiki the hooks record into. Persists in `~/.wikibricks/active-target`. `WIKIBRICKS_TARGET=<name>` env var beats the file. |
| `wikibricks-mcp` | Stdio MCP server registered with Claude Code. Five tools (`wiki_search`, `wiki_read_full`, `wiki_index`, `wiki_write_page`, `wiki_promote_answer`) talking directly to `WikiClient`. UC functions stay deployed for managed-MCP consumers — this is a separate consumer-side surface. |

The five MCP tools and their arguments (advertised at runtime by
`get_tool_schemas()` in `src/wikibricks_recorder/wiki_mcp.py`):

| Tool | Required | Optional |
|---|---|---|
| `wiki_search` | `query` | `k` (default 5, range 1-20) |
| `wiki_read_full` | `path` | — |
| `wiki_index` | — | `prefix` |
| `wiki_write_page` | `path`, `title`, `summary`, `body` | `page_type` (default `concept`), `tags` |
| `wiki_promote_answer` | `question`, `answer` | `source_paths` |

### Quick install

After `wiki-init`, install the five Claude Code hooks into a Claude Code
settings file. The CLI merges with any existing entries and backs up the
file before writing.

```bash
wiki-init --install-hooks                       # user scope (default)
wiki-init --install-hooks --scope project       # team-shared, ./.claude/settings.json
wiki-init --install-hooks --scope local         # personal-per-project, ./.claude/settings.local.json
wiki-init --install-hooks --settings PATH       # explicit path (escape hatch)
```

| Scope | Path | When to use |
|---|---|---|
| `user` | `~/.claude/settings.json` | Personal recorder for all projects (default) |
| `project` | `./.claude/settings.json` | Team-shared hooks; commit to git |
| `local` | `./.claude/settings.local.json` | Personal overrides for one project; gitignored |

To remove the hooks, run the same command with `--uninstall-hooks` (matches by
exact command string, leaves any non-recorder hooks untouched, backs up
first):

```bash
wiki-init --uninstall-hooks                     # mirror of --install-hooks
wiki-init --uninstall-hooks --scope project     # remove from ./.claude/settings.json
```

Then register the MCP server (run from your `wikibricks-dev` clone):

```bash
claude mcp add wiki --scope user -- \
  uvx --from "wikibricks[recorder] @ file://$(pwd)" wikibricks-mcp
```

If you would rather hand-merge, the bundled template at
`examples/claude-settings.json` can be sed-substituted instead:

```bash
sed "s|/PATH/TO/wikibricks-dev|$(pwd)|g" examples/claude-settings.json
```

### Team-shared MCP via `.mcp.json`

For a team that wants every contributor to register the same `wiki` MCP
server when they open the repo in Claude Code, commit a `.mcp.json` at the
repo root instead of asking each developer to run `claude mcp add`:

```json
{
  "mcpServers": {
    "wiki": {
      "command": "uvx",
      "args": [
        "--from",
        "wikibricks[recorder] @ git+https://github.com/<org>/wikibricks-dev@v0.2.0",
        "wikibricks-mcp"
      ]
    }
  }
}
```

Claude Code prompts the user to approve the server on first session in the
repo, then auto-starts it from then on. **Caveat:** this snippet pins the
recorder to a Git ref so every machine resolves to the same code — `file://`
absolute paths work for one developer but break across teammates. If you
publish the recorder to PyPI (or a private index), replace the `--from` arg
with `wikibricks[recorder]==0.2.0` for a faster cold-start.

Hooks are still per-machine (`settings.json` is not committable for team
hooks because the recorder needs an absolute Python path). Each developer
runs `wiki-init --install-hooks` once in their own user scope.

### Per-task wiki switching

```bash
wiki-target                    # list configured wikis (* marks active)
wiki-target team-platform      # switch
claude                         # this session records to team-platform
wiki-target personal           # back to personal
WIKIBRICKS_TARGET=team-platform claude   # one-shot env override
```

The recorder writes one page per session to
`sessions/<user_id>/YYYY/MM/DD/<sid>` in the active wiki's schema. For team
wikis, sessions are partitioned by `user_id` so shared schemas don't
collide. **Hard rule:** the library (`src/wikibricks/`) stays LLM-free; the
recorder is consumer-side, allowed to interact with LLMs (today it
doesn't), and is structurally separate from the storage contract.

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
uv sync                             # core library
uv sync --extra recorder            # add the optional recorder package
uv run pytest                       # 453 tests, no workspace needed
uv run ruff check src tests scripts
uv build                            # → dist/wikibricks-0.2.0-py3-none-any.whl
```

Coding agents (Claude Code, Cursor, Cortex, Copilot CLI) should read
[`AGENTS.md`](AGENTS.md) for repo conventions, hard rules, release
checklist, and known-wrong patterns. Claude Code picks it up via the
`CLAUDE.md` symlink automatically.

## What this is not

- Not a multi-hop QA system — the agent does the reasoning.
- Not a vector DB product — the index is Databricks Vector Search.
- Not a SaaS — it's a Databricks Asset Bundle that deploys into your workspace.
- Not scratch memory — per-session conversation state belongs elsewhere.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
