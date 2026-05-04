# Contributing to WikiBricks

Thanks for your interest. WikiBricks is a Databricks Asset Bundle that deploys a
Delta + Vector Search wiki store and exposes it as native MCP tools. Before
opening a PR, please read this file and skim [`AGENTS.md`](AGENTS.md) for the
project's non-negotiables.

## Development setup

```bash
git clone https://github.com/<you>/wikibricks.git
cd wikibricks
uv sync                       # core library only
uv sync --extra recorder      # also include the optional recorder package
uv run pytest                 # 453 tests, no Databricks workspace required
```

A Databricks workspace is only needed for bundle deploy and end-to-end runs.
See `databricks.override.example.yml` and `README.md` → Quick start.

## Development loop

```bash
uv run pytest                          # fast unit + DAG tests
uv run pytest tests/test_client.py     # single file
uv run ruff check src tests scripts    # lint
uv run ruff format src tests scripts   # format
```

The pre-commit hook runs lint + tests. If it blocks your commit, fix the
problem and create a **new** commit — never `--amend` or `--no-verify`.

## Hard rules

1. **No LLM calls inside `src/wikibricks/`.** The library is a storage contract.
   All LLM work lives in `notebooks/promote_from_traces.py` or user code.
   *Scope:* this rule binds the library only; `src/wikibricks_recorder/`
   is consumer-side tooling and may interact with LLMs.
2. **No FastMCP or bespoke MCP server *for the library*.** UC functions are
   the library's MCP surface via Databricks managed MCP. The recorder
   package ships its own stdio MCP server (`wikibricks-mcp`) because UC
   functions cannot do DML — that is consumer-side and allowed.
3. **No raw REST API calls.** Use `databricks.sdk.WorkspaceClient` everywhere
   except the vendored [redacted benchmark] evaluator.
4. **No hardcoded workspace IDs.** `databricks.yml` uses generic defaults;
   workspace-specific values belong in `databricks.override.yml` (gitignored).
5. **No destructive git without explicit confirmation.** No `git push --force`,
   no `git reset --hard`, no branch deletion.

## Pull requests

- Target `main`.
- Keep PRs focused — one feature or fix per PR.
- Every changed line should trace to the PR description.
- Add or update tests for behaviour changes. TDD is encouraged.
- Update `CHANGELOG.md` under `[Unreleased]` using Keep-a-Changelog headings
  (Added / Changed / Fixed / Deprecated / Removed / Security).
- Lint + all tests must pass. CI blocks merging on red.

## Reporting bugs

Open an issue using the **Bug report** template. Include:

- Expected vs. actual behaviour
- Minimum repro (ideally a failing test)
- Environment: `python --version`, `uv run python -c "import wikibricks; print(wikibricks.__version__)"`, Databricks runtime

## Suggesting features

Open an issue using the **Feature request** template. Describe the use case
first, then a sketch of the API. Breaking changes to `WikiClient` need a minor
version bump and a CHANGELOG entry.

## Versioning + release

WikiBricks follows SemVer. When bumping the library version:

1. `pyproject.toml` — `version = "x.y.z"`
2. **Every notebook's `%pip install` line** — `grep -rn "wikibricks-.*\.whl" notebooks/`
3. `CHANGELOG.md` — new `## [x.y.z] - YYYY-MM-DD` section; update the footer compare links
4. `uv build` to produce the wheel
5. Tag `vx.y.z` — the release workflow builds + publishes

## Code of conduct

Be kind. Assume good intent. Disagree on substance, not on people.
