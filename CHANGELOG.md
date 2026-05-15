# Changelog

All notable changes to WikiBricks are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.2] - 2026-05-15

### Added

- **`examples/team_wiki/`** — multi-agent team-wiki walkthrough.
  README explains the shared-wiki pattern (one catalog/schema, many
  writers, UC-governed). `simulate_team_activity.py` script writes
  ~9 sample pages across three fake users for screenshots/demos.
- **`examples/audit_demo/`** — bi-temporal audit demo.
  `audit_demo.py` writes a four-page graph through three event windows
  (Munich → London → Berlin) with backdated `valid_from` / `valid_until`
  values, then queries it from three points in time. `post.md` is a
  Medium-ready essay explaining bi-temporal memory and why
  wikibricks on Delta + UC differs from Mem0 / Letta / Graphiti.


## [0.7.1] - 2026-05-15

### Added

- **Karpathy export** — `python -m wikibricks.export_karpathy <target_dir>`
  walks every page, writes one `.md` per page with YAML frontmatter and
  a `## Related` section carrying outgoing currently-valid edges as
  `[[wikilinks]]` (plain) or `link_type::[[wikilinks]]` (typed). Round-trips
  with the v0.6.0 importer — export, edit in Obsidian/Foam/Dendron, re-import.
- **`wikibricks.karpathy_logic.render_page_markdown`** and
  `map_wiki_path_to_file` — pure helpers for the export pipeline. Tested
  for round-trip fidelity against the example fixture.

### Changed

- Bundle's `version` variable default bumped to `0.7.1` so fresh deploys
  resolve to the published wheel.


## [0.7.0] - 2026-05-15

### Added

- **`wikibricks.graph_logic`** — pure helpers for graph analytics:
  `build_igraph`, `compute_pagerank`, `compute_communities`, `rrf_fuse`.
  LLM-free; igraph as a runtime dependency.
- **`pages.hub_score`** — PageRank (PRPACK, damping=0.85) over the directed
  graph of currently-valid edges. Reflects authority flow per the `cites`
  relationship.
- **`pages.community_id`** — Leiden community assignment computed on the
  undirected projection of the graph. NULL for tiny graphs (<5 nodes) where
  community detection is not informative.
- **`pages.memory_class`** — taxonomy column (`episodic`, `semantic`,
  `procedural`, `synthesis`). Default `'semantic'`. Aligns with the
  community-standard memory-class vocabulary (atlan, mem0, appscale).
- **`WikiClient.update_graph_scores(scores)`** — batch MERGE of
  `(page_id, hub_score, community_id)` into pages. Used by the new
  notebook task. NULL-safe.
- **`WikiClient.search(rerank_with_pagerank=True)`** — optional flag that
  pulls each result's `hub_score` and reorders via Reciprocal Rank Fusion
  (k=60) across vector-search rank and PageRank rank. Default off —
  backward-compatible.
- **`notebooks/wiki_graph_analytics.py`** — new opt-in task in
  `wikibricks_curate`, depends on `curate`. Reads currently-valid edges
  (bi-temporal filter `WHERE valid_until IS NULL`), builds igraph,
  computes PageRank + Leiden, writes scores back. Logs a `graph_analytics`
  event to `wiki_log`.
- **`[graph]` optional install extra** in `pyproject.toml`:
  `uv pip install wikibricks[graph]` for the igraph dependency.
- **Migration**: `migrate_tables_sql()` adds three columns to `pages` in
  one ALTER batch + backfills `memory_class='semantic'` for existing rows.

### Changed

- **Bundle deploy** installs `igraph>=0.11,<2.0` into the serverless env
  alongside the wikibricks wheel.
- **Default `version` bundle variable** bumped from `0.5.0` to `0.7.0`
  so the env-dep wheel path resolves correctly out of the box.


## [0.6.2] - 2026-05-15

### Fixed

