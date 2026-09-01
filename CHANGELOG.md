# Changelog

## Unreleased

## 0.9.0 - 2026-09-01

- Added MCP-owned background automation for live Omnigent capture, daily local
  maintenance, and optional Lakebase push, pull, and safe apply.
- Local edits now win background curation conflicts automatically.
- Added automatic local database creation and live SQLite WAL support.
- Made MCP instructions responsible for context retrieval at the start of an
  agent task.
- Renamed the Claude Code plugin and hook to `wikibricks` and
  `wikibricks-hook`.
- Made the weekly remote schedule opt-in for every bundle target.
- Replaced historical implementation material with installation and
  contributor guidance for the current architecture.

## 0.8.0

- PostgreSQL is the authoritative local store for pages, sessions, history,
  search, and maintenance.
- The stdio MCP server supports Codex, Claude Code, Omnigent, and other MCP
  clients with five harness-neutral tools.
- Session import supports Omnigent and the versioned JSONL contract.
- Deterministic local curation repairs indexes and gates retention on archive
  acknowledgements.
- Explicit, idempotent Lakebase sync archives immutable versions.
- The optional weekly Lakeflow Job publishes guarded curation manifests for
  local review and conflict-aware application.
