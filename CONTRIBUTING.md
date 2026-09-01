# Contributing to WikiBricks

WikiBricks is local PostgreSQL memory for AI agents. Read
[`AGENTS.md`](AGENTS.md) before changing code.

## Set up

PostgreSQL 16 or 17 must be running. Tests create disposable databases, so
never point them at user data.

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
uv sync --extra dev
uv run pytest
```

## Development loop

This repository uses overnight-dev hooks and test-driven development.

1. Write one failing behavior test.
2. Confirm that it fails for the intended reason.
3. Implement the smallest change.
4. Run the focused test, Ruff, and the full suite.

```bash
uv run ruff check src tests
uv run pytest
UV_OFFLINE=1 uv run pytest
uv build
```

Do not bypass the pre-commit hook or amend a checked commit.

## Boundaries

- Local APIs and MCP must work without a network or Databricks dependency.
- The connected agent, not the library, makes semantic curation decisions.
- Preserve immutable versions, transactionally coupled outbox writes, and the
  five MCP tool names.
- `pg_trgm` is the only required PostgreSQL extension.
- Keep Lakebase access behind explicit configuration or the diagnostic sync
  command.
- Use the Databricks SDK for control-plane work and SQL for data operations.
- Do not hardcode credentials, workspace IDs, or user paths.

## Pull requests

Target `main` and keep each pull request focused. Add an entry under
`Unreleased` in `CHANGELOG.md`. If remote resources changed, also run:

```bash
databricks bundle validate --strict -t staging --profile PROFILE
```

Update README and agent guidance when a public command, configuration surface,
or architectural boundary changes.

## Releases

WikiBricks follows Semantic Versioning. Update the package version, lockfile,
plugin manifest, changelog, and installation examples together. Publish only
after the clean-install and local release gates pass.

## Code of conduct

Be kind. Disagree on substance, not on people.
