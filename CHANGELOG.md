# Changelog

## 0.11.0 - 2026-09-01

- Replaced the Omnigent companion agent with one shared memory skill and native
  MCP configuration for Codex, Claude Code, and Kimi.
- Added an idempotent `wikibricks install omnigent` command that preserves
  unrelated user settings and removes the recognized legacy agent profile.
- Kept the Omnigent importer as an optional recovery command. Normal work does
  not poll Omnigent's conversation database.

## 0.9.0 - 2026-09-01

- Added background automation for daily local maintenance and optional weekly
  Lakebase push, pull, and safe apply.
- Local edits now win background curation conflicts automatically.
- Made SQLite with WAL the default local store and added PostgreSQL-to-SQLite
  migration.
- Made MCP instructions responsible for context retrieval at the start of an
  agent task.
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
