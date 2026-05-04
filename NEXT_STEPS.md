# WikiBricks — Next Steps

A living roadmap. Items are grouped by theme, not release. Items closer to
the top of each theme are higher priority. Shipped work moves to
`CHANGELOG.md`; this file is what's *next*.

## Theme 1 — Graph primitive: stop hand-rolling it

Today WikiBricks implements the graph as (a) a three-column Delta `links`
table, (b) a `propose_edges` heuristic (VS NN + title substring), and (c) a
recursive-CTE `graph_neighbors` BFS. That's enough to prove the pattern,
but it's a long way from what Wikipedia, Roam, Obsidian, or a citation
graph actually need. Three concrete bets, in order:

### 1.1  GraphFrames analytics inside the curate job (high value, low lift)

Add `src/wikibricks/graph.py` thin wrapper around
[GraphFrames](https://graphframes.github.io). Materialise results as
page-level columns every curate run, so every *read* path (search
reranking, Genie, the Streamlit app) picks them up for free.

New library surface:

- `wiki.graph_pagerank(damping=0.85, iterations=20) -> Delta table` —
  hub-score per page. Writes to `pages.hub_score`. Search reranking can
  blend this with the VS score so popular pages surface first on
  ambiguous queries.
- `wiki.graph_communities(algorithm='label_propagation') -> Delta table`
  — community id per page. Writes to `pages.community_id`. The `promote`
  notebook can cluster questions *by community* rather than pure embedding
  similarity, giving tighter synthesis pages.
- `wiki.graph_shortest_path(src_path, tgt_path, max_hops=6)` —
  explanation paths between two pages. Useful for "why is X related to Y?"
  agent questions.
- `wiki.graph_motifs(pattern)` — Motif DSL passthrough. First motif to
  ship: "pages cited by synthesis pages that only cite entity pages" →
  quality-triage signal.

**Effort:** ~2 weeks. GraphFrames is already available on all-purpose
clusters; zero new services.
**Risk:** runtime on very large wikis (>10M edges) — mitigate by running
inside the nightly curate job, not the agent hot path.

### 1.2  Lakebase + `ltree` for category hierarchy (medium value, low lift)

Pages already have hierarchical paths (`topics/databricks/vector-search`,
`promoted/what-is-foo`). Today those are just strings; every "pages under
this category" query becomes `WHERE path LIKE 'topics/databricks/%'`,
which is a full scan and has no notion of depth.

Proposal: run [Lakebase](https://docs.databricks.com/lakebase) (managed
Postgres on Databricks) as a read replica of `pages` + `links`, and
mirror `pages.path` into an [`ltree`](https://www.postgresql.org/docs/current/ltree.html)
column (`topics.databricks.vector_search`). GiST index over ltree gives
O(log n) hierarchy queries:

```sql
SELECT * FROM pages WHERE path_ltree <@ 'topics.databricks';     -- all descendants
SELECT * FROM pages WHERE path_ltree ~ 'topics.*.benchmark';     -- pattern match
SELECT * FROM pages WHERE path_ltree @> 'topics.databricks.vs';  -- all ancestors
```

Expose via MCP through a single UC function that calls Lakebase via
Databricks' Postgres federation:

```sql
fn_wiki_subtree(path STRING, depth INT DEFAULT 3) RETURNS STRING
```

**Why ltree first, AGE later (1.3 below).** ltree is Postgres built-in,
no third-party extension to install, no Cypher learning curve. It covers
the ~80% of agent queries that are really "what lives under this
category" — which is the first thing an agent asks when orienting in an
unfamiliar wiki. Arbitrary-edge multi-hop traversal is a different use
case and belongs to a different primitive.

**Effort:** ~2–3 weeks, dominated by the Delta → Lakebase sync pipeline
(reusable for 1.3). The ltree column + index is half a day.

### 1.3  Lakebase + Apache AGE for multi-hop graph traversal (medium value, medium lift — opt-in)

Once the Lakebase sync exists (1.2), adding [Apache AGE](https://age.apache.org)
on the same instance is a low-incremental-cost way to get Cypher over
the typed-link graph for queries ltree can't express:

```cypher
MATCH (p:Page)-[:cites*1..3]->(q:Page {title:'Delta Lake'}) RETURN p LIMIT 10
MATCH path = shortestPath((a:Page {path:'topics/mlflow'})-[*..6]-(b:Page {path:'topics/uc'})) RETURN path
```

New UC function:

```sql
fn_wiki_graph_query(cypher STRING) RETURNS STRING
```

**Decision rule:** ship 1.3 only when `wiki_log` shows real agent traffic
running three-hop-plus traversals that 1.2 can't serve. Don't add a
second query language speculatively.

### 1.4  Graph-aware promote (high value, low lift *after* 1.1)

Once PageRank and communities exist, rewrite
`notebooks/promote_from_traces.py` to:

1. Cluster trace questions *inside* each community (cheaper + tighter
   than global KMeans over embeddings).
2. Boost clusters whose questions touch high-hub pages — those are the
   ones most agents actually need.
3. On promote, auto-attach `related` edges to the top-N highest-PageRank
   pages in the same community.

Turns PageRank + communities into a *feedback loop*: popular areas get
more canonical synthesis pages; synthesis pages raise the PageRank of
their sources.

## Theme 2 — Ingestion: stop asking users to write JSONL

`ingest_source` takes raw text + URL. Real corpora live in Confluence,
Notion, GitHub wikis, Slack threads, public docs sites. First-class
connectors (in `src/wikibricks/connectors/`) that produce canonical page
dicts ready for `bulk_write_pages`:

- `connectors/confluence.py` — Atlassian API → `entity` pages, preserves
  space + label metadata as tags.
- `connectors/notion.py` — Notion DB → `entity` / `concept` pages.
- `connectors/github_wiki.py` — GitHub wiki repo → pages with stable
  history.
- `connectors/slack.py` — Slack threads → `synthesis` candidates for the
  promote job to judge.

Each connector is a Lakeflow-Job-ready notebook + a pure Python module
with the same interface (`fetch() -> list[dict]`). Keeps the library core
LLM-free; any summarization happens in an upstream notebook.

## Theme 3 — Search quality

The current `search()` is single-shot HYBRID VS. Obvious wins:

- **Temporal freshness** — blend `updated_at` into reranking. Stale meta
  pages currently win over fresher synthesis pages.
- **Edge-type awareness** — when a query names a relationship ("what
  cites the 2024 DPO paper"), filter candidates by outgoing `cites` edges
  before ranking.
- **Cross-encoder reranker** — optional second-stage reranker using a
  Databricks Foundation Model Serving endpoint (`databricks-bge-reranker`
  when it ships). Opt-in via `wiki.search(..., rerank=True)`.
- **Query rewriting** — agent can call `wiki.rewrite_query(q)` to get 3
  paraphrases, run all three, union results. Pushes the LLM out of the
  library (still contract-pure) but makes a concrete helper available.

## Theme 4 — Observability + operator tools

- **Genie-on-wiki dashboard** — wire `wiki_log`, `pages`, `links`,
  `pages_history` into a Genie Space. Operators ask "which pages did
  agents read most last week?" in English.
- **Per-agent metrics** — add `agent_id` column to `wiki_log`; dashboard
  panels for read/write/promote rates by agent.
- **Hallucination-flag loop** — agents emit a "this answer was wrong"
  signal → corresponding `promote` row gets a `deprecated` tag →
  search reranker demotes it.

## Theme 5 — Multi-tenancy

Today the whole wiki lives in one `<catalog>.<schema>`. Enterprise users
will want one wiki per team. Two options:

1. **Schema-per-tenant** (cheap, no code change): just deploy the bundle
   multiple times with different `schema` vars. Document in README.
2. **Tenant column in-table** (more work, single index): add `tenant_id`
   to every table, every query filters on it, one VS index sharded by
   tenant. Keeps the curate job centralized.

Pick (2) only when real demand appears; (1) is the pragmatic default.

## Theme 6 — Safety + governance

- **PII scan on write** — add an optional `safety_check=True` flag on
  `write_page` that calls a UC function running a PII regex / ML
  classifier. Blocks or quarantines the write.
- **Schema-enforced page content** — today `content` is free-form JSON.
  Optional Pydantic-style validation per `page_type` would catch bad
  writes at the library boundary.
- **Attribution invariants** — every `promoted/*` page must have at
  least one outgoing `cites` edge. Add a lint rule + test.

## Theme 7 — Packaging

- **PyPI release.** `pip install wikibricks` + `wikibricks init` CLI to
  scaffold a bundle skeleton. Makes adoption a one-minute path.
- **Databricks Marketplace listing** once 1.1 + 2.1 ship.

## Non-goals (explicit)

- **Bespoke MCP server.** Managed MCP surfaces UC functions natively.
- **Building our own graph storage engine.** Use GraphFrames + AGE, not
  custom CRDT / triple-store code.
- **LLM inside `src/wikibricks/`.** Agent is the only LLM in the loop.
