# Changelog

All notable changes to WikiBricks are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `wiki-init --install-hooks` — auto-merge the five recorder hooks into
  `~/.claude/settings.json` (existing entries preserved, file backed up
  first). Replaces the manual `sed examples/claude-settings.json` step.
  Honors `--python` and `--settings` for non-default paths.
- README "Recorder" section now lists every MCP tool's required and
  optional arguments, sourced from `wiki_mcp.py::get_tool_schemas()`.

### Fixed

- `wiki-init` personal-flow Next-steps message: replaced the broken
  `uvx --from . '.[recorder]'` invocation with the correct
  `uvx --from "wikibricks[recorder] @ file://$(pwd)"` form, and pointed
  users at the new `--install-hooks` flag.

## [0.2.0] - 2026-05-03

### Added

- **`wikibricks_recorder` package** — optional Claude Code → wiki bridge
  shipped alongside the library. Install with `pip install wikibricks[recorder]`.
  Three console scripts are wired in `pyproject.toml`:
  - `wiki-init` — interactive setup that writes `~/.wikibricks-recorder.toml`
    with a `[wikis.<name>]` section per wiki. Three flows: personal,
    team-create (emits a non-secret `wikibricks-team.toml` for sharing +
    GRANT SQL for the owner), team-join (consumes the shared toml + your
    own CLI profile).
  - `wiki-target` — switch which configured wiki the hooks write to.
    Persists the choice in `~/.wikibricks/active-target`. `WIKIBRICKS_TARGET`
    env var beats the file for one-shot overrides.
  - `wikibricks-mcp` — stdio MCP server registered with Claude Code via
    `claude mcp add`. Five tools (3 read, 2 write) talking directly to
    `WikiClient`. The library's UC functions stay deployed for managed-MCP
    consumers; this is a separate consumer-side surface.
- **Multi-wiki TOML format.** `~/.wikibricks-recorder.toml` now supports
  multiple `[wikis.<name>]` sections (e.g. `[wikis.personal]` next to
  `[wikis.team-platform]`). The legacy `[recorder]` single-section format
  is still read for back-compat.
- **`examples/claude-settings.json`** — copy-pasteable hook template with
  a `/PATH/TO/wikibricks-recorder` placeholder so new users `sed` it to
  their checkout and merge into `~/.claude/settings.json` instead of
  hand-crafting five identical hook entries.
- **`create_uc_functions_sql(..., enabled=...)`** — opt-in subset deploy
  for the eight UC functions. `enabled=None` (the default) keeps the
  existing behavior (deploy all eight); pass a set or list of names to
  deploy only those, e.g. `{"fn_wiki_search", "fn_wiki_read_full"}`.
  Unknown names raise `ValueError` so a typo can't silently produce a
  partial deploy. The library still ships every function — this is
  purely about which surface the managed-MCP endpoint exposes.
- **`UC_FUNCTION_NAMES`** — public tuple of the eight names, re-exported
  from `wikibricks` so callers can reference them without string typos.
- **`enabled_uc_functions` widget on `deploy_wiki_store`** — comma-separated
  list (default empty = all eight). Lets a deployment narrow the MCP tool
  surface without forking the notebook.
- **`scripts/sdk_redeploy.py`** — direct-SDK redeploy that bypasses
  Terraform, an escape hatch for `databricks bundle deploy` failing with
  `openpgp: key expired` on some CLI versions. Workspace-agnostic via
  required `WIKIBRICKS_CATALOG` / `WIKIBRICKS_SCHEMA` /
  `WIKIBRICKS_WAREHOUSE_ID` env vars; optional
  `WIKIBRICKS_ENABLED_UC_FUNCTIONS` mirrors the bundle variable of the
  same name. Idempotent: schema → seven tables → managed `wheels` volume
  + wheel upload → drop UC functions outside enabled set →
  `CREATE OR REPLACE` enabled set → verify.

### Changed

- **AGENTS.md hard rules 1 + 2 scoped to the library.** "No LLM in `src/`"
  and "no bespoke MCP server" now explicitly bind only the `src/wikibricks/`
  package. `src/wikibricks_recorder/` is consumer-side tooling and ships its
  own stdio MCP server because UC functions cannot do DML.

### Fixed

