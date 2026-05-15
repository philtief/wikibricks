# WikiBricks

**A wiki for your AI agent, on Databricks.** Delta + Vector Search +
Unity Catalog, exposed as native MCP tools. Your agent gets a
persistent, versioned, typed-link knowledge store that **grows from its
own sessions** — and you can search and read those sessions back from
your next conversation.

Two pieces, both optional, designed to be used together:

- **The wiki store** — a Databricks Asset Bundle that deploys the
  schema, tables, Vector Search index, UC functions, and a daily
  maintenance job. One `databricks bundle deploy`, then any agent that
  speaks MCP can read and write it.
- **The recorder** — an optional Claude Code plugin that records every
  session as one wiki page and exposes 5 MCP tools to the agent so it
  can search prior sessions. This is the easy on-ramp: 5 minutes to
  install, immediately useful. Citations from `wiki.search()` flow into
  an `agent_traces_v` view that the promote pipeline mines for recurring
  questions.

> **Grounding idea:** Andrej Karpathy's
> [LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
> — instead of re-retrieving raw documents at every query, the agent
> incrementally compiles a structured, interlinked wiki it maintains
> itself. Knowledge **compounds** instead of getting re-derived. See
> also [Context and Memory for Agents on
> Databricks](https://medium.com/@philipp.tiefenbacher_42173/context-and-memory-for-agents-on-databricks-f3c945cd8681)
> for the design rationale.

## 5-minute install: the personal recorder

The fastest way to see what WikiBricks is: install the recorder plugin
and let it record one Claude Code session.

### 1. Deploy the wiki store (one-time, ~3 minutes)

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
cp databricks.override.example.yml databricks.override.yml
# edit: host, profile, catalog, schema, warehouse_id

databricks bundle deploy --target dev
databricks bundle run deploy_wiki_store --target dev
```

Idempotent. Creates the schema, 8 Delta tables (including
`wiki_vocabulary` for auto-tagged slugs), the `agent_traces_v` view over
`wiki_log`, the Vector Search index, 8 UC functions, and the daily
curate Lakeflow Job. The wheel installs into the serverless tasks via
the bundle's environment `dependencies` field — notebooks contain no
`%pip install` lines.

### 2. Install the plugin (~1 minute)

In any Claude Code session:

```
/plugin marketplace add https://github.com/philtief/wikibricks.git
/plugin install wikibricks-recorder@wikibricks
```

Then once per machine:

```bash
uvx --from "git+https://github.com/philtief/wikibricks.git@v0.5.1" \
    wiki-init personal      # | team-create | team-join
```

That's it. Open a new Claude Code session and the recorder writes one
page per session into `sessions/<user>/YYYY/MM/DD/<sid>`. Five MCP
tools (`wiki_search`, `wiki_read_full`, `wiki_index`, `wiki_write_page`,
`wiki_promote_answer`) appear automatically so the agent can search and
read your prior sessions. Plugin details:
[`plugin/README.md`](plugin/README.md).

## Why a wiki, not a log

Agents forget. Context windows are not memory, pasted docs are not
memory, embeddings alone are not memory. What you actually want:

- **pages** with stable paths and titles
- **typed links** between them (not a generic `related_to` bag)
- **history** — who wrote what, when, and what changed
- **search** by meaning or keyword
- **growth** — the store gets *better* as the agent answers more
  questions

WikiBricks delivers all five on managed Databricks services. No bespoke
vector DB, no separate MCP server, no model dependency inside the
library core — the agent calling the wiki is the only LLM in the loop.

## The maintenance loop

Every deployment ships one Lakeflow Job (`wikibricks_curate`) that runs
daily, with four tasks. The first is the contract; the other three are
opt-in.

1. **`curate`** *(LLM-free)*. Proposes new typed edges via Vector Search
   nearest-neighbor + exact-title matching, tagged with `confidence` +
   `origin`. Auto-commits anything above
   `auto_commit_threshold=0.85`; leaves the rest for the agent. Runs
   lint (orphans, stale pages, duplicates, broken links), deterministic
   link repair, and flags pages `oversize` / `empty` / `ok`.
2. **`segregate`** *(LLM-driven, opt-in)*. Picks up oversize pages,
   splits each into a parent (summary + ToC) plus N chunk children.
   Reassembly via `fn_wiki_read_full(parent_path)`. Drop the task to
   run fully LLM-free.
3. **`tag`** *(LLM-driven, opt-in)*. Proposes 3–5 semantic tags for each
   recent top-level page, MERGEs them into `wiki_vocabulary` (with
   `count` and `status='pending'|'approved'`), and appends the slugs to
   `pages.tags` with an `llm:` prefix. The recorder owns mechanical tags
   (`session`, `cwd:...`, `model:...`); the tag task owns `llm:*`. The
   library preserves `llm:*` across all writes so the two never collide.
4. **`promote`** *(LLM-driven, opt-in)*. Mines agent session traces
   from `agent_traces_v` (search ops with returned paths), clusters
   recurring questions, synthesizes one canonical answer per cluster,
   scores it with an LLM judge, and writes passing clusters to
   `promoted/<slug>` with `cites` edges back to source pages. This
   is what makes the wiki *grow* from agent traces.

Every write, search, auto-tag, promote, and index sync appends to
`wiki_log` so operators can watch the pipeline.
`scripts/wikibricks_health.py --profile … --catalog … --schema … --warehouse-id …`
runs a six-probe oracle (auto-tag coverage, vocab growth, page-tag
coverage, citations logged, promote end-to-end, curate recency) and
exits non-zero if any criterion drops. `scripts/diagnose_traces.py
--window-days 7` summarizes trace volume, cluster eligibility, and
recent events.

## Library API

```python
from wikibricks import WikiClient

wiki = WikiClient(warehouse_id="abc123")

wiki.write_page("topics/vector-search", title="Vector Search",
                content={"summary": "...", "body": "..."}, tags=["retrieval"])

# Agent-in-the-loop: WikiBricks proposes, agent decides.
candidates = wiki.propose_edges("topics/vector-search", min_similarity=0.70)
wiki.commit_edges([c for c in candidates if my_agent_approves(c)])

wiki.search("what index modes exist", mode="HYBRID")    # HYBRID / ANN / FULL_TEXT
# search() logs {returned_paths, k, mode} to wiki_log.details. agent_traces_v
# reads those rows so the promote pipeline mines real agent traffic.

wiki.graph_neighbors("topics/vector-search", depth=2)
wiki.history("topics/vector-search")

# Tag task batches — the library is LLM-free, the notebook calls FMAPI.
wiki.upsert_vocabulary(
    [{"slug": "row-level-security", "source": "auto_tag"}],
    approve_threshold=3,
)
wiki.append_page_tags("topics/vector-search", ["llm:retrieval", "llm:hybrid-search"])

# Promote a Q&A pair into a canonical synthesis page (cites every source).
wiki.promote_answer(query="...", answer="...",
                    source_pages=[wiki.read_page("topics/vector-search")])
```

Full surface: [`src/wikibricks/client.py`](src/wikibricks/client.py).
For agent runtimes that can't speak Python directly, use
`make_agent_tools(...)` to get tool-callable equivalents of the write
methods.

## MCP tools

Eight UC functions auto-exposed via Databricks managed MCP at
`https://<workspace>/api/2.0/mcp/functions/<catalog>/<schema>` (OAuth,
`unity-catalog` scope, UC permissions enforced):

| Tool | Description |
|---|---|
| `fn_wiki_search(question, num_results)` | HYBRID Vector Search over `pages` |
| `fn_wiki_read(page_path)` | Read a page by path |
| `fn_wiki_read_full(parent_path)` | Read parent + chunk children |
| `fn_wiki_history(page_path)` | Full version history |
| `fn_wiki_log(num_entries)` | Recent operation log |
| `fn_wiki_index()` | Page catalog |
| `fn_wiki_schema()` | Conventions (page types, link types, tags) |
| `fn_wiki_write_help()` | How to write good wiki pages |

Pass `--var="enabled_uc_functions=fn_wiki_search,fn_wiki_read_full,..."`
to `bundle deploy` to expose a subset — useful for read-only agents or
weaker models that get distracted by long tool lists.

DML (writes) is exposed separately via `make_agent_tools(...)` — UC
SQL functions can't perform writes.

## Development

```bash
uv sync                              # core library
uv sync --extra dev                  # +pytest, ruff, streamlit, pyyaml
uv sync --extra recorder             # also install the recorder package
uv run pytest                        # 583 tests, no workspace needed
uv run ruff check src tests scripts
uv build                             # → dist/wikibricks-0.5.1-py3-none-any.whl
```

For the recorder, see [`plugin/README.md`](plugin/README.md). For
deeper deploy customization (custom seed corpora, app env vars, ad-hoc
overrides), see [`docs/`](docs/) and the bundle config in
[`databricks.yml`](databricks.yml). Coding agents should read
[`AGENTS.md`](AGENTS.md) for repo conventions, hard rules, release
checklist, and the dev → public sync checklist.

## What this is not

- Not a multi-hop QA system — the agent does the reasoning.
- Not a vector DB product — the index is Databricks Vector Search.
- Not a SaaS — a Databricks Asset Bundle that deploys into your workspace.
- Not scratch memory — per-session conversation state belongs elsewhere.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