- **`commit_edges` is now genuinely bi-temporal.** Prior versions accepted
  no caller-supplied event times, so every edge's `valid_from` collapsed
  to `current_timestamp()` — structurally bi-temporal (the columns existed)
  but functionally uni-temporal in disguise. v0.6.2 accepts optional
  `valid_from` and `valid_until` strings per edge. Supersede close-out
  uses the new edge's `valid_from` (continuous validity intervals, no gap).
  An agent learning today that "Philipp lived in Munich from 2020 to 2023"
  can now record that fact with its real validity window while `created_at`
  remains today.
- README claim about "Graphiti's bi-temporal model" was technically true
  only after v0.6.2. Prior to this release the column shape was bi-temporal
  but the write API was not — calling the v0.6.0 model "bi-temporal" was an
  overclaim. Corrected.

### Changed

- **`commit_edges` batching**: edges are now grouped by `(valid_from,
  valid_until)`. Default-now case (no caller-supplied times) is still
  exactly one UPDATE + one INSERT per call. Mixed-time batches do one
  UPDATE + one INSERT per distinct `(valid_from, valid_until)` group.


## [0.6.1] - 2026-05-15

### Fixed

- **`migrate_tables_sql`** — replace `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` (rejected by Databricks SQL with PARSE_SYNTAX_ERROR) with the
  one-shot `ADD COLUMNS (valid_from TIMESTAMP, valid_until TIMESTAMP)`
  form. Idempotency on re-run is provided by `sdk_redeploy`'s
  continue-on-failure handling (logs FAILED on the second run, moves on)
  rather than by the SQL itself. v0.6.0 tag had the broken syntax; v0.6.1
  is the one to install for a fresh deploy.

### Docs

- **README**: corrected the bi-temporal section. History is persisted in
  the closed rows themselves (which survive `OPTIMIZE`/`VACUUM`), not via
  Delta time travel — time travel is retention-bounded and is not used by
  any of the bi-temporal read paths.


## [0.6.0] - 2026-05-15

### Added

- **Bi-temporal links (Track 1)** — `links` table gains `valid_from` and
  `valid_until` columns. `WikiClient.commit_edges` is now append-only:
  each new edge closes any prior currently-open row for the same
  `(source_page_id, target_page_id, link_type)` (sets `valid_until =
  current_timestamp()`) and INSERTs a fresh row. Matches Graphiti's
  bi-temporal model on a Delta substrate. Reads filter by validity
  automatically.
- **`WikiClient.graph_neighbors_at(path, at_timestamp, depth, link_types)`**
  — point-in-time graph traversal. Returns neighbors that were valid at
  `at_timestamp` (ISO 8601 string).
- **`WikiClient.link_history(src_path, dst_path)`** — full chronological
  version trace of edges between two pages, oldest first. Each row carries
  `link_type`, `confidence`, `origin`, `valid_from`, `valid_until`.
- **Karpathy-pattern importer (Track 3)** — `python -m wikibricks.import_karpathy
  <dir>` walks a folder of markdown files, parses YAML frontmatter, extracts
  `[[wikilinks]]` and `relationship::[[target]]` typed edges, and writes
  them through `bulk_write_pages` + `commit_edges`. Supports Obsidian /
  Foam / Dendron-style markdown with zero runtime dependencies beyond
  stdlib. `--dry-run` reports without writing.
- **`wikibricks.karpathy_logic`** — pure helpers: `parse_frontmatter`,
  `extract_wikilinks`, `extract_typed_edges`, `wiki_path_for`. LLM-free.
- **`examples/karpathy_wiki/`** — example fixture with 6 markdown files
  demonstrating the Karpathy three-folder pattern (`raw/`, `wiki/`,
  `index.md`) plus a typed-edge example (`cites::[[Apache Spark]]`).

### Changed

- **`WikiClient.commit_edges` SQL shape** changed from MERGE-with-update
  to UPDATE-close-then-INSERT. Two batched round-trips regardless of N.
- **`graph_neighbors`** now filters `WHERE l.valid_until IS NULL` by
  default. Use `graph_neighbors_at(t)` for historical state.
- **Schema migration**: `migrate_tables_sql()` adds `ALTER TABLE links
  ADD COLUMNS (valid_from, valid_until)` + a backfill UPDATE. Idempotent
  on re-run (Databricks SQL has no `ADD COLUMN IF NOT EXISTS`; sdk_redeploy
  continues past "column already exists" errors).

