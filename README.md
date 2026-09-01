# WikiBricks

WikiBricks gives AI agents durable memory in local PostgreSQL. Recording,
search, reads, writes, history, and maintenance work without Databricks or a
network connection.

Lakebase is optional. It stores an archive and can run a weekly curation job,
but it is never part of the local read or write path.

After the one-time install, WikiBricks runs behind the agent. The MCP process
captures Omnigent sessions, performs local maintenance, retrieves relevant
memory, and applies safe remote curation. Normal use requires no WikiBricks
commands.

## Install

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
when the agent starts. `wikibricks init` remains available for diagnostics.

For development from a checkout:

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
uv sync --extra dev
```

## Connect an agent

`wikibricks-mcp` is a local stdio MCP server. It works with Codex, Claude Code,
Omnigent, and other MCP clients.

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

The server exposes five tools:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

Their names, descriptions, and schemas do not depend on an agent harness.
The server tells the agent to search relevant memory at the start of a task and
read the best pages before answering. This is part of the MCP contract, so the
user does not need to request a memory lookup.

## Import sessions

Omnigent stores conversations in `~/.omnigent/chat.db`. While an agent is
connected, WikiBricks reads new and changed conversations in the background.
The importer is read-only, supports a live SQLite WAL, and preserves agent
metadata, including Codex sessions.

The command below is a recovery and bulk-import tool. It is not part of normal
use:

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

## How the memory works

WikiBricks follows Andrej Karpathy's
[LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
Raw sessions remain evidence. The active agent maintains a smaller set of
linked topic, entity, comparison, and synthesis pages.

```text
Codex / Claude / Omnigent / another harness
                    |
             MCP or session import
                    v
        local PostgreSQL active memory
          |                       |
    immutable evidence       maintained pages
          |                       |
          +--------- search ------+
                    |
        deterministic local maintenance
                    |
          optional background archive sync
                    v
             Lakebase (optional)
```

The library does not call a language model. The connected agent decides what
to search, write, link, merge, or promote.

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
local cycle applies them to local PostgreSQL. Lakebase never connects to the
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

```bash
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
