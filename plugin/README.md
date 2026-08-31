# WikiBricks Claude Code adapter

This optional plugin records Claude Code sessions in local PostgreSQL and
registers the harness-neutral WikiBricks MCP server. Databricks is not needed.

## Prerequisites

- PostgreSQL 16 or 17 is running.
- `uv` is on `PATH`.
- The local database is initialized.

```bash
export WIKIBRICKS_DATABASE_URL=postgresql:///wikibricks
wikibricks init
```

## What the plugin runs

Five hooks call `wikibricks-recorder-hook`:

| Event | Timeout | Stored data |
|---|---:|---|
| `SessionStart` | 60s | Session time, workspace, and model |
| `UserPromptSubmit` | 5s | User prompt |
| `PostToolUse` | 5s | Tool call and tool result |
| `Stop` | 30s | Normalized session flush |
| `SessionEnd` | 30s | Idempotent final flush |

The plugin also starts `wikibricks-mcp`, which exposes `wiki_search`,
`wiki_read_full`, `wiki_index`, `wiki_write_page`, and
`wiki_promote_answer`.

## Install after the 0.8.0 tag is published

In Claude Code:

```text
/plugin marketplace add https://github.com/philtief/wikibricks.git
/plugin install wikibricks-recorder@wikibricks
```

The launcher installs the tagged Python package into the plugin data
directory on first use. The current release-candidate branch keeps the
launcher on the last published tag. Do not change its default to `v0.8.0`
until that tag exists.

## Test this local release candidate

Install the worktree and use the manual hook example:

```bash
cd /absolute/path/to/wikibricks
uv sync --extra dev
cp examples/claude-settings.json /tmp/wikibricks-claude-settings.json
```

Merge the `hooks` object from that file into `~/.claude/settings.json` and
replace `/PATH/TO/wikibricks` with the absolute checkout path. Register the
same checkout's MCP server:

```bash
claude mcp add --scope user \
  -e WIKIBRICKS_DATABASE_URL=postgresql:///wikibricks \
  wikibricks -- /absolute/path/to/wikibricks/.venv/bin/wikibricks-mcp
```

Set `WIKIBRICKS_USER_ID` to override the recorded user. Without it, the
adapter uses `git config user.email`, then the operating-system user.

## Local files

Hook events are buffered under `~/.wikibricks_recorder/` by default. Set
`WIKIBRICKS_RECORDER_DIR` to change this path. PostgreSQL stores the durable
session after `Stop` or `SessionEnd`.

Temporary directories and system-prompt-only utility sessions are skipped.
A repeated flush is idempotent.

## License

Apache 2.0. See [`LICENSE`](../LICENSE).
