# Local release validation

Date: 2026-08-31

Branch: `feat/local-first-core`

Status: pending the final release gate after the local-only code and
documentation cleanup.

Remote state is unchanged. This work has not deployed to FEVM, connected to
Lakebase, changed the public mirror, pushed a branch, or published a package.

## Scope

The gate will verify these contracts:

- PostgreSQL 16 and 17 support
- local recording, search, reads, writes, history, and deterministic curation
- operation without network access, Databricks credentials, or the Databricks
  SDK
- Codex metadata through Omnigent import, Claude Code hooks, and versioned JSONL
- exact five-tool MCP behavior through an installed wheel
- immutable page and event versions, bounded search chunks, and atomic outbox
  writes
- backup and restore fingerprints
- optional archive failure, retry, and duplicate prevention against a second
  local PostgreSQL database
- guarded curation pull, planning, application, and conflict resolution

## Evidence to record

The final pass will record exact commands, versions, test counts, elapsed time,
wheel contents, dependencies, and SHA-256. It will also identify the retained
limitations: Lakebase Change Data Feed, Delta maintenance, current remote wiki
migration, public release, and plugin publication remain outside this local
gate.
