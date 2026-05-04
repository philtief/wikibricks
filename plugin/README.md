# wikibricks-recorder (Claude Code plugin)

Records every Claude Code session as one WikiBricks page in a Unity Catalog
schema you control, and exposes the wiki to the agent via 5 stdio MCP tools
so it can search and read prior sessions.

This directory **is** the plugin — `.claude-plugin/plugin.json` is the
manifest, `hooks/hooks.json` wires the 5 hook events, `.mcp.json`
declares the stdio MCP server, and `bin/launch.sh` is an idempotent
installer that fetches `wikibricks[recorder]` from a Git URL on first
use and caches the binaries in `${CLAUDE_PLUGIN_DATA}`.

## Install

From the marketplace at the [wikibricks-dev](https://github.com/philtief/wikibricks-dev) repo:

```bash
/plugin marketplace add https://github.com/philtief/wikibricks-dev.git
/plugin install wikibricks-recorder@wikibricks
```

> The marketplace will move to the public mirror at `philtief/wikibricks`
> after stabilization. Until then, install from `wikibricks-dev` directly.

For local development against this repo:

```bash
claude --plugin-dir /path/to/wikibricks-dev/plugin -p "say hi"
```

## Prerequisites

- **`uv`** must be on `$PATH`. The launcher uses `uv tool install` to
  fetch the recorder from a Git URL on first session. Install:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Databricks workspace** with a `wikibricks` schema deployed (run
  `databricks bundle deploy` from the wikibricks repo if you don't have
  one yet). Configure the recorder once with:
  ```bash
  uvx --from "git+https://github.com/philtief/wikibricks-dev.git@main" wiki-init personal
  ```

## What the plugin does

**Hooks** (5 events, each routed through `bin/launch.sh
wikibricks-recorder-hook`):

| Event             | Timeout | Purpose                                                 |
| :---------------- | :-----: | :------------------------------------------------------ |
| SessionStart      |   60s   | Stamps `started_at` + `cwd`. Cold-install on first use. |
| UserPromptSubmit  |    5s   | Appends the prompt to the session event log.           |
| PostToolUse       |    5s   | Appends the tool call to the session event log.        |
| Stop              |   30s   | Flushes the session as one wiki page.                  |
| SessionEnd        |   30s   | Same flush as Stop (defensive duplicate).              |

**MCP server** `wiki` exposes 5 tools (prefixed
`mcp__plugin_wikibricks-recorder_wiki__*` when invoked from a session):

- `wiki_search` — HYBRID Vector Search over your prior sessions.
- `wiki_read_full` — fetch full page content, including parent + chunks.
- `wiki_index` — list pages, optionally filtered by path prefix.
- `wiki_write_page` — create/update a wiki page (DML; agent-driven).
- `wiki_promote_answer` — promote a Q&A pair to a synthesis page.

## Environment overrides

| Variable                  | Default                                            | Purpose                                            |
| :------------------------ | :------------------------------------------------- | :------------------------------------------------- |
| `WIKIBRICKS_PLUGIN_REF`   | `main`                                             | Git ref (tag/branch/sha) the launcher installs. Pin to a tag (e.g. `v0.3.0`) once stable. |
| `WIKIBRICKS_PLUGIN_GIT`   | `https://github.com/philtief/wikibricks-dev.git`   | Git URL the launcher installs from. Will move to the public mirror later. |
| `WIKIBRICKS_RECORDER_DIR` | `~/.wikibricks/sessions/`                          | Where session event logs are buffered before flush. |
| `WIKIBRICKS_CATALOG`      | (resolved via `~/.wikibricks-recorder.toml`)       | UC catalog the recorder writes to.                 |
| `WIKIBRICKS_SCHEMA`       | (resolved via `~/.wikibricks-recorder.toml`)       | UC schema the recorder writes to.                  |

## Uninstall

```bash
/plugin uninstall wikibricks-recorder
rm -rf ~/.claude/plugins/data/wikibricks-recorder    # cached uv install
```

To switch the installed Git ref without reinstalling the plugin, set
`WIKIBRICKS_PLUGIN_REF` and remove the marker file:

```bash
rm ~/.claude/plugins/data/wikibricks-recorder/installed-*
```

The launcher will re-install on the next session.

## License

Apache-2.0. See [LICENSE](../LICENSE) at the repo root.
