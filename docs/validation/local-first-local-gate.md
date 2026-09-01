# Local release validation

Date: 2026-09-01

Branch: `main`

## Result

WikiBricks records, searches, reads, writes, curates, backs up, and serves MCP
tools through local SQLite without Databricks credentials. The Omnigent bridge
also passed its focused cross-harness, pre-turn recall, post-commit capture,
native transcript, and tool relay tests.

## Environment

| Component | Version |
|---|---|
| Python | 3.14.3 |
| SQLite | 3.51.2 |
| PostgreSQL compatibility test service | 17.11 |
| WikiBricks | 0.9.0 |
| overnight-dev plugin | 1.29.0 |

## Current gates

| Command or check | Result |
|---|---|
| `uv run ruff check src tests` | passed |
| `uv run pytest -q` | 97 passed |
| `UV_OFFLINE=1 uv run pytest -q` | 97 passed |
| `uv build --offline` | wheel and source archive built |
| Installed-wheel MCP smoke | five tools passed write, search, and read |
| Omnigent focused memory suite | 6 passed |
| Omnigent native transcript boundary | 2 passed |

The installed-wheel smoke used a new Python 3.14 virtual environment. The wheel
was installed there while its already-resolved MCP dependencies came from the
development environment because registry access was unavailable during this
run.

## Artifacts

| Artifact | Size | SHA-256 |
|---|---:|---|
| `dist/wikibricks-0.9.0-py3-none-any.whl` | 98,883 bytes | `ff7a28bb799e720bbff59d514847c4301c1036996478fe3250ec7688f46b73b6` |
| `dist/wikibricks-0.9.0.tar.gz` | 195,731 bytes | `63d0c5c21d56a4ba0e70a27b3a8cce5fe69d0859b190b6d461770f345557190f` |

The wheel contains 62 files, including SQLite migrations, YAML defaults, agent
instructions, MCP schemas, curation contracts, the optional PostgreSQL
compatibility backend, and the remote job package.

The base dependencies are `mcp>=1.0,<2` and `PyYAML>=6.0`. PostgreSQL migration
and Lakebase dependencies remain optional extras.

## Behavioral coverage

The suite verifies immutable SQLite page and session versions, atomic outbox
writes, long event reconstruction, bounded FTS5 search, deterministic local
maintenance, and archive-gated retention. It also covers the five tools through
a stdio MCP session with outbound networking blocked.

The Omnigent tests verify one offline memory flow from Codex to Claude Code to
Kimi, idempotent background capture, source runner metadata, a 250 ms fail-open
recall boundary, hidden-context removal, and native tool relay.

Raw sessions remain evidence. Agents maintain the smaller linked wiki. Daily
local maintenance keeps indexes and retention state consistent between
optional weekly remote runs.
