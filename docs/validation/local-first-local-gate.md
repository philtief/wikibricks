# Local release validation

Date: 2026-08-31

Branch: `feat/local-first-core`

## Result

The local release gate passes. WikiBricks records, searches, reads, writes,
curates, backs up, and serves MCP tools through PostgreSQL without Databricks
credentials or an SDK import. The installed wheel also completes its MCP smoke
test with no `databricks` package present.

Remote state is unchanged. This work did not deploy to FEVM, connect to
Lakebase, change the public mirror, push a branch, or publish a package.

## Environment

| Component | Version |
|---|---|
| macOS Python | 3.14.3 |
| uv | 0.10.9 |
| PostgreSQL | 16.13 and 17.11 |
| WikiBricks | 0.8.0 |
| MCP in the clean wheel environment | 1.29.0 |
| overnight-dev plugin | 1.29.0 |

The repository pre-commit hook is active. Every implementation commit in this
cleanup ran Ruff and the full pytest suite.

## Release gates

| Command or check | Result |
|---|---|
| `PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" uv run ruff check src tests` | passed |
| `PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" uv run pytest -q` | 83 passed in 14.59 seconds |
| `PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" UV_OFFLINE=1 uv run pytest -q` | 83 passed in 14.40 seconds |
| PostgreSQL 17 database-heavy selection | 43 passed in 12.69 seconds |
| `uv build` | wheel and source archive built |
| Clean Python 3.14 wheel install | 33 dependencies installed; Databricks SDK absent |
| Installed `wikibricks init` | fresh PostgreSQL 16 schema created |
| Installed `wikibricks-mcp` | five tools listed; write, search, and read passed |
| Backup, Omnigent/Codex, and archive retry selection | 3 passed in 3.41 seconds |

The PostgreSQL 17 selection covers storage, ingestion, maintenance, curation,
archive sync, and MCP. Disposable database clusters were used for every test.

An additional offline clean-environment installation was attempted before the
specified wheel gate. It stopped during dependency resolution because PyYAML
was not present in uv's local cache. No WikiBricks code ran in that attempt.
The offline runtime suite passed after dependencies were installed, which is
the supported offline contract.

## Built wheel

| Artifact | Size | SHA-256 |
|---|---:|---|
| `dist/wikibricks-0.8.0-py3-none-any.whl` | 72,327 bytes | `ffc37f7873dfabdb24969d61c43a56b70a3778c769fa6911f6c21cff9aa9ee0b` |

The wheel contains 49 files. It includes:

- `wikibricks/config/defaults.yml`
- `wikibricks/resources/agent-instructions.md`
- `wikibricks/resources/mcp-tools.json`
- both JSON interchange schemas
- all three PostgreSQL migrations
- focused `storage`, `curation`, and `remote` modules

The wheel does not contain the old recorder or Databricks packages, Apps,
notebooks, jobs, or scripts. Its base dependencies are:

```text
mcp<2,>=1.0
psycopg[binary]>=3.2
pyyaml>=6.0
```

`databricks-sdk>=0.44.0` is qualified by the `lakebase` extra. Importing the
base package and `wikibricks.remote.lakebase` succeeds while Databricks imports
are blocked. The adapter imports the SDK only when requesting a Lakebase
credential.

## Behavioral evidence

The 83-test suite verifies these contracts:

- Local page writes, immutable history, search chunks, operation records, and
  outbox entries commit in one PostgreSQL transaction.
- Session events have stable identities and immutable versions. A 25 MB tool
  result reconstructs exactly and remains searchable.
- Search uses 64 KiB UTF-8 chunks, GIN full-text indexes, and `pg_trgm` path and
  title indexes.
- Claude Code, Omnigent, and JSONL normalize to one session model. Omnigent is
  read-only and resumable, and it preserves Codex agent metadata.
- The stdio MCP server initializes with outbound sockets blocked and exposes
  exactly five harness-neutral tools.
- Backup and restore preserve database fingerprints.
- Local curation repairs search chunks, materializes `_meta/index`, reports
  duplicates and orphans, and prunes only fully archived old sessions.
- Archive batches are deterministic. A lost connection after remote commit
  leaves local rows pending; retry does not duplicate remote events.
- Curation manifests are immutable. Local apply checks the base version ID and
  content hash, stores conflicts, preserves history, and applies cleanup groups
  atomically.

This keeps the Karpathy LLM Wiki split intact: raw sessions are evidence, while
the active agent maintains linked wiki pages. Deterministic local maintenance
keeps indexes and retention state clean between monthly remote runs.

## Deferred remote work

The following work still requires separate approval and validation:

- Lakebase Change Data Feed into Delta history
- monthly remote semantic curation
- non-destructive migration of the current FEVM wiki
- public mirror update, release tag, and plugin publication

Electric replication is not part of this release. Archive sync uses explicit,
bounded application batches and immutable IDs; curation returns through guarded
manifests rather than database-level bidirectional replication.

## Implementation commits

- `a77df19` removed the legacy Databricks runtime and its tests
- `11271d1` added validated YAML configuration
- `847abf9` moved guidance and contracts into packaged resources
- `eaff2c2` split PostgreSQL storage into focused repositories
- `712ad45` split curation protocol, persistence, planning, and application
- `3271af9` moved optional Lakebase sync under `wikibricks.remote`
- `2851f25` rewrote local adoption and contributor documentation

The protocol runbook is in `docs/curation-sync.md`. `project.yaml` remains
untracked and unchanged.
