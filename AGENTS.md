# AGENTS.md — instructions for coding agents working on WikiBricks

This file is for LLM coding agents (Claude Code, Cursor, Cortex, Copilot CLI,
etc.) asked to modify this repo. Humans should read `README.md` first.
Claude Code reads `CLAUDE.md` by default — a symlink is provided.

## What WikiBricks is (1 paragraph)

A Databricks Asset Bundle that deploys a Delta + Vector Search + Unity
Catalog wiki store and exposes it as native MCP tools. The calling agent is
the only LLM in the loop — **the library core is LLM-free** (see "Hard rules"
below). One shipped Lakeflow Job (`wikibricks_curate`) runs daily: a
deterministic `curate` task (library contract) and an optional LLM-driven
`promote` task (mines agent traces into canonical pages).

## Repo layout

```
src/wikibricks/            Library core — LLM-free, governs the public API
  client.py                WikiClient (Python API)
  ops.py                   DDL + UC function CREATE statements
  promote_logic.py         Pure helpers for the promote notebook
  curate_logic.py          Pure helpers for the curate notebook
  seeds/                   Domain-agnostic seed loaders
notebooks/                 Databricks notebooks (deploy + curate + promote + eval)
  deploy_wiki_store.py     Creates schema, tables, VS index, UC functions
  wiki_curate.py           Curate task — deterministic, no LLM
  promote_from_traces.py   Promote task — LLM-driven, opt-in
resources/                 Bundle resource definitions (job, app, dashboards)
app/                       Streamlit reference app
scripts/                   Evaluation + diagnostic CLIs (hotpot_*, twowiki_*, diagnose_traces)
tests/                     pytest suite — runs without a workspace
docs/                      Evaluation reports + architecture diagrams
vendor/                    Vendored third-party (2WikiMultiHopQA evaluator)
databricks.yml             Bundle config (gets merged with databricks.override.yml)
databricks.override.example.yml   Template for workspace-specific overrides
pyproject.toml             Package config — version lives here
CHANGELOG.md               Keep-a-Changelog format, SemVer
```

## Hard rules — do not violate

1. **No LLM calls inside `src/wikibricks/`.** The library is a storage contract.
   All LLM work lives in `notebooks/promote_from_traces.py` or user code. If
   you think you need an LLM in `src/`, the design is wrong — surface the
   tradeoff to the user, don't silently add a call.
2. **No FastMCP or bespoke MCP server.** UC functions are the MCP surface via
   Databricks managed MCP at `/api/2.0/mcp/functions/<catalog>/<schema>`.
3. **No REST API calls from user-facing code.** Always use the Databricks SDK
   (`databricks.sdk.WorkspaceClient`). The only exception is the vendored
   2WikiMultiHopQA eval script.
4. **No hardcoded workspace IDs in the repo.** `databricks.yml` uses generic
   defaults (`catalog=main`, no `warehouse_id` default); the app reads env
   vars with no workspace-specific fallbacks. Workspace specifics go in
   `databricks.override.yml` (gitignored).
5. **No destructive git operations without explicit user confirmation.** No
   `git push --force`, no `git reset --hard`, no branch deletion. Do not
   push to `main` — the pre-push hook allows it but the user expects local
   commits only unless told otherwise.

## Commands you will actually run

```bash
uv sync                              # install deps into .venv
uv run pytest                        # 306 tests, no workspace needed
uv run pytest tests/test_client.py   # run a single file
uv run ruff check src tests scripts  # lint
uv run ruff format src tests scripts # format
uv build                             # build wheel → dist/wikibricks-*.whl
```

Bundle + deployment (require `databricks.override.yml` with a valid profile):

```bash
databricks bundle validate --target dev
databricks bundle deploy --target dev
databricks bundle run deploy_wiki_store --target dev
```

## TDD workflow (pre-commit hook enforces)

The pre-commit hook runs lint + tests on every commit. A commit blocked by
the hook means the commit did NOT happen — fix the problem and create a new
commit; **never use `--amend` or `--no-verify`** (the former would modify the
previous commit, the latter bypasses quality gates).

Write a test first. Make it fail. Implement. Make it pass. Commit.

## Versioning + release checklist

When bumping the library version (e.g. 0.1.4 → 0.1.5):

1. `pyproject.toml` — `version = "0.1.5"`
2. **Every notebook's `%pip install` line** — `grep -rn "wikibricks-.*\.whl" notebooks/` and bump all of them. Missing one ships a mismatched wheel silently.
3. `CHANGELOG.md` — new `## [0.1.5] - YYYY-MM-DD` section under `[Unreleased]`. Use Added / Changed / Fixed / Deprecated / Removed / Security headings. Update the `[Unreleased]` compare link at the bottom.
4. `README.md` — update test count + wheel filename in the Development section if they've moved.
5. Run `uv build` to produce the new wheel, copy it to `app/` if the app bundles it.