- **`fn_wiki_search` SQL UDF compatible with current `vector_search()`
  TVF.** Two runtime errors blocked the function from being created:
  `AI_SEARCH_HYBRID_QUERY_PARAM_DEPRECATION_ERROR` (HYBRID mode now requires
  `query_text =>` instead of `query =>`) and `NON_FOLDABLE_ARGUMENT` (UDF
  parameters can't be passed straight through to `num_results =>`). The
  inner `num_results` is now fixed at 20; the outer query trims to the
  caller's K via `ROW_NUMBER()`.
- **`deploy_wiki_store` notebook honors catalog/schema widgets.** Two real
  bugs surfaced when running against a non-default catalog/schema:
  `wikibricks.ops` reads `WIKIBRICKS_CATALOG` / `WIKIBRICKS_SCHEMA` at
  import time, so the notebook now sets `os.environ` from widgets BEFORE
  importing. `table_names` extended to all seven tables (was 5; missed
  `pages_vs_source` and `promote_checkpoint`, raising IndexError on the
  6th iteration).

## [0.1.5] - 2026-04-29

### Added

- **Page segregation for oversize pages.** Long pages now have a first-class
  parent/child split path. `wiki.pages` and `wiki.pages_history` gain
  `parent_id`, `chunk_index`, `health_status`, `health_score`, and
  `last_health_check` columns. The curate job's new Phase 4 health check
  classifies each page as `ok` / `empty` / `oversize` (default threshold
  50KB) and writes the verdict back via one batched UPDATE per status
  bucket. The new opt-in `wiki_segregate` notebook reads pages flagged
  `oversize`, asks the chat endpoint for a 1-2 sentence summary plus one
  title per chunk, then writes a parent (summary + Markdown ToC) and N
  chunk children joined by `parent_id`/`chunk_index`. Deterministic
  chunking and ToC construction live in `wikibricks.segregate_logic` and
  are unit-tested; the LLM call lives in the notebook only, per the
  AGENTS.md library-LLM-free rule.
- **`fn_wiki_read_full` UC function.** Reassembles a parent page with its
  chunks in `chunk_index` order, returning a single document. Exposed via
  managed MCP so agents reading a segregated page see the same content as
  before splitting.
- **`WikiClient.write_page(parent_id=..., chunk_index=...)`.** Two new
  optional kwargs let callers (and the segregate notebook) write chunk
  children that link to their parent and order deterministically.
- **`wikibricks.curate_logic.classify_page_health` /
  `find_duplicate_paths` / `build_health_summary`** — pure helpers for the
  curate health phase, with 15 new unit tests.
- **`wikibricks.segregate_logic.chunk_at_boundaries` / `child_path` /
  `child_title` / `build_parent_body`** — pure helpers for the split flow,
  with 14 new unit tests.
- **`wikibricks.make_agent_tools(warehouse_id)`** — factory that returns
  plain Python callables for the two write operations UC functions cannot
  perform: `wiki_write_page` and `wiki_promote_answer`. Register with any
  agent framework (Databricks Agent Framework, LangChain, LlamaIndex, a
  custom MCP server) to give agents direct promote-to-memory capability
  without routing through the curate job's trace-driven promote path.
- **`segregate` / `segregate_skip` `wiki_log` op_types.** Each split run
  appends a `segregate` row per parent (with chunk count + chunk titles)
  and a `segregate_skip` row when the chunker can't split a single
  oversize paragraph.

### Changed

- **`fn_wiki_search` now uses Vector Search.** The UC function previously
  did SQL `LIKE` substring matching over `content_text` / `title`, which
  meant the managed-MCP search surface was keyword-only while the Python
  `WikiClient.search` path was semantic. `fn_wiki_search` now calls the
  `vector_search()` SQL TVF with `query_type => 'HYBRID'` against
  `pages_index`, returning top-K pages ranked by semantic + lexical
  relevance with their full `content_text`. Signature changed from
  `(question, mode)` to `(question, num_results INT DEFAULT 5)` — agents
  that hard-coded `mode='HYBRID'` must drop the argument.
- **`wikibricks.ops.CATALOG` / `SCHEMA` are env-var driven.**
  `WIKIBRICKS_CATALOG` and `WIKIBRICKS_SCHEMA` retarget the library
  defaults from `main.wiki` without editing source — useful for forks and
  per-workspace deployments.

## [0.1.4] - 2026-04-23

### Added

- **`WikiClient.sync_index()`** — triggers the DELTA_SYNC Vector Search
  index and logs `vs_sync` / `vs_sync_fail` to `wiki_log`. Called
  automatically from `promote_from_traces` after successful promotions and
  from the Streamlit app after chat-mode auto-promote, so freshly written
  pages are searchable within one sync cycle.
- **Parse-fail discrimination in promote.** `promote_from_traces.py` now
  distinguishes *judge returned non-numeric text* (`promote_parse_fail`)
  from *legitimate low score* (`promote_reject`), so operators querying
  `wiki_log` can spot prompt drift independently of quality failures.
- **`judge_response_is_numeric`** helper in `wikibricks.promote_logic`.
- **Cross-task DAG integration test** (`tests/test_job_dag.py`) — executes
  `wiki_curate.py` and `promote_from_traces.py` in the real job DAG order
  against a `spec_set=WikiClient` mock, so method drift between the two
  notebooks fails loudly instead of silently.
- **`scripts/diagnose_traces.py`** — standalone diagnostic reporting
  trace volume, query-length percentiles, exact-match cluster eligibility,
  and `wiki_log` event counts. Run before trusting a scheduled promote
  window.
- **Env-var configuration for the Streamlit app**
  (`WIKIBRICKS_WAREHOUSE_ID`, `WIKIBRICKS_VS_INDEX`, `WIKIBRICKS_LLM_MODEL`).
  Wired from bundle vars in `resources/app.yml`. Warehouse ID and VS index
  are required — the app fails fast with a clear error if unset.
- **Browse-mode AppTest coverage** (`tests/test_app.py::TestBrowseMode`) —
  six in-process Streamlit tests covering the tree-button → session-state
  round-trip.
- **`databricks.override.example.yml`** — template for per-developer
  workspace host / profile / warehouse_id overrides.
  `databricks.override.yml` is gitignored.

### Changed

- **`judge_threshold` default lowered 4.5 → 4.0.** The judge prompt asks
  for a single digit 1–5, so 4.5 rejected every integer score; 4.0 admits
  4 and 5 as intended.
- **Portable bundle variable defaults.** `catalog` default is `main`;
  `warehouse_id` has no default and must be supplied per target. Target
  `workspace.host` / `profile` are provided via `DATABRICKS_CONFIG_PROFILE`
  env var or the new override file.
- **README refresh** — added operational-telemetry table, env-var config
  table, updated test count (223 → 305) and wheel version (0.1.3 → 0.1.4).

### Fixed

- **`_log` telemetry writes on SQL warehouse.** Rewrote the insert from
  `INSERT INTO ... VALUES (uuid(), ...)` to `INSERT INTO ... SELECT uuid(),
  ...` so `wiki_log` rows persist from the warehouse execution path.

## [0.1.3] - 2026-04-22

### Added

- **LLM-free graph primitives on `WikiClient`**: `propose_edges` (VS nearest-
  neighbor + exact-title entity match with per-edge `confidence` + `origin`),
  `commit_edges` (batch MERGE), `graph_neighbors` (1–3 hop traversal), and
  `fix_broken_links` (deterministic endpoint cleanup). No model calls inside
  WikiBricks — the calling agent stays the only LLM in the loop.
- **Default curate pipeline** (`notebooks/wiki_curate.py` +
  `resources/wiki_curate_job.yml`). One shipped Lakeflow Job with two tasks:
  (1) `curate` — deterministic connect + lint + repair (no LLM, library
  contract); (2) `promote` — optional trace-driven LLM synthesis that depends
  on `curate`. Drop the `promote` task block to run LLM-free.
- `confidence FLOAT` and `origin STRING` columns on the `links` table, with
  allowed origins `manual | auto-vs | auto-title | auto-cite`.

### Changed

- `add_link_sql` now writes `confidence` + `origin` and raises `ValueError` on
  invalid origin or out-of-range confidence.
- Legacy `notebooks/wiki_lint.py` fixed: the `wiki_log` INSERT now matches the
  real schema (`log_id, op_type, path, query, details, created_by`).
- Removed `resources/wiki_lint_job.yml` and `resources/promotion_pipeline.yml`;
  both superseded by the single two-task `wiki_curate_job.yml`. Promote task
  uses `databricks-claude-sonnet-4-5` by default (override via `llm_model`
  bundle var).

## [0.1.0] - 2026-04-21

Initial public release. A Delta + Vector Search wiki store for AI agents on
Databricks.

### Added

- **`WikiClient` Python API** (`src/wikibricks/client.py`) with `write_page`,
  `read_page`, `search`, `history`, `ingest_source`, `promote_answer`,
  `bulk_write_pages`, and `materialize_index`.
- **Five Delta tables** (`pages`, `pages_history`, `links`, `sources`, `log`)
  created by the `deploy_wiki_store` notebook. CDF enabled on `pages`.
- **Vector Search DELTA_SYNC index** (`pages_index`) over `pages.content_text`
  using `databricks-bge-large-en`. Three search modes: HYBRID (default), ANN,
  FULL_TEXT.
- **Seven UC functions** auto-exposed as MCP tools at
  `/api/2.0/mcp/functions/<catalog>/<schema>`: `fn_wiki_search`, `fn_wiki_read`,
  `fn_wiki_history`, `fn_wiki_log`, `fn_wiki_index`, `fn_wiki_schema`,
  `fn_wiki_write_help`. No FastMCP; Databricks managed MCP surfaces UC
  functions natively.
- **Versioned writes.** Every `write_page` archives the previous version to
  `pages_history`; `history(path)` returns the full lineage.
- **Typed links** between pages (`cites`, `related`, `supports`, `depends_on`,
  …) - cross-reference graph queryable in plain SQL.
- **Domain-agnostic seed loaders** (`src/wikibricks/seeds/`): `sample`
  (5 meta-pages), `hotpot` (HotpotQA, ~66k pages), `custom` (JSONL), `none`.
- **Databricks Asset Bundle** (`databricks.yml` + `resources/`) with
  `dev` / `staging` / `prod` targets. One-command deploy:
  `databricks bundle deploy --target dev`.
- **Reference Streamlit app** (`app/app.py`) with chat, Write, and Browse modes.
  Auto-promotes judged answers (score ≥ 4 on a 1-5 scale) into synthesis pages
  with `cites` edges back to source pages.
- **Batch promotion pipeline** (`resources/promotion_pipeline.yml`) -
  scheduled job that promotes offline-judged answers.
- **Nightly lint job** (`resources/wiki_lint_job.yml`) - scans for orphans,
  stale pages, duplicates, and broken links; writes issues to `log`.
- **Observability dashboard** (`resources/observability_dashboard.yml`) -
  pages, writes, reads, and lint findings over time.
- **Evaluation harness**:
  - HotpotQA fetch + seed + retrieval benchmark (`scripts/hotpot_*.py`,
    `notebooks/benchmark_hotpot.py`). Produces `benchmark_results.json` and
    `hotpotqa_results.html`.
  - 2WikiMultiHopQA fetch + seed + retrieval + generation + eval
    (`scripts/twowiki_*.py`), including an 8-variant cheap-lever ablation
    (`scripts/twowiki_variants.py`) and a Delta-checkpointed batch loop
    (`scripts/twowiki_batch_loop.sh`). Vendored official v1.1 evaluator.
- **220 unit tests** (`tests/`), no Databricks connectivity required.
- **Documentation**: `README.md`, `examples/hotpotqa.md`,
  `examples/twowiki.md`, `docs/hotpotqa_evaluation.md`,
  `docs/twowiki_evaluation.md`, `docs/img/architecture.{mmd,svg,png}`.

### Benchmarks

- **HotpotQA retrieval pilot** - 500-query HYBRID recall@10 ≈ 89% on a
  66,569-page corpus. Retrieval-only, not a HotpotQA leaderboard metric.
- **2WikiMultiHopQA open-retrieval** - preliminary 350-query ablation. Best
  variant (Sonnet 4.6 + HYBRID + K=10) reaches **Joint F1 21.2** under the
  official v1.1 evaluator. This matches the 2020 paper's own open-retrieval
  baseline (~20); modern 2024-2025 open-retrieval SOTA is 50-65 (task-tuned
  retrievers, iterative multi-hop, rerankers, fine-tuned heads - all outside
  WikiBricks' scope). See [`docs/twowiki_evaluation.md`](docs/twowiki_evaluation.md)
  for full framing.

### Limitations

- Off-the-shelf embeddings only (`databricks-bge-large-en`); no task-tuned
  retriever.
- Single-shot retrieval; no iterative multi-hop.
- No cross-encoder reranker.
- Evaluation harness uses a vendored copy of
  `2wikimultihop_evaluate_v1.py`; vendored assets are gitignored and fetched
  on demand by `scripts/fetch_twowiki.py`.

[Unreleased]: https://github.com/philtief/wikibricks/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/philtief/wikibricks/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/philtief/wikibricks/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/philtief/wikibricks/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/philtief/wikibricks/compare/v0.1.0...v0.1.3
[0.1.0]: https://github.com/philtief/wikibricks/releases/tag/v0.1.0
