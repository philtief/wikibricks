# AGENTS.md

These instructions apply to coding agents that modify WikiBricks. Users start
with `README.md`.

## Product contract

WikiBricks is local PostgreSQL memory for AI agents. `WikiClient()` and
`wikibricks-mcp` use local PostgreSQL by default. Recording, search, reads,
writes, history, and MCP tools must work without network access, Databricks
credentials, or `databricks-sdk`.

Lakebase is an optional archive. Only `wikibricks sync lakebase` can contact
it. The legacy Databricks client remains available when a caller installs the
`databricks` extra and passes a SQL warehouse ID explicitly.

The library core is LLM-free. The calling agent decides what to search, write,
or promote.

## Repository layout

```text
src/wikibricks/
  client.py                 Public local-first facade and legacy entry point
  local_client.py           Local WikiClient contract
  postgres_store.py         PostgreSQL transactions and search
  models.py                 Harness-neutral session model
  session_ingest.py         Stable identities, paths, hashes, and tags
  adapters/                 Claude Code, Omnigent, and JSONL adapters
  sql/migrations/           Shared local and Lakebase migrations
  mcp_server.py             Harness-neutral stdio MCP server
  cli.py                    Local lifecycle, import, search, and sync commands

src/wikibricks_recorder/
  local_hooks.py            Claude Code hook adapter for local PostgreSQL
  session.py                Durable pre-flush event buffer
  page_builder.py           Utility-session filters

src/wikibricks_databricks/
  lakebase_sync.py          Explicit archive push and curated snapshot pull

tests/                      Unit, PostgreSQL, package, MCP, and sync tests
plugin/                     Optional Claude Code adapter
resources/, notebooks/     Legacy and future optional Databricks assets
docs/validation/            Evidence from local and remote release gates
```

## Hard rules

1. Normal local work cannot make a network call or import Databricks code.
2. Keep all LLM calls out of `src/wikibricks/`.
3. Store long sessions as ordered event versions in PostgreSQL `text`. Do not
   append a full transcript to one growing row.
4. Preserve raw text. Search indexes use bounded chunks and reconstruct reads
   from event content.
5. `pg_trgm` is the only required PostgreSQL extension. Built-in `tsvector`
   and GIN implement full-text search. Keep `vector` optional.
6. A local write and its outbox event belong in one transaction.
7. Lakebase sync must be explicit, bounded, idempotent, and resumable. Do not
   add remote fallback to `WikiClient()` or the MCP server.
8. Import Databricks SDK modules lazily inside optional remote operations.
9. Do not add raw REST calls. Use the Databricks SDK for control-plane work
   and SQL for data operations.
10. Do not hardcode a workspace ID, user path, credential, or access token.
11. Do not push, publish, deploy, modify remote data, or edit the public mirror
    without explicit approval.
12. Do not use `git reset --hard`, `git checkout --`, `--no-verify`, or
    `git commit --amend`.

## Session and MCP contracts

Every adapter produces a `SessionRecord`. A session has a harness, external
ID, user ID, optional agent and workspace, source timestamps, metadata, and an
ordered list of `SessionEvent` values.

Supported event kinds are:

- `user`
- `assistant`
- `tool_call`
- `tool_result`
- `error`
- `lifecycle`

Session identity is `(harness, external_id)`. Event identity is stable within
that session. An exact re-import is a no-op. A changed source event creates one
new immutable event version.

The MCP server exposes exactly these local tools:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

Tool descriptions must stay independent of Claude, Codex, Omnigent,
Databricks, and a specific MCP client.

## PostgreSQL contract

Migrations are numbered, forward-only SQL files. They acquire an advisory
transaction lock and record applied filenames in `schema_migrations`.

PostgreSQL native `text` and TOAST store long content. `jsonb` stores
structured metadata. `timestamptz` stores timestamps. UUIDs identify pages,
versions, sessions, events, and archive batches.

Search chunks have a hard 64 KiB UTF-8 ceiling. Use `to_tsvector('simple',
...)`, `websearch_to_tsquery('simple', ...)`, and GIN. Trigram indexes cover
paths and titles, not transcript bodies.

Every page or event version is immutable. Current-version pointers live on the
stable entity rows. Outbox rows reference immutable versions instead of
duplicating their content.

## Development workflow

Overnight development is mandatory. `.overnight-dev.json` defines the gates:

```json
{
  "lintCommand": "uv run ruff check src tests scripts",
  "testCommand": "uv run pytest",
  "autoFix": false,
  "minCoverage": 0
}
```

The repository pre-commit hook runs both commands. Write a failing test first,
implement the smallest change, run the focused test, then run the full gates.
Commit only when both gates pass.

Use these commands:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests scripts
uv build
UV_OFFLINE=1 uv run pytest
```

PostgreSQL integration tests create disposable databases. Do not point their
fixtures at a database that contains user data.

## Definition of done for local changes

A local-first release candidate must pass all of these checks:

- Full pytest and Ruff gates
- Offline pytest gate
- Wheel build and clean-environment install
- Wheel metadata check with no required Databricks dependency
- Packaged SQL migration check
- Clean-home initialization and real stdio MCP test
- Deterministic local curation, index repair, duplicate/orphan reporting, and
  archive-gated retention
- PostgreSQL backup and restore hash comparison
- Omnigent import with Codex metadata
- Remote failure and idempotent retry against a second local PostgreSQL
  database

Record release evidence in `docs/validation/local-first-local-gate.md`.

## Version and release checklist

For a version change, update these files together:

1. `pyproject.toml`
2. `uv.lock`
3. `plugin/.claude-plugin/plugin.json`
4. `CHANGELOG.md` and its comparison links
5. README test count and wheel filename
6. Notebook wheel pins that specify an exact version

Do not set `WIKIBRICKS_PLUGIN_REF` to a tag that does not exist. The local
release-candidate commit can keep the launcher on the last published tag and
document the release-time update.

## Private development and public mirror

The private development repository includes benchmarks, generated results,
and operational assets that do not belong in the public mirror. Keep the
existing dev-only exclusions when the public sync happens:

```text
scripts/hotpot_*.py
scripts/twowiki_*.py
scripts/build_hotpot_seed.py
scripts/build_twowiki_seed.py
scripts/fetch_hotpot.py
scripts/fetch_twowiki.py
src/wikibricks/seeds/hotpot/
src/wikibricks/seeds/twowiki/
tests/test_build_hotpot_seed.py
tests/test_eval_metrics.py
vendor/2wikimultihop_evaluate_v1.1.py
docs/hotpotqa_evaluation.md
docs/twowiki_evaluation.md
notebooks/benchmark_hotpot.py
examples/hotpotqa.md
examples/twowiki.md
```

Before public release, replace private Git URLs with
`https://github.com/philtief/wikibricks.git`, strip dev-only README sections,
run the complete gate in the public worktree, and show the diff for approval.

## Remote phase

Remote work starts only after the local validation report is approved. The
remote phase will add Lakebase Change Data Feed, Delta history, monthly heavy
curation, hash-based patch sets, and non-destructive migration of the current
remote wiki. Local apply accepts a patch only when its base hash still matches.
Conflicts remain local review items.

Select the Databricks CLI profile explicitly. Use scale-to-zero resources.
Run bundle validation before deployment. Record resource IDs, job runs,
counts, hashes, and retry assertions in a separate remote validation report.
Never delete the old Delta or Unity Catalog data during migration.
