# WikiBricks

WikiBricks gives AI agents durable memory in local PostgreSQL. Recording,
search, reads, writes, history, and maintenance work without Databricks or a
network connection.

Lakebase is optional. It stores an archive and can run a weekly curation job,
but it is never part of the local read or write path.

## Install

WikiBricks supports PostgreSQL 16 and 17. On macOS:

```bash
brew install uv postgresql@17
brew services start postgresql@17
uv tool install "wikibricks @ git+https://github.com/philtief/wikibricks.git@feat/lakebase-remote-maintenance"
wikibricks init
wikibricks check
```

The default database URL is `postgresql:///wikibricks`. Set another URL before
initialization if needed:

```bash
export WIKIBRICKS_DATABASE_URL=postgresql://user:password@localhost/wikibricks
wikibricks init
```

`wikibricks init` creates the database, enables `pg_trgm`, and applies the
forward-only migrations. You can run it again after an upgrade.

For development from a checkout:

```bash
git clone --branch feat/lakebase-remote-maintenance https://github.com/philtief/wikibricks.git
cd wikibricks
uv sync --extra dev
uv run wikibricks init
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

## Import sessions

Omnigent stores conversations in `~/.omnigent/chat.db`. WikiBricks reads that
database without modifying it and preserves agent metadata, including Codex
sessions:

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
          explicit archive sync only
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

Local maintenance is deterministic and offline:

```bash
wikibricks curate
wikibricks check
wikibricks vacuum
```

Run `wikibricks curate` after a large import or from a daily local scheduler.
It repairs search chunks, rebuilds `_meta/index`, and reports duplicates and
orphans. It does not call a model.

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
sync:
  batch_size: 1000
  apply_policy: safe
```

Unknown keys and invalid values fail during startup. Explicit CLI or API
arguments take precedence over environment variables, the user file, and the
packaged defaults.

## Optional Lakebase archive

Install the Lakebase extra on the machine that runs archive sync:

```bash
uv tool install --force \
  "wikibricks[lakebase] @ git+https://github.com/philtief/wikibricks.git@feat/lakebase-remote-maintenance"
```

Push a bounded archive batch explicitly:

```bash
wikibricks sync lakebase \
  --profile PROFILE \
  --project PROJECT \
  --branch production \
  --endpoint primary \
  --database wikibricks
```

Add `--drain` to send consecutive batches. Immutable IDs and hashes make a
retry idempotent.

The optional weekly job reads archived evidence and publishes immutable
curation manifests. It cannot update a local database directly. Pulling a
manifest also leaves active pages unchanged:

```bash
wikibricks sync lakebase --profile PROFILE --project PROJECT --pull-patches
wikibricks sync plan RUN_ID --policy safe
wikibricks sync apply RUN_ID --policy safe
wikibricks sync conflicts
```

An update applies only when its base version ID and content hash still match.
Local edits win conflicts until you resolve them explicitly. The full protocol
is in [`docs/curation-sync.md`](docs/curation-sync.md).

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
