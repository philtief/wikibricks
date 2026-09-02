# WikiBricks agent instructions

These instructions apply to coding agents that modify WikiBricks. Installation
and usage are documented in `README.md`.

## Product boundary

WikiBricks is shared memory for multiple agent harnesses.

- Omnigent is the primary interface. Its Claude, Codex, Debby, Goose, Hermes,
  Kimi, Kiro, OpenCode, Pi, Polly, and Qwen harnesses use one shared WikiBricks
  skill and MCP server. Do not create a separate memory agent or patch Omnigent
  source code.
- Standalone clients connect to the same local database through standard MCP.
- Without Omnigent, the installer configures only detected clients. Keep
  standalone Claude Code and Codex installations supported.
- SQLite at `~/.wikibricks/wikibricks.db` is the default active store.
- Lakebase is an optional archive and curation exchange. Local memory must work
  when Lakebase, Databricks credentials, and the network are absent.

The library does not call a language model. The active agent makes semantic
decisions about page content. Deterministic local maintenance repairs indexes
and reports hygiene problems.

## Commands

```bash
uv sync --extra dev
uv run ruff check src tests
uv run pytest -q
UV_OFFLINE=1 uv run pytest -q
uv build
```

Run the Databricks bundle command only when a remote resource changes. Select
the profile explicitly.

```bash
databricks bundle validate --strict -t staging --profile PROFILE
```

## Repository layout

```text
src/wikibricks/
  client.py                 Public local client
  models.py                 Harness-neutral session contract
  session_ingest.py         Stable session and event identities
  adapters/                 Omnigent recovery and JSONL importers
  config/                   YAML defaults and validation
  storage/sqlite_store.py   Default SQLite store
  storage/                  Shared storage contracts and optional PostgreSQL code
  curation/                 Guarded patch planning and application
  automation.py             Daily local and optional weekly remote scheduler
  omnigent_install.py       Universal skill and MCP installer
  harness_launchers.py      OpenCode and Hermes isolated-runtime adapters
  remote/lakebase.py        Optional Lakebase exchange
  resources/                MCP instructions and JSON contracts
  sql/sqlite/               Forward-only SQLite migrations
  mcp_server.py             Harness-neutral stdio MCP server
  cli.py                    Local lifecycle, import, migration, and sync commands

src/wikibricks_remote/      Optional weekly curation job
resources/                  Databricks Asset Bundle resources
tests/                      Local, compatibility, MCP, package, and remote tests
docs/                       Protocol and validation evidence
```

`postgres_store.py` and `curation_sync.py` are compatibility exports. Put new
implementation code in `storage/` and `curation/`.

## Required invariants

1. Local capture, search, reads, writes, and maintenance cannot require a
   network call or Databricks import.
2. Keep model calls out of `src/wikibricks/`.
3. Store sessions as immutable ordered event versions. Do not append a full
   transcript to one growing row.
4. Preserve original event text. Search bounded chunks and reconstruct reads
   from the stored event versions.
5. A local write and its outbox event belong in one transaction.
6. SQLite must support concurrent host and MCP access through WAL and bounded
   busy timeouts.
7. Lakebase sync is opt-in, bounded, idempotent, and resumable.
8. Import Databricks SDK modules only inside remote operations.
9. Use the Databricks SDK for control-plane work and SQL for data operations.
10. Never hardcode a workspace ID, user path, connection string, or token.
11. Never push, deploy, publish, or modify remote data without approval.
12. Never use `git reset --hard`, `git checkout --`, `--no-verify`, or
    `git commit --amend`.

## Session and MCP contracts

Every adapter produces a `SessionRecord` with a harness, external ID, user ID,
optional agent and workspace, source timestamps, metadata, and ordered events.
Session identity is `(harness, external_id)`. Exact re-imports are no-ops.
Changed source events create immutable versions.

The MCP server exposes exactly these tools to native harnesses and direct clients:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

Do not add client-specific names or schemas. Generic MCP has no portable
session lifecycle, so it must not claim automatic transcript capture. The
Omnigent integration must use public harness configuration. It must not set a
WikiBricks default agent. Preserve unrelated user settings when installing.
Keep `wikibricks install` as the primary setup command. Keep
`wikibricks install omnigent` as a strict compatibility alias.

## Curation and synchronization

Raw sessions are evidence. Agents maintain a smaller set of linked pages during
normal work. `wikibricks curate` performs deterministic offline repair and
hygiene reporting.

Remote patches arrive as immutable manifests. Pull, plan, apply, and conflict
resolution are separate operations. Apply a patch only when its base version
and content hash match. Preserve local history on every conflict. The automatic
safe policy records `keep_local` when a local page has diverged.

The Lakeflow Job is one bounded serverless wheel task scheduled weekly. Every
bundle target is paused by default. Remote output is data, never SQL or
executable code. The job never connects to a local WikiBricks database.

Record staging commands, resource identifiers, run results, counts, and hashes
in `docs/validation/lakebase-remote-staging.md`.

## Development workflow

Overnight development is mandatory. `.overnight-dev.json` configures the
pre-commit gate. Write a failing behavior test, confirm the failure, implement
the smallest change, then run the focused and full checks. Human prose does not
need a source-text test.

Keep tests lean. Prefer one end-to-end contract test over repeated unit tests
for the same behavior. Never point a test fixture at a database that contains
user data.

## Release checklist

For a version change, update `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, and
installation examples together. Run Ruff, the full and offline suites, build
the wheel and source archive, then test a clean installation before publishing.
