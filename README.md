# WikiBricks

WikiBricks is one local memory shared by Codex, Claude Code, Kimi, and other
agent harnesses. Omnigent-managed sessions use it automatically. Other clients
connect through standard MCP.

The active memory is a SQLite database at `~/.wikibricks/wikibricks.db`. Local
capture, search, recall, and maintenance work without Databricks, PostgreSQL,
credentials, or a network connection. Lakebase and a weekly Databricks job are
optional. They archive history and propose guarded cleanup patches for the
local database.

## The architecture

```text
                Omnigent
     Codex  Claude Code  Kimi  other runners
        \       |        |       /
         automatic capture + pre-turn recall
                         |
                         v
              ~/.wikibricks/wikibricks.db
                 SQLite + FTS5 + WAL
                         ^
                         |
       standard MCP clients via wikibricks-mcp

optional, once a week:

local SQLite <---- guarded sync ----> Lakebase <---- Databricks curation job
```

Omnigent is the zero-touch path. It records committed session events in the
background and adds relevant memory to the next agent turn. The injected block
is bounded, treated as reference material, and removed before Omnigent stores
or displays the user message. If WikiBricks is unavailable or recall exceeds
250 ms, the turn continues without memory.

The original runner remains visible in event metadata, so a Codex session can
inform a later Claude Code or Kimi session without creating separate stores.
All five WikiBricks tools also use Omnigent's existing local tool relay.

MCP is the portable path. Any compatible client gets the same tools and
packaged instructions. The instructions tell the agent to search before work,
read relevant pages, and save knowledge that will be useful again. Generic MCP
does not expose a standard session lifecycle, so full transcript capture is an
Omnigent feature. Other clients can still import sessions through the JSONL
contract.

## Install

SQLite is included with Python. WikiBricks has no local database service to
install.

For Omnigent, install WikiBricks in the same tool environment:

```bash
uv tool install --force git+https://github.com/philtief/omnigent.git \
  --with "wikibricks @ git+https://github.com/philtief/wikibricks.git"
```

Start Omnigent and work normally. Every Omnigent-managed harness uses the same
memory. No MCP entry, hook, scheduler, or prompt convention is required.

Set `WIKIBRICKS_ENABLED=false` to disable the integration. Set
`WIKIBRICKS_DATABASE_PATH` only when you want a non-default database path.

For Codex, Claude Code, Kimi, or another client used outside Omnigent, install
the MCP server once:

```bash
uv tool install "wikibricks @ git+https://github.com/philtief/wikibricks.git"
```

Register `wikibricks-mcp` in the client.

Codex:

```bash
codex mcp add wikibricks -- wikibricks-mcp
```

Claude Code:

```bash
claude mcp add --scope user wikibricks -- wikibricks-mcp
```

Generic MCP configuration, including clients that use JSON configuration:

```json
{
  "mcpServers": {
    "wikibricks": {
      "command": "wikibricks-mcp"
    }
  }
}
```

Restart the client after registration. All clients that use the default path
now share the same database.

## Memory model

WikiBricks keeps two layers:

1. Raw sessions are immutable evidence. Events are ordered, versioned, and
   tagged with their source session, runner, user, and workspace.
2. Wiki pages are maintained knowledge. Agents update topic, entity,
   comparison, guide, and synthesis pages instead of creating one summary per
   chat.

This follows Andrej Karpathy's
[LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
search the existing wiki, integrate new evidence into the right pages, preserve
links to sources, and keep the result readable as ordinary files. Export with:

```bash
python -m wikibricks.export_karpathy ./wiki
```

The local store uses SQLite WAL for concurrent readers and writers. FTS5
indexes bounded chunks while the original event text remains intact. No
embedding model or vector database is required.

## Tools

The Omnigent relay and MCP server expose the same fixed contract:

- `wiki_search` finds relevant pages and raw session evidence.
- `wiki_read_full` reads one page or reconstructs one session.
- `wiki_index` lists maintained pages.
- `wiki_write_page` creates or updates a page.
- `wiki_promote_answer` saves an answer and links it to its source pages.

Tool names and schemas do not change between harnesses.

## Background maintenance

Omnigent and the MCP server start the same local scheduler. It performs a cheap
due-work check every five minutes. Deterministic local maintenance runs once a
day and repairs search metadata, rebuilds `_meta/index`, and reports duplicate
or orphan pages. It does not call a model.

These commands are available for diagnosis and recovery:

```bash
wikibricks check
wikibricks curate
wikibricks backup ~/.wikibricks/backups/wikibricks.db
wikibricks vacuum
```

Session deletion is archive-gated. A retention run can remove an old session
only after every immutable event version has a committed archive receipt:

```bash
wikibricks curate --prune-archived-sessions-after-days 90
```

## Optional Lakebase curation

Local memory does not need Lakebase. Configure it only when you want a remote
archive and a weekly semantic cleanup pass.

For Omnigent, reinstall the tool with the Lakebase extra in the same
environment:

```bash
uv tool install --force git+https://github.com/philtief/omnigent.git \
  --with "wikibricks[lakebase] @ git+https://github.com/philtief/wikibricks.git"
```

For a standalone MCP installation, reinstall WikiBricks with the Lakebase
extra:

```bash
uv tool install --force \
  "wikibricks[lakebase] @ git+https://github.com/philtief/wikibricks.git"
```

Create `~/.wikibricks/config.yml`:

```yaml
version: 1
sync:
  interval_hours: 168
  profile: PROFILE
  project: PROJECT
  branch: production
  endpoint: primary
  database: wikibricks
```

The local scheduler pushes immutable versions to Lakebase and pulls published
curation manifests. The Databricks job reads the archive and writes data-only
patches. It never connects to the laptop.

A patch updates a local page only when its base version and content hash still
match. If the page changed locally, the local version wins and WikiBricks
records `keep_local`. Pull and apply are idempotent, and each patch group is
transactional. Remote errors never block local capture, recall, or writes.

Every bundle target is paused by default:

```bash
databricks bundle validate --strict -t staging --profile PROFILE
databricks bundle deploy -t staging --profile PROFILE
```

The full protocol is in [`docs/curation-sync.md`](docs/curation-sync.md).

## Import and migration

Use JSONL when another system can export the versioned session contract:

```bash
wikibricks import jsonl examples/session-v1.jsonl
```

The Omnigent database importer remains a one-time recovery command. Normal
Omnigent sessions use direct capture and never poll `~/.omnigent/chat.db`.

```bash
wikibricks import omnigent --user-id "$USER"
```

Existing PostgreSQL installations can move to SQLite once:

```bash
wikibricks migrate-postgres \
  --source-url postgresql:///wikibricks \
  ~/.wikibricks/wikibricks.db
```

PostgreSQL remains an optional compatibility backend. It is not required for a
new local installation.

## Develop

```bash
git clone https://github.com/philtief/wikibricks.git
cd wikibricks
uv sync --extra dev
uv run ruff check src tests
uv run pytest -q
UV_OFFLINE=1 uv run pytest -q
uv build
```

The overnight-dev pre-commit hook runs Ruff and the full suite. Contributor
rules are in [`AGENTS.md`](AGENTS.md) and
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache 2.0. See [`LICENSE`](LICENSE).
