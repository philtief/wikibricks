# WikiBricks

WikiBricks is local PostgreSQL memory for AI agents. Recording, search, reads,
writes, history, and MCP tools run without Databricks credentials or network
access. Lakebase is an optional archive that WikiBricks contacts only when you
run an explicit sync command.

The local database is the source of truth:

```text
Claude Code hooks     Omnigent chat.db     Other harnesses
        |                    |               JSONL v1
        +--------------------+-------------------+
                             |
                     normalized sessions
                             |
                             v
                  local PostgreSQL + GIN
                             |
                   local stdio MCP server
                             |
                  explicit archive sync
                             v
                  Lakebase (optional)
```

The core has no model dependency. Your agent decides what to search, read, and
promote. The design follows Andrej Karpathy's
[LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## Local setup

WikiBricks supports PostgreSQL 16 and 17. On macOS, Homebrew can install and
start PostgreSQL:

```bash
brew install postgresql@17
brew services start postgresql@17
```

Clone WikiBricks and install it as a local tool:

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
uv tool install --force .

export WIKIBRICKS_DATABASE_URL=postgresql:///wikibricks
wikibricks init
wikibricks check
```

The default URL is `postgresql:///wikibricks`, which uses the local Unix
socket and operating-system user. Set `WIKIBRICKS_DATABASE_URL` for another
user, socket, port, or database. `wikibricks init` creates the database when
it does not exist and applies forward-only migrations.

Write and find a page from Python:

```python
from wikibricks import WikiClient

wiki = WikiClient()
wiki.write_page(
    "topics/local-memory",
    "Local memory",
    {"summary": "PostgreSQL is the source of truth.", "body": "Full notes."},
    tags=["architecture"],
)

print(wiki.search("source of truth"))
print(wiki.read_page("topics/local-memory"))
```

## Connect an agent through MCP

`wikibricks-mcp` is a local stdio server. It exposes five tools:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

During MCP initialization, the server also sends the agent the WikiBricks
schema. It tells the agent to treat sessions as raw evidence and to maintain
linked topic, entity, comparison, and synthesis pages. This keeps the
Karpathy-style compounding workflow independent of the agent harness.
`wiki_index` lists curated pages. `wiki_search` can also return raw sessions as
evidence.

Codex can register it directly:

```bash
codex mcp add wikibricks \
  --env WIKIBRICKS_DATABASE_URL=postgresql:///wikibricks \
  -- wikibricks-mcp
```

Claude Code uses the same server:

```bash
claude mcp add --scope user \
  -e WIKIBRICKS_DATABASE_URL=postgresql:///wikibricks \
  wikibricks -- wikibricks-mcp
```

For Omnigent or another MCP client, configure a stdio server with command
`wikibricks-mcp` and pass `WIKIBRICKS_DATABASE_URL` in its environment. The
server has no Claude, Codex, Omnigent, or Databricks assumptions.

## Record sessions

MCP gives an agent access to memory. A source adapter records the agent's
sessions.

### Omnigent and Codex

Omnigent stores conversations in `~/.omnigent/chat.db`. WikiBricks opens that
file in read-only mode and keeps a resumable cursor:

```bash
wikibricks import omnigent --user-id "$USER"
```

Use `--since-days` or `--limit` for a bounded first import. The adapter keeps
the Omnigent agent name. A Codex conversation receives the
`agent:codex-native-ui` tag.

The import is safe to rerun. An unchanged conversation is not imported twice.
If an event changes at the source, WikiBricks writes a new immutable event
version.

### Claude Code

The optional Claude Code adapter records lifecycle, prompt, tool-call, and
tool-result events. It writes only to local PostgreSQL. See
[`plugin/README.md`](plugin/README.md) for the plugin and manual hook setup.

### Other harnesses

Any harness can emit one JSON object per line. The current schema version is
1:

```json
{"schema_version":1,"session":{"harness":"my-harness","external_id":"session-42","user_id":"philipp","agent":"my-agent","workspace":"/work/project","started_at":"2026-08-31T08:00:00Z","updated_at":"2026-08-31T08:05:00Z","events":[{"external_id":"0","kind":"user","content":"Remember this"},{"external_id":"1","kind":"assistant","content":"Stored"}],"metadata":{}}}
```

Import it with:

```bash
wikibricks import jsonl examples/session-v1.jsonl
```

Supported event kinds are `user`, `assistant`, `tool_call`, `tool_result`,
`error`, and `lifecycle`. Unsupported schema versions fail with a clear error.

## PostgreSQL storage for long sessions

PostgreSQL native `text` stores page and session content. PostgreSQL moves
large values to [TOAST](https://www.postgresql.org/docs/current/storage-toast.html)
automatically, so WikiBricks does not need a custom large-text extension. Each
session is a sequence of immutable event versions. Appending an event does not
rewrite the full transcript.

WikiBricks requires
[`pg_trgm`](https://www.postgresql.org/docs/current/pgtrgm.html) for path and
title matching. Full-text search uses built-in
[`tsvector`](https://www.postgresql.org/docs/current/datatype-textsearch.html)
values and GIN indexes. It indexes long events in 64 KiB UTF-8 chunks, then
reconstructs reads from the unchanged source text. This avoids the size limit
of a single `tsvector`. A 25 MB tool result is part of the storage test suite.

The base package does not install `pgvector` or an embedding model. Local
search requires no embedding service. Semantic search can remain an optional
feature later.

## Local maintenance

```bash
wikibricks check
wikibricks curate
wikibricks search "index failure" -k 10
wikibricks backup backups/wikibricks.dump
wikibricks vacuum
```

Restore requires a connection URL whose target database does not exist:

```bash
wikibricks --database-url postgresql:///wikibricks_restored \
  restore backups/wikibricks.dump
```

`wikibricks check` reports broken version pointers and the number of pending
archive events. Local reads and writes do not wait for those events to sync.

`wikibricks curate` is the local deterministic loop. It repairs missing search
documents, materializes `_meta/index`, and reports exact duplicate and orphan
pages for the active agent to review. It does not need a model or network
connection. Run it after a large ingest or once a day. Raw sessions remain
searchable evidence, but they do not enter the curated `_meta/index` page.

The active harness performs semantic curation during normal work. Its MCP
instructions tell it to search before it writes, update existing pages, preserve
provenance, and record contradictions. The local deterministic pass then
checks structure and search metadata. Monthly remote maintenance can compare
longer history and propose deduplication or contradiction patches. Local code
will accept a future patch only when its base content hash still matches.

After a verified archive sync and backup, an explicit retention policy can
remove old raw sessions while keeping curated wiki pages local:

```bash
wikibricks curate --prune-archived-sessions-after-days 90
```

The command removes a session only when every event version has a committed
remote acknowledgement. Unarchived sessions remain local regardless of age.

## Optional Lakebase archive

Install the optional Databricks dependency only on a machine that performs
archive sync:

```bash
uv tool install --force ".[databricks]"
```

Then run the sync explicitly:

```bash
wikibricks sync lakebase \
  --profile <databricks-profile> \
  --project <lakebase-project> \
  --branch production \
  --endpoint primary \
  --database wikibricks
```

The command obtains a short-lived Lakebase credential from the selected
Databricks profile. It copies a bounded outbox batch into a staging table,
commits by immutable event ID and hash, and acknowledges local rows only after
the remote commit. A retry after a lost connection does not duplicate data.

Use `--pull-curated` to import a newer remote `curated_pages` snapshot into the
local archive cache. A remote page never overwrites a locally changed page.

The local 0.8.0 release includes the archive protocol and local PostgreSQL
contract tests. [Lakebase Change Data
Feed](https://docs.databricks.com/aws/en/oltp/projects/quickstart-lakebase-cdf),
Delta curation, the read-only synced table, and migration of the current
remote wiki are the next remote phase. They remain undeployed until the local
gate is approved. Electric is also deferred because its
[released write path](https://electric-sql.com/docs/guides/writes) does not yet
provide the offline upstream-write and conflict contract that WikiBricks
needs.

The remote phase will replace the read-only snapshot cache as the final
curation interface with versioned patch sets. Each patch will include its base
content hash. Local curation will apply unchanged bases and queue local edit
conflicts for review.

## Legacy Databricks compatibility

The existing Delta, Vector Search, Unity Catalog, jobs, and app assets remain
in this development repository for migration and compatibility. They are not
used by `WikiClient()` or `wikibricks-mcp`.

Install `wikibricks[databricks]` and pass a `warehouse_id` explicitly to use
the legacy client:

```python
from wikibricks import WikiClient

remote_wiki = WikiClient(warehouse_id="<sql-warehouse-id>")
```

No call falls back to Databricks when local PostgreSQL is unavailable.

## Development

```bash
uv sync --extra dev
uv run pytest                        # 983 tests
uv run ruff check src tests scripts
uv build                             # dist/wikibricks-0.8.0-py3-none-any.whl
UV_OFFLINE=1 uv run pytest
```

The overnight-dev pre-commit hook runs the lint and test commands before each
commit. Coding agents must also follow [`AGENTS.md`](AGENTS.md).

## License

Apache 2.0. See [`LICENSE`](LICENSE).
