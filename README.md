# WikiBricks

WikiBricks gives AI coding agents durable local memory in PostgreSQL.

```bash
pip install wikibricks
wikibricks init
wikibricks-mcp
```

PostgreSQL is the only runtime for recording, search, reads, writes, history,
and MCP tools. Databricks credentials and network access are not required.
Lakebase is an optional archive used only by an explicit sync command.

## Install locally

WikiBricks supports PostgreSQL 16 and 17. Start PostgreSQL before running the
three commands above. On macOS:

```bash
brew install postgresql@17
brew services start postgresql@17
```

The default connection is `postgresql:///wikibricks`. Override it when needed:

```bash
export WIKIBRICKS_DATABASE_URL=postgresql://user:password@localhost/wikibricks
wikibricks init
wikibricks check
```

`wikibricks init` creates the database, installs the `pg_trgm` extension, and
applies forward-only migrations. It is safe to run again.

## Connect an agent

`wikibricks-mcp` is a local stdio server with five tools:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

Codex can register the server directly:

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

For Omnigent or any other MCP client, configure a stdio server with command
`wikibricks-mcp` and pass `WIKIBRICKS_DATABASE_URL` in its environment. The
tool names and schemas do not depend on a specific agent harness.

Python callers use the same local store:

```python
from wikibricks import WikiClient

wiki = WikiClient()
wiki.write_page(
    "topics/local-memory",
    "Local memory",
    {
        "summary": "PostgreSQL is the active memory store.",
        "body": "Databricks is not part of the local read or write path.",
    },
    tags=["architecture"],
)

print(wiki.search("active memory"))
print(wiki.read_page("topics/local-memory"))
```

## How memory compounds

WikiBricks follows Andrej Karpathy's
[LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
Recorded sessions remain raw evidence. The active agent searches that evidence
and maintains a smaller wiki of topic, entity, comparison, and synthesis pages.
Those pages carry links and source IDs, so later sessions can refine knowledge
instead of appending another transcript summary.

The MCP server sends this workflow to every connected harness. The core library
does not call a language model. Curation decisions stay with Codex, Claude,
Omnigent, or whichever agent uses the tools.

```text
Codex / Claude / Omnigent / another harness
                    |
           MCP tools or session import
                    |
                    v
        local PostgreSQL active memory
          |                       |
    raw session evidence     maintained wiki pages
          |                       |
          +-------- search -------+
                    |
          deterministic local curate
                    |
          explicit archive sync only
                    v
             Lakebase (optional)
```

## Import sessions

Omnigent stores conversations in `~/.omnigent/chat.db`. WikiBricks opens the
file read-only and keeps a local cursor:

```bash
wikibricks import omnigent --user-id "$USER"
```

Use `--since-days` or `--limit` for a bounded first import. Agent metadata is
preserved, including Codex sessions. Re-importing unchanged events is a no-op;
changed source events create immutable event versions.

The optional Claude Code plugin records lifecycle, prompt, tool-call, and
tool-result events into local PostgreSQL. Installation instructions are in
[`plugin/README.md`](plugin/README.md).

Other harnesses can emit the versioned JSONL contract:

```json
{"schema_version":1,"session":{"harness":"my-harness","external_id":"session-42","user_id":"philipp","agent":"my-agent","workspace":"/work/project","events":[{"external_id":"0","kind":"user","content":"Remember this"},{"external_id":"1","kind":"assistant","content":"Stored"}],"metadata":{}}}
```

```bash
wikibricks import jsonl examples/session-v1.jsonl
```

Event kinds are `user`, `assistant`, `tool_call`, `tool_result`, `error`, and
`lifecycle`.

## Long sessions and search

PostgreSQL `text` and TOAST store large event content without a custom text
extension. Each session is an ordered sequence of immutable event versions, so
adding an event does not rewrite the transcript.

Built-in `tsvector` values and GIN indexes provide full-text search. WikiBricks
splits long events into 64 KiB UTF-8 search chunks and reconstructs reads from
the original text. `pg_trgm` indexes paths and titles. It is the only required
PostgreSQL extension. The base package does not install `pgvector`, create
embeddings, or contact an embedding service.

## Keep local memory clean

The active agent handles semantic maintenance while it works: search before
writing, update an existing page when possible, preserve evidence, and record
contradictions. Local deterministic maintenance handles database hygiene:

```bash
wikibricks curate
wikibricks check
wikibricks vacuum
```

`wikibricks curate` repairs missing search chunks, rebuilds `_meta/index`, and
reports duplicate or orphan pages. It does not call a model or use the network.
Run it after a large import or on a local schedule.

Back up before retention or a large curation apply:

```bash
wikibricks backup backups/wikibricks.dump
wikibricks --database-url postgresql:///wikibricks_restored \
  restore backups/wikibricks.dump
```

Old sessions can be pruned only after every immutable event version has a
committed archive acknowledgement:

```bash
wikibricks curate --prune-archived-sessions-after-days 90
```

## Readable configuration and contracts

Create `~/.wikibricks/config.yml` to override packaged defaults:

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

Precedence is packaged defaults, the user file, `WIKIBRICKS_CONFIG`, environment
variables, then explicit API or CLI arguments. Unknown YAML keys and invalid
values fail during startup.

Agent guidance and interchange contracts ship as editable text files:

- `src/wikibricks/resources/agent-instructions.md`
- `src/wikibricks/resources/mcp-tools.json`
- `src/wikibricks/resources/schemas/session-record.schema.json`
- `src/wikibricks/resources/schemas/curation-manifest-v1.schema.json`

## Optional Lakebase archive

Install the extra only on a machine that runs archive sync:

```bash
pip install "wikibricks[lakebase]"
```

Archive a bounded batch explicitly:

```bash
wikibricks sync lakebase \
  --profile PROFILE \
  --project PROJECT \
  --branch production \
  --endpoint primary \
  --database wikibricks
```

The command obtains a short-lived Lakebase credential, copies immutable page
and session versions, commits by ID and hash, then acknowledges local outbox
rows. Interrupted syncs retry the same batch without duplicating remote data.
Normal WikiBricks commands never invoke this adapter.

A monthly remote process can publish curation manifests back to a local inbox.
Pulling a manifest does not change active pages:

```bash
wikibricks sync lakebase --profile PROFILE --project PROJECT --pull-patches
wikibricks sync plan RUN_ID --policy safe
wikibricks sync apply RUN_ID --policy safe
wikibricks sync conflicts
```

Updates apply only when the local base version ID and content hash still match.
Conflicting local edits remain active until an explicit resolution. Duplicate
cleanup updates pages, links, aliases, and receipts in one transaction. See
[`docs/curation-sync.md`](docs/curation-sync.md) for the protocol and runbook.

The Lakebase migration and monthly Databricks maintenance job are separate from
this local release and have not been deployed.

## Develop

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
uv sync --extra dev
uv run pytest                     # 83 tests
uv run ruff check src tests
uv build
UV_OFFLINE=1 uv run pytest
```

The overnight-dev pre-commit hook runs Ruff and the full test suite before each
commit. Contributors should read [`AGENTS.md`](AGENTS.md) and
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache 2.0. See [`LICENSE`](LICENSE).
