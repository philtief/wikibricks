# Changelog

All notable changes to WikiBricks are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/philtief/wikibricks/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/philtief/wikibricks/releases/tag/v0.1.0
