# wikibricks-recorder (Claude Code plugin)

Records every Claude Code session as one WikiBricks page in a Unity
Catalog schema you control, and exposes the wiki to the agent via 5
stdio MCP tools so it can search and read prior sessions.

## Install — two halves

The plugin is the client half; you also need a deployed wiki store.

### Half 1: deploy the wiki store (one-time, per `<catalog>.<schema>`)

From a clone of [wikibricks](https://github.com/philtief/wikibricks):

```bash
cp databricks.override.example.yml databricks.override.yml   # edit host/profile/catalog/schema/warehouse_id
databricks bundle deploy --target dev
databricks bundle run deploy_wiki_store --target dev
```

Idempotent. Creates schema, Delta tables, Vector Search index, 7 UC
functions, and the daily curate Lakeflow Job. Team owners run once and
`GRANT` to teammates — `wiki-init team-create` prints the SQL.

### Half 2: install the plugin (every machine, ~1 min)

```bash
/plugin marketplace add https://github.com/philtief/wikibricks.git
/plugin install wikibricks-recorder@wikibricks
uvx --from "git+https://github.com/philtief/wikibricks.git@v0.7.1" \
    wiki-init personal             # | team-create | team-join
```

`wiki-init` writes `~/.wikibricks-recorder.toml`. The plugin's launcher
does `uv tool install` from the Git URL on first session (~30s cold)
and exec's the cached binary thereafter (~50ms warm).

Local-dev shortcut (no marketplace): `claude --plugin-dir <repo>/plugin`.

## Prerequisites

- **`uv`** on `$PATH` (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
- **Databricks workspace** with a deployed wiki store (Half 1).

## What the plugin does

5 hook events, each routed through `bin/launch.sh wikibricks-recorder-hook`:

| Event            | Timeout | Purpose                                                 |
| :--------------- | :-----: | :------------------------------------------------------ |
| SessionStart     |   60s   | Stamp `started_at` + `cwd`. First-call install budget.  |
| UserPromptSubmit |    5s   | Append the prompt to the session event log.            |
| PostToolUse      |    5s   | Append the tool call to the session event log.         |
| Stop             |   30s   | Flush the session as one wiki page.                     |
| SessionEnd       |   30s   | Defensive duplicate of Stop.                           |

MCP server `wiki` exposes 5 tools, surfaced as
`mcp__plugin_wikibricks-recorder_wiki__<name>`:

- `wiki_search` — HYBRID Vector Search over your prior sessions.
- `wiki_read_full` — page content with parent + chunks reassembled.
- `wiki_index` — list pages, optionally filtered by path prefix.
- `wiki_write_page` — create/update a page (DML, agent-driven).
- `wiki_promote_answer` — promote a Q&A pair to a synthesis page.

## Environment overrides

Env vars win over `~/.wikibricks-recorder.toml` when both are set.

| Variable                  | Default                                          | Purpose                                                          |
| :------------------------ | :----------------------------------------------- | :--------------------------------------------------------------- |
| `WIKIBRICKS_PLUGIN_REF`   | `v0.7.1`                                         | Git ref the launcher installs. Override for bleeding-edge.       |
| `WIKIBRICKS_PLUGIN_GIT`   | `https://github.com/philtief/wikibricks.git` | Git URL the launcher installs from.                              |
| `WIKIBRICKS_RECORDER_DIR` | `~/.wikibricks_recorder/`                        | Where session event logs are buffered before flush.              |
| `WIKIBRICKS_CATALOG`      | from TOML                                        | UC catalog the recorder writes to.                               |
| `WIKIBRICKS_SCHEMA`       | from TOML                                        | UC schema the recorder writes to.                                |
| `WIKIBRICKS_TARGET`       | `~/.wikibricks/active-target`, then sole wiki    | Which `[wikis.<name>]` section to use; per-session override of `wiki-target`. |

## Uninstall

```bash
/plugin uninstall wikibricks-recorder
rm -rf ~/.claude/plugins/data/wikibricks-recorder-*   # cached uv install
```

The cache dir is named `wikibricks-recorder-<marketplace>`; the glob
covers any marketplace name. To force a re-install at a different ref,
set `WIKIBRICKS_PLUGIN_REF` and `rm
~/.claude/plugins/data/wikibricks-recorder-*/installed-*` — the launcher
re-installs on the next session.

## License

Apache-2.0. See [LICENSE](../LICENSE).