## [0.5.1] - 2026-05-15

### Fixed

- **`WikiClient.upsert_vocabulary`** (Bug 5) — pre-aggregates source rows
  with `GROUP BY slug, COUNT(*) AS occurrences` before the MERGE. Without
  this, batches with the same slug across multiple pages fail with
  `DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE`. Increments
  count by occurrences (not a hardcoded 1) on the matched branch.
- **`agent_traces_v`** (Bug 6) — emits `CAST('' AS STRING) AS model_response`
  instead of NULL. The promote notebook concatenates message contents
  through `str.join`; NULL hits `TypeError: sequence item 0: expected str
  instance, NoneType found`.


## [0.5.0] - 2026-05-15

### Added

- **`wikibricks.tag_logic`** — pure helpers for LLM-driven auto-tagging:
  `parse_tag_response`, `normalize_slug`, `dedupe_against_vocabulary`,
  `should_approve`, `prefix_llm`, `build_tag_event`. Library stays
  LLM-free; the LLM call lives in `notebooks/wiki_tag.py`.
- **`WikiClient.upsert_vocabulary(observations, approve_threshold)`** —
  batched MERGE into `wiki_vocabulary` with auto-approval at threshold.
- **`WikiClient.append_page_tags(path, tags)`** — append tags to
  `pages.tags`, deduped via `array_distinct(array_union(...))`.
- **`notebooks/wiki_tag.py`** — new opt-in task in `wikibricks_curate`,
  proposes 3-5 semantic tags per recent top-level page via FMAPI,
  writes vocab + page tags, logs `auto_tag` events.
- **`wikibricks.ops.VOCABULARY_TABLE`** + DDL in `create_tables_sql()`
  for the `wiki_vocabulary` table.
- **`wikibricks.ops.create_agent_traces_view_sql()`** — DDL for
  `agent_traces_v` view over `wiki_log` (search rows with returned
  paths). Promote consumes this view instead of the default
  `agent_traces` table.
- **`wikibricks.health`** + `scripts/wikibricks_health.py` — six-probe
  health oracle for the v0.5.0 feature set (auto-tag coverage, vocab
  growth, page-tag coverage, citations logged, promote end-to-end,
  curate recency). Exits non-zero when any probe fails.
- **`tests/test_job_yaml_contract.py`** — YAML-level contract test
  asserting the four-task DAG and `traces_table` wiring.

### Changed

- **`WikiClient.search()`** now logs `{returned_paths, k, mode}` in
  `wiki_log.details` (citation tracking). Enables the promote pipeline
  to mine real agent traces and the health probe to count citations.
- **`resources/wiki_curate_job.yml`** gains a `tag` task and points
  promote's `traces_table` at `${var.catalog}.${var.schema}.agent_traces_v`.

## [0.3.1] - 2026-05-05

### Added

- **`wikibricks.curate_logic.run_connect_phase`** — pure helper that fans
  `propose_fn` across paths via `ThreadPoolExecutor` and batches one
  `commit_fn` call at the end. Used by `notebooks/wiki_curate.py`.
- **`wiki_curate` notebook** gains a `propose_concurrency` widget
  (default 8). The bundle resource sets the same default for scheduled
  runs.

### Performance

- **`notebooks/wiki_curate.py` connect phase** now runs `propose_edges`
  in parallel up to `propose_concurrency` workers and commits all
  high-confidence edges in a single MERGE INTO links instead of one
  MERGE per page. On the personal philipp wiki (~92 candidate pages
  per run on serverless) this drops the connect phase from ~9 min to
  ~1-2 min wall time.

### Fixed

- **`notebooks/wiki_curate.py` connect filter** restricts the recent-
  pages window to `parent_id IS NULL` and
  `created_by NOT IN ('segregate', 'promote')`. Segregate-produced
  chunk children dominated the prior 48h lookback after a single big
  segregate run (984 of 1074 "recent" pages on 2026-05-05); the loop
  was processing stale chunks instead of new agent writes.
- **`WikiClient.propose_edges`** accepts an optional `other_pages`
  argument. Batch callers can pre-fetch `list_pages()` once and pass it
  in, collapsing N list_pages SQL round-trips into 1. Default behavior
  unchanged.

