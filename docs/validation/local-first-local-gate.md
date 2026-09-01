# Local release validation

Date: 2026-09-01

Branch: `feat/lakebase-remote-maintenance`

## Result

WikiBricks records, searches, reads, writes, curates, backs up, and serves MCP
tools through PostgreSQL without Databricks credentials. The installed wheel
also completes its MCP smoke test without the Databricks SDK.

## Environment

| Component | Version |
|---|---|
| Python | 3.14.3 |
| PostgreSQL | 17.11 |
| WikiBricks | 0.8.0 |
| overnight-dev plugin | 1.29.0 |

PostgreSQL 16 and 17 integration coverage was completed during the local-first
release gate on 2026-08-31. This cleanup reran the full suite against PostgreSQL
17.

## Current gates

| Command or check | Result |
|---|---|
| `uv run ruff check src tests` | passed |
| `uv run pytest -q` | 89 passed |
| `UV_OFFLINE=1 uv run pytest -q` | 89 passed |
| `uv build` | wheel and source archive built |
| Clean Python 3.14 wheel install | 33 dependencies installed |
| Installed `wikibricks init` | disposable PostgreSQL schema created |
| Installed `wikibricks-mcp` | five-tool write, search, and read smoke passed |
| Installed `wikibricks-hook` | executable present |
| Strict plugin and marketplace validation | passed |
| Databricks SDK in clean base install | absent |

The clean-install database was dropped after the smoke test.

## Wheel

| Artifact | Size | SHA-256 |
|---|---:|---|
| `dist/wikibricks-0.8.0-py3-none-any.whl` | 82,641 bytes | `f194b0b2bc6a6543f5d99ee79e5ec030b2025979254ab483b640944cbc5c9625` |

The wheel contains 58 files, including YAML defaults, agent instructions, MCP
schemas, curation schemas, PostgreSQL migrations, local storage modules, and
the optional remote job package.

Base dependencies are `mcp<2,>=1.0`, `psycopg[binary]==3.2.13`, and
`pyyaml>=6.0`. The Databricks SDK is limited to the `lakebase` extra.

## Behavioral coverage

The suite verifies:

- immutable PostgreSQL page and session versions;
- atomic version and outbox writes;
- exact reconstruction of a 25 MB event;
- bounded full-text search chunks and trigram path search;
- Claude Code, Omnigent, Codex metadata, and JSONL session import;
- all five tools through a real stdio MCP session without outbound access;
- deterministic local curation and archive-gated retention;
- idempotent Lakebase archive retry;
- guarded curation manifests, atomic cleanup groups, and conflict history.

Raw sessions remain evidence. Agents maintain the smaller linked wiki, and
local deterministic maintenance keeps indexes and retention state clean
between optional remote runs.