## Known-wrong patterns (will burn you)

- **`INSERT INTO t VALUES (uuid(), ...)` on a SQL warehouse** — rejected as
  `INVALID_INLINE_TABLE.CANNOT_EVALUATE_EXPRESSION_IN_INLINE_TABLE`. Use
  `INSERT INTO t (cols) SELECT uuid(), ...` instead. This bit `_log` silently
  in 0.1.0–0.1.3; see 0.1.4 fix in `src/wikibricks/client.py::_log`.
- **`MagicMock()` without `spec_set`** hides method drift. Cross-notebook DAG
  tests (`tests/test_job_dag.py`) use `MagicMock(spec_set=WikiClient)` so a
  typo or removed method fails loudly. Follow that pattern for anything that
  mocks `WikiClient`.
- **Databricks Apps listen on port 8000, not 8080.** `app/app.yaml` already
  has `--server.port=8000`. If you touch it and flip to 8080, the proxy will
  return 502 "App Not Available" even though the app is running.
- **Streamlit `AppTest` session_state does not support `.get()`.** Use
  `at.session_state["key"]`, not `at.session_state.get("key")`.
- **`dbutils.fs.cp("file:...")` fails on serverless.** Use Spark `.write` to
  Volumes instead.
- **Judge threshold is on an integer scale (1–5).** The prompt asks for a
  single digit. `4.5` rejects every real score; `4.0` admits 4 and 5. Keep
  thresholds at integer grid points unless you're changing the prompt.
- **`ChatAgentMessage` (MLflow 3) requires `id=str(uuid.uuid4())`.** Missing
  `id` is a common source of confusing deserialization errors.

## WikiClient API surface (stable)

`WikiClient` lives at `src/wikibricks/client.py` and is the library contract.
Adding, renaming, or removing a method is a breaking change — bump the minor
version and document it in CHANGELOG. Current methods:

- `write_page`, `bulk_write_pages`, `read_page`, `list_pages`, `history`
- `search` (modes: `HYBRID` / `ANN` / `FULL_TEXT`)
- `ingest_source`, `promote_answer`, `materialize_index`, `sync_index`
- `propose_edges`, `commit_edges`, `graph_neighbors`, `fix_broken_links`
- `_log` (private, used by notebooks; `spec_set` allows it)

UC functions exposed via MCP (defined in `src/wikibricks/ops.py`):
`fn_wiki_search`, `fn_wiki_read`, `fn_wiki_history`, `fn_wiki_log`,
`fn_wiki_index`, `fn_wiki_schema`, `fn_wiki_write_help`. Every parameter has
a `COMMENT`; agents discover these via the MCP endpoint.

## Telemetry — `wiki_log` op_types

| `op_type` | Meaning |
|---|---|
| `write` | `write_page` call |
| `read` | `read_page` call |
| `search` | `search` call |
| `promote` | A cluster passed the judge and was written |
| `promote_reject` | Judge score below threshold — legitimate low quality |
| `promote_parse_fail` | Judge returned non-numeric text — prompt drift |
| `vs_sync` / `vs_sync_fail` | `sync_index()` result |
| `verify_fix` | `fix_broken_links` healed an edge |
| `curate_run` | End-of-run summary from the curate notebook |

Never invent new op_types silently — add a row to this table and to the
`wiki_log` section in README.md.

## Config surfaces (where to edit what)

| Change | File |
|---|---|
| Add/rename a bundle variable | `databricks.yml` `variables:` |
| Adjust a per-target default | `databricks.override.yml` (local) |
| Add env to the deployed app | `resources/app.yml` `config.env` |
| Add env for local `streamlit run` | `app/app.yaml` `env:` |
| Change a notebook parameter | Notebook widget + `resources/wiki_curate_job.yml` |
| Add a UC function (MCP tool) | `src/wikibricks/ops.py::get_uc_functions` |

## First-time workspace setup

`README.md` → Quick start. The short version: `cp
databricks.override.example.yml databricks.override.yml`, fill in host /
profile / catalog / warehouse_id, `databricks bundle deploy --target dev`.

## When in doubt

- **Ask the user.** Vague "make it better" tasks should get clarifying
  questions, not invented requirements.
- **Prefer editing existing files** to creating new ones.
- **Commit small and often.** Each commit should leave lint + tests green.
- **Keep the user informed.** State what you're about to do in one sentence,
  and report results crisply.
