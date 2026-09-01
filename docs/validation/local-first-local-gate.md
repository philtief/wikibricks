# Local release validation

Date: 2026-09-01

Branch: `main`

## Result

WikiBricks records, searches, reads, writes, curates, backs up, and serves MCP
tools through local SQLite without Databricks credentials. Stock Omnigent
0.11.0 discovered the installed WikiBricks skill for its native Codex, Claude
Code, and Kimi harnesses without a separate agent profile.

## Environment

| Component | Version |
|---|---|
| Python | 3.14.3 |
| SQLite | 3.51.2 |
| WikiBricks | 0.11.0 |
| Omnigent | 0.11.0 |
| overnight-dev plugin | 1.29.0 |

## Current gates

| Command or check | Result |
|---|---|
| `uv run --no-sync ruff check src tests` | passed |
| `uv run --no-sync pytest -q` | 102 passed |
| `UV_OFFLINE=1 uv run --no-sync pytest -q` | 102 passed |
| `uv build --no-build-isolation` | wheel and source archive built |
| Installed-wheel MCP smoke | five tools passed write, search, and read |
| Omnigent skill discovery | Codex, Claude Code, and Kimi passed |
| Native MCP registration | Codex enabled; Claude Code connected |
| Kimi user configuration | prepared; Kimi binary is not installed locally |
| Legacy upgrade | agent profile removed; remote server setting preserved |

The installed-wheel smoke used a new Python 3.14 virtual environment. Registry
access was unavailable, so the wheel used the dependency set from the
development environment. The build backend came from its upstream GitHub
sources because Hatchling was not present in the local package cache.

## Artifacts

| Artifact | Size | SHA-256 |
|---|---:|---|
| `dist/wikibricks-0.11.0-py3-none-any.whl` | 102,488 bytes | `089e0bdcba71b9cc383aeb371defc0e29e84ca1f12d3edd001a391604379a792` |
| `dist/wikibricks-0.11.0.tar.gz` | 200,371 bytes | `41fcaa4377fd3c562d293be8bea23f6a5a23a6a8e11183ce6752883f01466fbb` |

The wheel contains 64 files, including SQLite migrations, YAML defaults, the
shared memory skill, MCP schemas, curation contracts, the optional PostgreSQL
compatibility backend, and the remote job package. It contains no Omnigent
agent profile.

The base dependencies are `mcp>=1.0,<2` and `PyYAML>=6.0`. PostgreSQL migration
and Lakebase dependencies remain optional extras.

## Behavioral coverage

The suite verifies immutable SQLite page and session versions, atomic outbox
writes, long event reconstruction, bounded FTS5 search, deterministic local
maintenance, and archive-gated retention. It also covers the five tools through
a stdio MCP session with outbound networking blocked.

The Omnigent installer tests cover native MCP registration, Kimi JSON merging,
legacy profile removal, unrelated setting preservation, and missing binaries.
The live check used the unmodified Omnigent 0.11.0 skill resolver. A Kimi
runtime test remains pending because Kimi is not installed on this machine.

Raw sessions remain evidence. Agents maintain the smaller linked wiki. Daily
local maintenance keeps indexes and retention state consistent between
optional weekly remote runs.