## [0.3.0] - 2026-05-04

### Fixed

- **Curate / segregate / promote notebooks** set `WIKIBRICKS_CATALOG`
  and `WIKIBRICKS_SCHEMA` from job widgets before importing
  `wikibricks.ops` — `ops` reads them at module-load time and was
  silently resolving every table to `main.wiki` (latent since the
  recorder shipped). `resources/wiki_curate_job.yml` now passes
  `catalog` / `schema` widgets to all three task `base_parameters`.
- **Phase 4 health check** in the curate notebook used the wrong
  column names (`id`, `body`) — corrected to `page_id` / `content_text`
  via SQL aliases so `classify_page_health` / `find_duplicate_paths`
  keep working unchanged.
- **`run_sql` in segregate** used `wait_timeout="60s"`, which the
  Databricks Statements API rejects — capped at 50s, lowered to 30s
  for consistency with curate.

### Performance

- **`WikiClient.commit_edges`** now batches into a single MERGE
  (multi-row VALUES source) instead of one MERGE per edge. At scale
  (60 edges per session page × 66 pages updated in 48h) this turns 3960
  round-trips into 66, dropping a ~3-hour curate phase to ~3 minutes.
- **`WikiClient.propose_edges`** drops the N+1 `SELECT page_id FROM
  pages WHERE path = ...` per matching title — `list_pages()` now
  returns `page_id` (additive change, no breaking callers) and
  `propose_edges` reads it directly. The shipped curate job lowers
  `max_pages_per_run` default from 500 → 100 to keep cold-start
  serverless runs inside the 30-min task budget; pages beyond the cap
  roll forward into the next nightly window.
- **New `WikiClient.write_pages(pages: list[dict])`** does real batched
  writes — exactly four SQL statements regardless of N (history INSERT,
  pages MERGE, pages_vs_source MERGE, wiki_log INSERT). `bulk_write_pages`
  delegates to it. `notebooks/wiki_segregate.py` collects parent + chunk
  children into one `wiki.write_pages(...)` call per oversize page,
  collapsing 6× round-trips per page into 1×. End-to-end: a curate run
  that previously timed out at 30 min now completes all three tasks
  in ~32 min on cold serverless including the full segregate workload.

### Changed

- Plugin launcher's `WIKIBRICKS_PLUGIN_REF` default switched from `main`
  to `v0.3.0` so installs are reproducible by default. Override to
  `main` (or any other ref) for bleeding-edge.
- `plugin/README.md` rewritten with a two-half install (workspace bundle
  deploy first, plugin install second), corrected
  `WIKIBRICKS_RECORDER_DIR` default (`~/.wikibricks_recorder/`, not
  `~/.wikibricks/sessions/`), and added the missing `WIKIBRICKS_TARGET`
  row to the env-var table.
- Root `README.md` restructured to lead with the personal recorder as
  the 5-minute on-ramp (was buried 65% through the document). Trimmed
  448 → 203 lines: dropped redundant "Why" section, compressed the
  maintenance-loop description, cut deploy-customization sub-sections
  that moved one link away to `databricks.yml`, cut the team-shared
  `.mcp.json` snippet (superseded by the plugin's own auto-registering
  `.mcp.json`), and replaced pre-plugin install instructions
  (`uv pip install -e ".[recorder]"` + `claude mcp add`) with the
  marketplace install path. Test count 453 → 491; wheel filename
  0.2.0 → 0.3.0.

### Added

- **`wikibricks-recorder` Claude Code plugin** at `plugin/`. Users install
  via marketplace flow instead of hand-editing `~/.claude/settings.json`:
  ```
  /plugin marketplace add https://github.com/philtief/wikibricks-dev.git
  /plugin install wikibricks-recorder@wikibricks
  ```
  Plugin ships:
  - `.claude-plugin/plugin.json` — full manifest (name, description,
    version, homepage, repository, license, keywords, author).
  - `hooks/hooks.json` — 5 events (SessionStart 60s, UserPromptSubmit 5s,
    PostToolUse 5s, Stop 30s, SessionEnd 30s) routed through `bin/launch.sh`.
  - `.mcp.json` — `wiki` stdio MCP server, auto-registers without
    `claude mcp add`. Tools surfaced as
    `mcp__plugin_wikibricks-recorder_wiki__*`.
  - `bin/launch.sh` — idempotent `uv tool install` from Git URL into
    `${CLAUDE_PLUGIN_DATA}` on first call (~5s cold), exec's cached binary
    thereafter (~70ms warm). Override Git ref / URL via
    `WIKIBRICKS_PLUGIN_REF` / `WIKIBRICKS_PLUGIN_GIT`.
