# Changelog

All notable changes to WikiBricks are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  using `[redacted-model]`. Three search modes: HYBRID (default), ANN,
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
  (5 meta-pages), `custom` (JSONL), `none`.
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
- **Unit tests** (`tests/`), no Databricks connectivity required.
- **Documentation**: `README.md`, `docs/img/architecture.{mmd,svg,png}`.

[Unreleased]: https://github.com/philtief/wikibricks/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/philtief/wikibricks/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/philtief/wikibricks/compare/v0.1.0...v0.1.3
[0.1.0]: https://github.com/philtief/wikibricks/releases/tag/v0.1.0
