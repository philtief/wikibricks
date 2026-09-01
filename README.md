# WikiBricks

WikiBricks is one local memory shared by Codex, Claude Code, Kimi, and other
agent harnesses. Omnigent manages the sessions and launches the native harness.
A shared WikiBricks skill tells that harness when to recall and save context;
standard MCP provides the memory tools. There is no separate WikiBricks agent.

The active memory is a SQLite database at `~/.wikibricks/wikibricks.db`.
Search, recall, writes, and maintenance work without Databricks, PostgreSQL,
credentials, or a network connection. Lakebase and a weekly Databricks job are
optional. They archive history and propose guarded cleanup patches for the
local database.

## The architecture

```text
                  Omnigent
         UI, sessions, runtime selection
                       |
          Codex  Claude Code  Kimi
             native harness process
                       |
       WikiBricks shared skill + MCP
                       |
                       v
            ~/.wikibricks/wikibricks.db
               SQLite + FTS5 + WAL
                       ^
                       |
     clients outside Omnigent via wikibricks-mcp

optional, once a week:

local SQLite <---- guarded sync ----> Lakebase <---- Databricks curation job
```

After installation, use the normal `omnigent codex`, `omnigent claude`, or
`omnigent kimi` command. The selected harness loads the WikiBricks skill and
MCP server from its user-level configuration. A Codex session can therefore
inform later Claude Code or Kimi work without selecting a memory agent or
running a memory command.

WikiBricks uses agent-driven curation. It stores findings, decisions,
comparisons, and reusable answers that the agent judges useful instead of
copying every message. This keeps the maintained wiki smaller than the chat
history and follows the LLM Wiki pattern.

The integration uses the public configuration interfaces of Omnigent 0.11.0,
Codex, Claude Code, and Kimi. WikiBricks does not patch, import, or require the
Omnigent source tree. Generic MCP has no portable session lifecycle, so
WikiBricks does not claim automatic transcript recording.

## Install

SQLite is included with Python. WikiBricks has no local database service to
install. Omnigent 0.11.0 or newer is required for the native installer.

Install the official Omnigent release with Homebrew:

```bash
brew tap omnigent-ai/tap
brew install omnigent-ai/tap/omnigent
omnigent --version
```

The Homebrew tap currently publishes Omnigent 0.10.0. Until it publishes
0.11.0, unlink that formula and install the official PyPI release:

```bash
brew unlink omnigent
uv tool install --force omnigent==0.11.0
```

Install WikiBricks once, then use the usual Omnigent commands:

```bash
uv tool install "wikibricks @ git+https://github.com/philtief/wikibricks.git"
wikibricks install omnigent

omnigent codex
# or: omnigent claude
# or: omnigent kimi
```

The installer:

- initializes `~/.wikibricks/wikibricks.db`;
- installs the same `wikibricks-memory` skill in the shared, Codex, and Claude
  Code user skill directories;
- registers `wikibricks-mcp` through the Codex and Claude Code user-level
  configuration commands when those clients are installed;
- prepares Kimi's user-level `~/.kimi/mcp.json`;
- removes the previous WikiBricks agent profile when it is still selected.

The command preserves unrelated MCP servers and Omnigent settings, including a
configured remote server. Rerun it after installing a previously missing
Codex or Claude Code binary.

This native installer currently covers Codex, Claude Code, and Kimi. Other
Omnigent launchers can use `wikibricks-mcp`, but they are not yet part of the
zero-configuration installation contract.

Set `WIKIBRICKS_DATABASE_PATH` only when you want a non-default database path.

The same Codex, Claude Code, and Kimi configuration also works when those
clients run outside Omnigent. For another MCP client, install WikiBricks and
register `wikibricks-mcp` once:

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

The MCP server exposes one fixed contract to Omnigent and direct clients:

- `wiki_search` finds relevant pages and raw session evidence.
- `wiki_read_full` reads one page or reconstructs one session.
- `wiki_index` lists maintained pages.
- `wiki_write_page` creates or updates a page.
- `wiki_promote_answer` saves an answer and links it to its source pages.

Tool names and schemas do not change between harnesses.

## Background maintenance

The MCP server starts a local scheduler when a configured harness launches it.
It performs a cheap due-work check every five minutes.
Deterministic local maintenance runs once a day and repairs search metadata,
rebuilds `_meta/index`, and reports duplicate or orphan pages. It does not call
a model.

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

Reinstall WikiBricks with the Lakebase extra. Omnigent remains unchanged:

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

The Omnigent database importer is an optional, read-only recovery command. The
native integration does not poll `~/.omnigent/chat.db` during normal work.

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
