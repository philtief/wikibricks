# Contributing to WikiBricks

WikiBricks is local PostgreSQL memory for AI agents. Read [`AGENTS.md`](AGENTS.md)
before changing code; it defines the storage, MCP, sync, and release contracts.

## Set up the repository

PostgreSQL 16 or 17 must be running. Integration tests create disposable test
databases, so never point them at a database that contains user data.

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
uv sync --extra dev
uv run pytest
```

## Development loop

The repository uses the overnight-dev hook and test-driven development. Add a
failing behavior test, confirm the expected failure, implement the smallest
change, then run the focused and full gates.

```bash
uv run pytest tests/test_postgres_store.py -q
uv run ruff check src tests
uv run pytest
UV_OFFLINE=1 uv run pytest
uv build
```

The pre-commit hook runs Ruff and all tests. Fix a blocked commit and create a
new commit. Do not use `--no-verify` or amend a checked commit.

## Code boundaries

- `WikiClient()` and `wikibricks-mcp` must work without network access,
  Databricks credentials, or the Databricks SDK.
- Keep model calls out of `src/wikibricks/`. The connected agent owns semantic
  decisions.
- Preserve immutable page and event versions, write/outbox atomicity, 64 KiB
  search chunks, and the five MCP tool names.
- `pg_trgm` is the only required PostgreSQL extension. Do not make embeddings
  part of the base runtime.
- Lakebase access belongs only in `wikibricks.remote.lakebase` and the explicit
  `wikibricks sync lakebase` command. Import the Databricks SDK lazily.
- Use the Databricks SDK for control-plane work and SQL for data operations.
  Do not add raw REST calls.
- Do not hardcode workspace IDs, user paths, credentials, or tokens.

## Pull requests

Target `main` and keep each pull request focused on one behavior or refactor.
Every changed line should trace to the request. Update tests for behavior
changes and add an entry under `[Unreleased]` in `CHANGELOG.md`.

Before opening the pull request, run Ruff, the full suite, the offline suite,
and `uv build`. Changes to MCP packaging or storage also require an installed
wheel smoke test.

## Bugs and feature requests

A bug report should include expected and observed behavior, a minimal
reproduction, Python and PostgreSQL versions, and whether the failure occurs
offline. A failing test is preferred.

Feature requests should lead with the use case and the proposed public API.
Changes to `WikiClient`, the session JSON schema, curation manifests, or MCP
tool names are compatibility changes and need explicit review.

## Releases

WikiBricks follows Semantic Versioning. A version change updates
`pyproject.toml`, `uv.lock`, `plugin/.claude-plugin/plugin.json`,
`CHANGELOG.md`, and the README wheel/test references together. Build the wheel,
run the local release gate, and publish only after the release candidate is
approved.

## Code of conduct

Be kind. Assume good intent. Disagree on substance, not on people.
