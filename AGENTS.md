# AGENTS.md

These instructions apply to coding agents that modify WikiBricks. Users start
with `README.md`.

## Product contract

WikiBricks is local PostgreSQL memory for AI agents. `WikiClient()` and
`wikibricks-mcp` must record, search, read, write, and serve history without
network access, Databricks credentials, or `databricks-sdk`.

Lakebase is an optional archive. Only `wikibricks sync lakebase` contacts it.
No local API or MCP operation may fall back to a remote service.

The library does not call a language model. The connected agent decides what
to search, write, link, promote, or merge.

## Repository layout

```text
src/wikibricks/
  client.py                 Public local PostgreSQL client
  models.py                 Harness-neutral session contract
  session_ingest.py         Stable session identities, paths, hashes, and tags
  adapters/                 Claude Code, Omnigent, and JSONL adapters
  config/                   Validated YAML defaults and loader
  storage/                  PostgreSQL facade and focused repositories
  curation/                 Protocol, persistence, planning, and application
  remote/lakebase.py        Optional explicit archive adapter
  resources/                Agent Markdown, MCP JSON, and interchange schemas
  sql/migrations/           Shared local and Lakebase PostgreSQL migrations
  mcp_server.py             Harness-neutral stdio MCP server
  cli.py                    Local lifecycle, import, and sync commands

tests/                      Behavior, PostgreSQL, package, MCP, and sync tests
plugin/                     Optional Claude Code hook and MCP adapter
docs/validation/            Local and remote release evidence
```

`postgres_store.py` and `curation_sync.py` are compatibility export modules.
New implementation code belongs in `storage/` and `curation/`.

## Hard rules

1. Normal local work cannot make a network call or import Databricks code.
2. Keep all LLM calls out of `src/wikibricks/`.
3. Store long sessions as ordered event versions in PostgreSQL `text`. Never
   append a full transcript to one growing row.
4. Preserve raw text. Search indexes use bounded chunks and reads reconstruct
   the original event content.
5. `pg_trgm` is the only required PostgreSQL extension. Built-in `tsvector`
   and GIN implement full-text search.
6. A local write and its outbox event belong in one transaction.
7. Lakebase sync must be explicit, bounded, idempotent, and resumable.
8. Import Databricks SDK modules lazily inside optional remote operations.
9. Use the Databricks SDK for control-plane work and SQL for data operations.
   Do not add raw REST calls.
10. Do not hardcode a workspace ID, user path, credential, or access token.
11. Do not push, publish, deploy, modify remote data, or edit the public mirror
    without explicit approval.
12. Do not use `git reset --hard`, `git checkout --`, `--no-verify`, or
    `git commit --amend`.

## Session and MCP contracts

Every adapter produces a `SessionRecord` with a harness, external ID, user ID,
optional agent and workspace, source timestamps, metadata, and ordered
`SessionEvent` values.

Supported event kinds are `user`, `assistant`, `tool_call`, `tool_result`,
`error`, and `lifecycle`. Session identity is `(harness, external_id)`. Event
identity is stable within the session. An exact re-import is a no-op; a changed
source event creates one immutable version.

The MCP server exposes exactly these tools:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

Descriptions and JSON schemas must remain independent of Claude, Codex,
Omnigent, Databricks, and any specific MCP client.

## PostgreSQL contract

Migrations are numbered, forward-only SQL files. They take an advisory
transaction lock and record applied filenames in `schema_migrations`.

Use PostgreSQL `text` and TOAST for long content, `jsonb` for structured
metadata, `timestamptz` for source time, and UUIDs for stable entities. Search
chunks have a hard 64 KiB UTF-8 ceiling. Trigram indexes cover paths and
titles, not transcript bodies.

Page and event versions are immutable. Current-version pointers live on stable
entity rows. Outbox rows reference immutable versions instead of copying their
content.

## Curation contract

Raw sessions are evidence. Agents maintain the smaller linked wiki during
normal work. `wikibricks curate` performs deterministic local repair and
hygiene reporting without a model or network connection.

Remote curation arrives as immutable manifests. Pull, plan, apply, and conflict
resolution are separate operations. Local apply accepts a patch only when the
base version ID and content hash still match. Conflict resolution never deletes
local version history. Cleanup groups update pages, aliases, links, receipts,
and outbox rows in one transaction.

## Development workflow

Overnight development is mandatory. `.overnight-dev.json` defines these gates:

```json
{
  "lintCommand": "uv run ruff check src tests",
  "testCommand": "uv run pytest",
  "autoFix": false,
  "minCoverage": 0
}
```

Write a failing behavior test, confirm the expected failure, implement the
smallest change, then run the focused and full gates. Internal-only extraction
may start from a green characterization test. The pre-commit hook runs Ruff and
the full suite.

```bash
uv sync --extra dev
uv run ruff check src tests
uv run pytest
UV_OFFLINE=1 uv run pytest
uv build
```

PostgreSQL integration tests create disposable databases. Never point their
fixtures at a database that contains user data.

## Definition of done

A local release candidate must pass:

- Ruff and the full PostgreSQL 16 suite
- the same suite with `UV_OFFLINE=1`
- the PostgreSQL 17 integration selection
- wheel build, metadata, resource, and clean-install checks
- a clean-home initialization and installed-wheel stdio MCP test
- backup and restore fingerprint comparison
- Omnigent import with Codex metadata
- deterministic local curation and archive-gated retention
- remote failure and idempotent retry against a second local PostgreSQL
  database

Record commands, versions, counts, and hashes in
`docs/validation/local-first-local-gate.md`.

## Version and release checklist

For a version change, update these files together:

1. `pyproject.toml`
2. `uv.lock`
3. `plugin/.claude-plugin/plugin.json`
4. `CHANGELOG.md` and its comparison links
5. README test count and wheel filename

Do not set `WIKIBRICKS_PLUGIN_REF` to a tag that does not exist. The local
release candidate may keep the launcher on the last published tag.

## Remote phase

Remote work starts only after the local validation report is approved. It adds
Lakebase Change Data Feed, Delta history, monthly curation, and a
non-destructive migration of the current remote wiki. Never delete the old
Delta or Unity Catalog data during migration.

Select the Databricks CLI profile explicitly, use scale-to-zero resources, and
run bundle validation before deployment. Record resource IDs, job runs, counts,
hashes, and retry assertions in a separate remote validation report.
