# WikiBricks agent instructions

These instructions apply to coding agents that modify WikiBricks. Installation
and usage are documented in `README.md`.

## Product boundary

PostgreSQL is the authoritative local memory store. `WikiClient()` and
`wikibricks-mcp` must record, search, read, write, and maintain memory without
network access, Databricks credentials, or the Databricks SDK.

Lakebase is an optional archive. Only an explicit
`wikibricks sync lakebase` command contacts it. The optional weekly job reads
that archive and publishes curation manifests; it never writes to a local
WikiBricks database.

The library does not call a language model. The connected agent owns semantic
decisions.

## Commands

```bash
uv sync --extra dev
uv run ruff check src tests
uv run pytest
UV_OFFLINE=1 uv run pytest
uv build
databricks bundle validate --strict -t staging --profile PROFILE
```

The bundle command is required only when a remote resource changes. Select the
Databricks CLI profile explicitly.

## Repository layout

```text
src/wikibricks/
  client.py                 Public local client
  models.py                 Harness-neutral session contract
  session_ingest.py         Stable session and event identities
  adapters/                 Claude Code, Omnigent, and JSONL adapters
  config/                   YAML defaults and validation
  storage/                  PostgreSQL repositories
  curation/                 Local planning and guarded patch application
  remote/lakebase.py        Explicit optional archive adapter
  resources/                Agent guidance and JSON contracts
  sql/migrations/           Forward-only PostgreSQL migrations
  mcp_server.py             Harness-neutral stdio MCP server
  cli.py                    Local lifecycle, import, and sync commands

src/wikibricks_remote/      Optional weekly curation job
resources/                  Databricks bundle resources
tests/                      Local, PostgreSQL, MCP, package, and remote tests
plugin/                     Optional Claude Code adapter
docs/                       Current protocol and validation evidence
```

`postgres_store.py` and `curation_sync.py` are compatibility exports. Put new
implementation code in `storage/` and `curation/`.

## Required invariants

1. Normal local work cannot make a network call or import Databricks code.
2. Keep all LLM calls out of `src/wikibricks/`.
3. Store long sessions as immutable ordered event versions in PostgreSQL
   `text`. Never append a transcript to one growing row.
4. Preserve raw event text. Search bounded chunks and reconstruct reads from
   the original content.
5. `pg_trgm` is the only required extension. Built-in `tsvector` and GIN
   provide full-text search.
6. A local write and its outbox event belong in one transaction.
7. Lakebase sync must be explicit, bounded, idempotent, and resumable.
8. Import Databricks SDK modules lazily inside remote operations.
9. Use the Databricks SDK for control-plane work and SQL for data operations.
10. Never hardcode a workspace ID, user path, connection string, or token.
11. Never push, deploy, publish, or modify remote data without approval.
12. Never use `git reset --hard`, `git checkout --`, `--no-verify`, or
    `git commit --amend`.

## Session and MCP contracts

Every adapter produces a `SessionRecord` with a harness, external ID, user ID,
optional agent and workspace, source timestamps, metadata, and ordered events.
Session identity is `(harness, external_id)`. Exact re-imports are no-ops;
changed source events create immutable versions.

The MCP server exposes exactly these harness-neutral tools:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

## PostgreSQL and curation

Migrations are numbered, forward-only SQL files. They take an advisory lock and
record applied filenames in `schema_migrations`.

Use `text` and TOAST for long content, `jsonb` for structured metadata,
`timestamptz` for source time, and UUIDs for stable entities. Search chunks
have a 64 KiB UTF-8 limit. Trigram indexes cover paths and titles, not session
bodies.

Raw sessions are evidence. Agents maintain the smaller linked wiki during
normal work. `wikibricks curate` performs deterministic offline repair and
hygiene reporting.

Remote patches arrive as immutable manifests. Pull, plan, apply, and conflict
resolution are separate operations. Apply a patch only when its base version
ID and content hash match. Preserve local history on every conflict.

## Development workflow

Overnight development is mandatory. `.overnight-dev.json` configures the
pre-commit gate. Write a failing behavior test, confirm the failure, implement
the smallest change, then run the focused and full checks. Human prose does not
need a source-text test.

PostgreSQL tests create disposable databases. Never point test fixtures at a
database containing user data.

## Optional remote job

The Lakeflow Job is a single bounded serverless wheel task scheduled for Sunday
at 04:00 UTC. Every target is paused by default. Enable a schedule only through
an explicit deployment override after validating a staging manifest.

Remote output is data, never SQL or executable code. The local database accepts
it only through the guarded curation protocol in `docs/curation-sync.md`.

Record staging commands, resource identifiers, run results, counts, and hashes
in `docs/validation/lakebase-remote-staging.md`.

## Release checklist

For a version change, update `pyproject.toml`, `uv.lock`, the plugin
manifest, `CHANGELOG.md`, and installation examples together. Run Ruff, the
full and offline suites, build the wheel, and test a clean installation before
publishing.