- **Repo-root `.claude-plugin/marketplace.json`** registers the plugin in
  the `wikibricks` marketplace so a single `claude plugin marketplace add`
  picks up future plugins from the same repo.
- **`wikibricks-recorder-hook` console script** wired to
  `wikibricks_recorder.hooks:main`. Lets the plugin launcher exec a
  binary instead of `python -m wikibricks_recorder.hooks`.
- **`wikibricks_recorder.wiki_mcp.format_tool_response()`** — extracted
  the MCP `call_tool` error-wrapping path into a sync helper. Unknown
  tools, raising tools, and bad kwargs all return `{"error": "..."}` JSON
  instead of crashing the stdio loop. Five new robustness tests in
  `tests/test_recorder_wiki_mcp.py::TestFormatToolResponse`.
- **`tests/test_plugin_manifest.py`** — 16 manifest tests covering plugin
  fields, version sync with `pyproject.toml`, hook events + timeouts,
  MCP server entry, launcher executability, and marketplace consistency.
- README "Team-shared MCP via `.mcp.json`" section — show how a team commits
  one `.mcp.json` at the repo root that pins the recorder to a Git ref, so
  every contributor's Claude Code session registers the same `wiki` server
  without each developer running `claude mcp add`. Documents the file://
  portability caveat and the per-machine nature of hooks. (Largely
  superseded by the plugin's own `.mcp.json` from 0.3.0; kept for
  non-plugin / multi-server team setups.)
- `wiki-init --install-hooks` — auto-merge the five recorder hooks into
  `~/.claude/settings.json` (existing entries preserved, file backed up
  first). Replaces the manual `sed examples/claude-settings.json` step.
  Honors `--python` and `--settings` for non-default paths. Marked
  legacy in 0.3.0 — recommended install path is now the plugin.
- `wiki-init --install-hooks --scope {user,project,local}` — match the
  `claude mcp add` UX. `user` (default) writes to `~/.claude/settings.json`;
  `project` writes to `./.claude/settings.json` (team-shared, commit to
  git); `local` writes to `./.claude/settings.local.json` (personal-per-
  project, gitignored). `--scope` and `--settings` are mutually exclusive.
- `wiki-init --uninstall-hooks` — inverse of `--install-hooks`. Matches
  recorder entries by exact command string, leaves any non-recorder hooks
  untouched, drops empty event arrays, and backs up before writing.
  Honors the same `--scope` / `--settings` / `--python` flags.
- README "Recorder" section now lists every MCP tool's required and
  optional arguments, sourced from `wiki_mcp.py::get_tool_schemas()`.

### Fixed

- `wiki-init` personal-flow Next-steps message: replaced the broken
  `uvx --from . '.[recorder]'` invocation with the correct
  `uvx --from "wikibricks[recorder] @ file://$(pwd)"` form, and pointed
  users at the new `--install-hooks` flag.
- `wiki_mcp.py` module docstring: updated the stale
  `claude mcp add wiki -- uvx --from . wikibricks-mcp` example to the
  working PEP 508 form (`--scope user`, absolute `file://` URL).

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

[Unreleased]: https://github.com/philtief/wikibricks/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/philtief/wikibricks/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/philtief/wikibricks/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/philtief/wikibricks/compare/v0.1.5...v0.2.0
[0.1.5]: https://github.com/philtief/wikibricks/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/philtief/wikibricks/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/philtief/wikibricks/compare/v0.1.0...v0.1.3
[0.1.0]: https://github.com/philtief/wikibricks/releases/tag/v0.1.0
