# WikiBricks

WikiBricks gives AI agents persistent local memory backed by PostgreSQL. It
connects through MCP and stores raw sessions plus maintained wiki pages. Codex,
Claude Code, Omnigent, and other MCP clients can share the same memory.

The complete read and write path works offline. Databricks and Lakebase are
optional and are used only for archive storage and weekly curation.

## How it works

```text
agent <--> wikibricks-mcp <--> local PostgreSQL
              |                       |
              |                       +-- daily local maintenance
              +-- imports Omnigent sessions every five minutes

optional remote maintenance:

local PostgreSQL <-- daily push/pull --> Lakebase <-- weekly --> Databricks job
```

Raw sessions remain immutable evidence. The agent maintains a smaller set of
topic, entity, comparison, and synthesis pages. This follows Andrej Karpathy's
[LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
search the existing wiki, integrate useful knowledge, and preserve links to
the source material.

The MCP process initializes PostgreSQL, imports live Omnigent conversations,
runs local maintenance, and performs optional Lakebase synchronization. Its
instructions tell the connected agent to search relevant pages before
answering and to save reusable knowledge.

## Install once

WikiBricks supports PostgreSQL 16 and 17. On macOS:

```bash
brew install uv postgresql@17
brew services start postgresql@17
uv tool install "wikibricks @ git+https://github.com/philtief/wikibricks.git"
```

The default database URL is `postgresql:///wikibricks`. Set another URL before
starting the agent if needed:

```bash
export WIKIBRICKS_DATABASE_URL=postgresql://user:password@localhost/wikibricks
```

The MCP server creates the database, enables `pg_trgm`, and applies migrations
when the agent starts. You do not need a configuration file for local use.

## Connect an agent once

`wikibricks-mcp` is a local stdio MCP server. It works with Codex, Claude Code,
Omnigent, and other MCP clients.

Register it in each harness that should share the memory.

Codex:

```bash
codex mcp add wikibricks \
  --env WIKIBRICKS_DATABASE_URL=postgresql:///wikibricks \
  -- wikibricks-mcp
```

Claude Code:

```bash
claude mcp add --scope user \
  -e WIKIBRICKS_DATABASE_URL=postgresql:///wikibricks \
  wikibricks -- wikibricks-mcp
```

Generic MCP configuration:

```json
{
  "mcpServers": {
    "wikibricks": {
      "command": "wikibricks-mcp",
      "env": {
        "WIKIBRICKS_DATABASE_URL": "postgresql:///wikibricks"
      }
    }
  }
}
```

The server exposes the same five tools to every harness:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

Their names, descriptions, and schemas do not depend on the harness.

## Daily use

Restart the agent after registering the MCP server, then work as usual. You do
not need to mention WikiBricks in prompts or run import, maintenance, or
synchronization commands. WikiBricks runs those tasks in the MCP background
process.

When prior work is relevant, the agent searches the wiki and reads the best
pages before answering. It updates existing pages or creates new ones when a
conversation produces knowledge that will be useful again. Raw session capture
does not create a curated page for every chat.

## Session capture and other imports

Omnigent stores conversations in `~/.omnigent/chat.db`. While an agent is
connected, WikiBricks reads new and changed conversations in the background.
The importer is read-only, supports a live SQLite WAL, and preserves agent
metadata, including Codex sessions.

The command below is only for recovery or a bulk import:

```bash
wikibricks import omnigent --user-id "$USER"
```

Use `--since-days` or `--limit` for a bounded first import. Re-importing an
unchanged event is a no-op. If a source event changes, WikiBricks writes a new
immutable version.

Any harness can export the versioned JSONL contract:

```bash
wikibricks import jsonl examples/session-v1.jsonl
```

The optional Claude Code plugin adds automatic session capture. See
[`plugin/README.md`](plugin/README.md).

## Storage and search

WikiBricks does not call a language model. The connected agent decides what to
search, write, link, merge, or promote.

PostgreSQL `text` and TOAST store long session events. Search uses built-in
`tsvector` values, GIN indexes, and bounded 64 KiB UTF-8 chunks. `pg_trgm`
indexes paths and titles. WikiBricks does not require embeddings or
`pgvector`.

## Keep local memory clean

The active agent handles semantic maintenance during normal work: search
before writing, update an existing page when possible, cite its evidence, and
record contradictions.

Local maintenance is deterministic and offline. The MCP process runs it once a
day. These commands remain available for diagnostics and recovery:

```bash
wikibricks curate
wikibricks check
wikibricks vacuum
```

`wikibricks curate` repairs search chunks, rebuilds `_meta/index`, and reports
duplicates and orphans. It does not call a model.

Back up before retention or a large curation apply:

```bash
wikibricks backup backups/wikibricks.dump
wikibricks --database-url postgresql:///wikibricks_restored \
  restore backups/wikibricks.dump
```

Retention is archive-gated:

```bash
wikibricks curate --prune-archived-sessions-after-days 90
```

A session is eligible only after every immutable event version has a committed
archive acknowledgement.

## Configuration

Create `~/.wikibricks/config.yml` to override the packaged defaults:

```yaml
version: 1
database:
  url: postgresql:///wikibricks
search:
  default_results: 5
  maximum_results: 20
maintenance:
  prune_archived_sessions_after_days: null
automation:
  enabled: true
  poll_seconds: 300
  local_maintenance_hours: 24
  omnigent:
    database: ~/.omnigent/chat.db
sync:
  batch_size: 1000
  apply_policy: safe
  interval_hours: 24
  profile: null
  project: null
  branch: production
  endpoint: primary
  database: wikibricks
```

Unknown keys and invalid values fail during startup. Explicit CLI or API
arguments take precedence over environment variables, the user file, and the
packaged defaults.

## Optional Lakebase archive

Install the Lakebase extra on the machine that runs the agent:

```bash
uv tool install --force \
  "wikibricks[lakebase] @ git+https://github.com/philtief/wikibricks.git"
```

Set the Lakebase target once in `~/.wikibricks/config.yml`:

```yaml
sync:
  profile: PROFILE
  project: PROJECT
  branch: production
  endpoint: primary
  database: wikibricks
```

The local background cycle connects once a day. It pushes bounded immutable
versions, pulls new manifests, and applies low-risk patches whose base version
and content hash still match. A divergent local edit wins automatically and is
recorded as `keep_local`. Remote failures are logged and never block an agent.

The weekly Databricks job reads the archive and publishes manifests. The next
local cycle applies them to local PostgreSQL. The local MCP process initiates
both directions of the exchange; the Databricks job never connects to the
laptop. The guarded exchange is documented in
[`docs/curation-sync.md`](docs/curation-sync.md), including manual recovery
commands.

Every bundle target keeps the weekly schedule paused by default:

```bash
databricks bundle validate --strict -t staging --profile PROFILE
databricks bundle deploy -t staging --profile PROFILE
```

Enable the production schedule only after reviewing a staging manifest:

```bash
databricks bundle deploy -t personal --profile PROFILE \
  --var="schedule_pause_status=UNPAUSED"
```

## Develop

From a checkout:

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
uv sync --extra dev
uv run ruff check src tests
uv run pytest
UV_OFFLINE=1 uv run pytest
uv build
```

The overnight-dev pre-commit hook runs Ruff and the full suite. Contributor
rules are in [`AGENTS.md`](AGENTS.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache 2.0. See [`LICENSE`](LICENSE).
