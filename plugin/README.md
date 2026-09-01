# WikiBricks for Claude Code

This optional plugin records Claude Code sessions in local PostgreSQL and
registers the same stdio MCP server used by other agent harnesses. It does not
require Databricks.

## Prerequisites

PostgreSQL 16 or 17 must be running, and `uv` must be on `PATH`.

## Install

Clone the public repository:

```bash
git clone https://github.com/philtief/wikibricks.git
```

Then run these commands inside Claude Code, replacing the path with the clone's
absolute path:

```text
/plugin marketplace add /absolute/path/to/wikibricks
/plugin install wikibricks@wikibricks
```

Restart Claude Code after installation. The launcher installs WikiBricks from
that checkout into the plugin data directory on first use.

## What it runs

Five hooks call `wikibricks-hook`:

| Event | Timeout | Stored data |
|---|---:|---|
| `SessionStart` | 60s | Session time, workspace, and model |
| `UserPromptSubmit` | 5s | User prompt |
| `PostToolUse` | 5s | Tool call and result |
| `Stop` | 30s | Normalized session flush |
| `SessionEnd` | 30s | Idempotent final flush |

The plugin also starts `wikibricks-mcp`, which exposes `wiki_search`,
`wiki_read_full`, `wiki_index`, `wiki_write_page`, and
`wiki_promote_answer`. That process initializes local PostgreSQL, imports live
Omnigent sessions, runs local maintenance, and performs configured Lakebase
sync in the background. It also instructs the agent to retrieve relevant
context before answering. Normal sessions require no WikiBricks commands.

Set `WIKIBRICKS_USER_ID` to override the recorded user. Without it, the
adapter uses `git config user.email`, then the operating-system user.

Temporary directories and system-prompt-only utility sessions are skipped.
Repeated flushes do not duplicate events.

## Use MCP without session capture

The plugin is optional. Claude Code can connect to WikiBricks directly:

```bash
claude mcp add --scope user \
  -e WIKIBRICKS_DATABASE_URL=postgresql:///wikibricks \
  wikibricks -- wikibricks-mcp
```

## License

Apache 2.0. See [`LICENSE`](../LICENSE).
