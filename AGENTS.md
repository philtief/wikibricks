# AGENTS.md — instructions for coding agents working on WikiBricks

This file is for LLM coding agents asked to modify this repo. Humans should
read `README.md` first.

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
  agent_tools.py           Custom agent-tool factory for write ops (DML)
  promote_logic.py         Pure helpers for the promote notebook
  curate_logic.py          Pure helpers for the curate notebook
  seeds/                   Domain-agnostic seed loaders
src/wikibricks_recorder/   Optional Claude Code → wiki bridge (consumer-side)
  hooks.py                 SessionStart/UserPromptSubmit/Stop/SessionEnd dispatch
  init_cli.py              `wiki-init` — interactive personal/team config
  target_cli.py            `wiki-target` — switch active wiki per task
  wiki_mcp.py              Stdio MCP server for read+write from Claude Code
  config.py                Multi-wiki TOML resolver
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

## Two repos: `wikibricks-dev` (private) vs `wikibricks` (public)

This repo is the development home and ships **everything**, including the
benchmark / evaluation suites used to validate `WikiClient.search` and the
managed-MCP retrieval surface. The public repo at
[philtief/wikibricks](https://github.com/philtief/wikibricks) is a curated
mirror that ships only the library, app, deploy notebooks, and unit tests —
**no benchmark code, results, or eval-only tests**.

### Dev-only paths — DO NOT publish to public

Anything matching one of these globs stays in `wikibricks-dev`. When syncing
dev → public, exclude them. When adding new benchmark / eval material, place
it under one of these prefixes so the cut stays mechanical.

```
scripts/build_hotpot_seed.py
scripts/fetch_hotpot.py
scripts/hotpot_*.py                  # hotpot_01_setup … hotpot_04_render
scripts/build_twowiki_seed.py
scripts/fetch_twowiki.py
scripts/twowiki_*.py                 # twowiki_01_setup … twowiki_06_render, _quick_eval, _variants
scripts/twowiki_batch_loop.sh
src/wikibricks/seeds/hotpot/         # HotPotQA seed loader
src/wikibricks/seeds/twowiki/        # 2WikiMultiHopQA seed loader
tests/test_build_hotpot_seed.py
tests/test_eval_metrics.py
vendor/2wikimultihop_evaluate_v1.1.py
docs/hotpotqa_evaluation.md
docs/twowiki_evaluation.md
```

Everything outside this list is public-eligible. `scripts/diagnose_traces.py`,
`scripts/seed_traces_fixture.py`, `scripts/smoke_segregate.py`, and
`scripts/sdk_redeploy.py` are diagnostic / operational tools and ship to
public. The standard unit-test suite (`tests/test_client.py`,
`tests/test_curate_logic.py`, …) ships to public too — only the benchmark
tests above are dev-only. **`src/wikibricks_recorder/` and
`tests/test_recorder_*.py` ship to public** — the recorder is consumer-side
tooling that any user can opt into via `pip install wikibricks[recorder]`.

## Hard rules — do not violate

1. **No LLM calls inside `src/wikibricks/`.** The library is a storage contract.
   All LLM work lives in `notebooks/promote_from_traces.py` or user code. If
   you think you need an LLM in `src/wikibricks/`, the design is wrong —
   surface the tradeoff to the user, don't silently add a call. *Scope:*
   this rule applies to the library package only; `src/wikibricks_recorder/`
   is consumer-side tooling and may interact with LLMs (today it doesn't,
   but the rule does not bind it).
2. **No FastMCP or bespoke MCP server *for the library*.** UC functions are
   the library's MCP surface via Databricks managed MCP at
   `/api/2.0/mcp/functions/<catalog>/<schema>`. The recorder ships its own
   stdio MCP server in `src/wikibricks_recorder/wiki_mcp.py` because UC
   functions cannot do DML and Claude Code needs both read + write — that
   is a *consumer-side* tool, not a library surface, and is allowed.
3. **No REST API calls from user-facing code.** Always use the Databricks SDK
   (`databricks.sdk.WorkspaceClient`). The only exception is the vendored
   2WikiMultiHopQA eval script.
4. **No hardcoded workspace IDs in the repo.** `databricks.yml` uses generic
   defaults (`catalog=main`, no `warehouse_id` default); the app reads env
   vars with no workspace-specific fallbacks. Workspace specifics go in
   `databricks.override.yml` (gitignored).
5. **No destructive git operations without explicit user confirmation.** No
   `git push --force`, no `git reset --hard`, no branch deletion. Keep
   commits local until tests are green and a push is explicitly requested.

## Commands you will actually run

```bash
uv sync                              # install deps into .venv
uv sync --extra recorder             # also install the optional recorder package
uv run pytest                        # 453 tests, no workspace needed
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

## Platform gotchas

- **`INSERT INTO t VALUES (uuid(), ...)` on a SQL warehouse** — rejected as
  `INVALID_INLINE_TABLE.CANNOT_EVALUATE_EXPRESSION_IN_INLINE_TABLE`. Use
  `INSERT INTO t (cols) SELECT uuid(), ...` instead.
- **`MagicMock()` without `spec_set`** hides method drift. Use
  `MagicMock(spec_set=WikiClient)` so a typo or removed method fails loudly.
- **Databricks Apps listen on port 8000, not 8080.** `app/app.yaml` already
  has `--server.port=8000`. Flipping to 8080 causes a 502 at the proxy.
- **Streamlit `AppTest` session_state does not support `.get()`.** Use
  `at.session_state["key"]`, not `at.session_state.get("key")`.
- **`dbutils.fs.cp("file:...")` fails on serverless.** Use Spark `.write` to
  Volumes instead.
- **Judge threshold is on an integer scale (1–5).** Keep thresholds at
  integer grid points unless you're changing the prompt.
- **`ChatAgentMessage` (MLflow 3) requires `id=str(uuid.uuid4())`.** Missing
  `id` produces confusing deserialization errors.

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
`fn_wiki_search` (HYBRID `vector_search()` TVF), `fn_wiki_read`,
`fn_wiki_history`, `fn_wiki_log`, `fn_wiki_index`, `fn_wiki_schema`,
`fn_wiki_write_help`. Every parameter has a `COMMENT`; agents discover
these via the MCP endpoint.

Write operations (DML) are exposed through `wikibricks.make_agent_tools`,
not UC functions — SQL UDFs cannot MERGE. Register the returned
`wiki_write_page` and `wiki_promote_answer` callables with your agent
framework to give an agent direct promote-to-memory capability.

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
| `segregate` | A page was split into a parent + N chunk children |
| `segregate_skip` | An oversize page could not be split (single paragraph too large) |

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
| Recorder runtime config (per-machine) | `~/.wikibricks-recorder.toml` (written by `wiki-init`) |
| Recorder active wiki (per-machine) | `~/.wikibricks/active-target` (written by `wiki-target`) |
| Recorder hook commands | `~/.claude/settings.json` (template at `examples/claude-settings.json`) |

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
