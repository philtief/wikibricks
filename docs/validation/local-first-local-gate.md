# WikiBricks 0.8.0 local validation

Date: 2026-08-31

Branch: `feat/local-first-core`

Remote state: unchanged. This validation did not deploy to FEVM, connect to
Lakebase, modify the public mirror, or push a Git branch.

## Result

The local release gate passes. WikiBricks records, searches, reads, writes,
curates, backs up, and serves MCP tools through PostgreSQL without a
Databricks dependency or outbound network connection. It also receives,
plans, applies, and resolves immutable remote curation patches through local
PostgreSQL.

The local product follows the Karpathy LLM Wiki contract. Recorded sessions
and raw sources are evidence. The agent maintains linked topic, entity,
comparison, and synthesis pages. The MCP initialization sends this workflow
to every harness.

## Environment

| Component | Version |
|---|---|
| macOS Python | 3.14.3 |
| uv | 0.10.9 |
| PostgreSQL | 16.13 and 17.11 |
| Package | 0.8.0 |
| MCP in the clean base install | 1.29.0 |
| overnight-dev plugin | 1.29.0 |

The Git pre-commit hook is executable in the shared repository hook
directory. A repository-local `core.hooksPath` overrides the global
`/dev/null` setting for this repository. The overnight-dev commit gate
completed Ruff and the full test suite.

## Release gates

| Command or check | Result |
|---|---|
| `uv run pytest` | 997 passed |
| `uv run ruff check src tests scripts` | passed |
| `uv build` | wheel and source archive built |
| `UV_OFFLINE=1 uv run pytest` | 997 passed |
| PostgreSQL 17 local contract selection | 54 passed |
| Clean base-wheel install | passed |
| Real stdio MCP from clean wheel | five tools, write, and search passed |
| Databricks module in clean environment | absent |
| Repository pre-commit hook | Ruff and full test suite passed |

The PostgreSQL 17 selection includes storage, local client, ingestion,
maintenance, Lakebase archive and curation protocols, MCP, session contract,
and package-boundary tests. The main suite runs against PostgreSQL 16.13.

## Built artifacts

| Artifact | SHA-256 |
|---|---|
| `dist/wikibricks-0.8.0-py3-none-any.whl` | `3b8105b4f267c9dc87086fc14e84d48928b2bcee9c1e38b62db8c285939e854f` |

The source archive contains this report, so its final hash belongs in the
release record after the report is complete.

The wheel contains:

- `wikibricks/sql/migrations/0001_local_postgres.sql`
- `wikibricks/sql/migrations/0002_archive_sync.sql`
- `wikibricks/sql/migrations/0003_curation_patches.sql`
- `wikibricks/curation_sync.py`
- `wikibricks/WIKIBRICKS.MD`

Its required dependencies are `mcp>=1.0,<2` and
`psycopg[binary]>=3.2`. `databricks-sdk` appears only under the `databricks`
and `dev` extras. A clean install initially exposed an incompatible MCP 2.0
API. The upper bound and regression test now prevent that failure.

## Behavioral evidence

The suite verifies these local properties:

- Page writes, immutable history, search chunks, operations, and outbox rows
  commit in one transaction.
- Concurrent migrations and session writers complete without lost events.
- A 25 MB tool result reconstructs byte-for-byte and remains searchable
  without a `tsvector` size failure.
- Search uses 64 KiB UTF-8 chunks, GIN full-text indexes, and `pg_trgm` path
  and title indexes.
- Claude Code, Omnigent, and JSONL normalize to one session contract.
- Omnigent import is read-only and resumable. Codex metadata survives as
  `agent:codex-native-ui`.
- A stdio client initializes the server, receives the compounding-wiki
  schema, and calls all five tools with outbound sockets blocked.
- Backup and restore preserve database fingerprints.
- `wikibricks curate` repairs missing search documents, materializes
  `_meta/index`, and reports exact duplicates and orphan pages.
- The dynamic MCP index and `_meta/index` contain curated wiki pages, not raw
  sessions or archive pages. Search can still return raw session evidence.
- Local retention removes only old sessions whose event versions all have a
  committed archive acknowledgement.
- Archive batches are deterministic and idempotent. A retry after remote
  commit does not duplicate events.
- A pulled curated page cannot overwrite a local page with the same path.
- Curation manifests are immutable and targeted to a stable local replica.
  Pull stores them without changing active pages.
- An update applies only when its base version ID and content hash still
  match. A divergent local edit stores the base, local, and remote values.
- Keep-local, accept-remote, defer, and manual-merge conflict resolutions
  preserve immutable page history.
- High-risk cleanup groups cannot pass the safe policy. An approved duplicate
  merge updates the canonical page, retargets links, creates an alias, and
  supersedes the duplicate in one transaction.
- Concurrent patch application creates one page version and one receipt. A
  simulated crash before commit leaves no partial page, alias, link, receipt,
  or outbox state.
- Applied page hashes equal the remote proposal hash. Patch receipts return
  through the ordinary archive outbox and survive backup and restore.

## Local curation loop

Two local mechanisms keep PostgreSQL useful between remote runs:

1. The active agent receives the wiki schema over MCP. It searches before it
   writes, updates existing knowledge, maintains typed links, and files useful
   answers as synthesis pages.
2. `wikibricks curate` runs deterministic maintenance without a model or
   network connection. It repairs search metadata, rebuilds the index, reports
   hygiene candidates, and applies an explicit archive-gated retention policy.

A monthly remote run can perform more expensive deduplication and contradiction
analysis. It returns versioned patches with a base version ID and content hash.
Local apply accepts unchanged bases and queues local edit conflicts.

## Known limits and remote checkpoint

The local gate excludes:

- Lakebase Change Data Feed and Delta history
- Monthly remote curation jobs
- Migration of the existing FEVM wiki
- Public mirror update, release tag, or plugin publication
- Standalone Codex log parsing outside Omnigent or JSONL

The legacy `--pull-curated` option still imports a read-only archive cache.
`--pull-patches` downloads immutable manifests into the local inbox. Planning,
application, and conflict resolution then run without a remote connection.

The Claude plugin launcher remains pinned to the last published tag,
`v0.7.13`. Change it to `v0.8.0` only after that tag exists.

Electric remains deferred. Its current write path does not provide the
offline upstream-write and conflict contract required here.

Remote work requires explicit approval of the Databricks profile and target.
Migration must preserve the existing remote tables as rollback data.

## Implementation commits

- `9a8fffe` harness-neutral session contract
- `dfbac28` transactional PostgreSQL store
- `64e1ffc` local-default client and optional Databricks dependency
- `366539f` harness-neutral stdio MCP server
- `5f36460` Claude Code, Omnigent, and JSONL ingestion
- `f438d73` initialization, checks, backup, restore, and vacuum
- `5c994ea` optional resumable Lakebase archive sync
- `bc9e2d2` guarded curation patches, conflicts, receipts, aliases, and cleanup
- `d44f9f7` exact proposal hashes and complete backup fingerprints

The protocol runbook is in `docs/curation-sync.md`. Remote state remains
unchanged until the Lakebase target and staging plan receive explicit approval.
