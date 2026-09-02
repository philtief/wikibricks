# WikiBricks

WikiBricks gives Codex, Claude Code, Kimi, and other agent harnesses one local
memory. Each client connects through MCP and reads or writes the same database.
The shared WikiBricks skill tells the active agent when to recall and save
context. There is no separate WikiBricks agent.

Omnigent is the primary interface for choosing a harness and managing sessions,
but it is optional. A standalone Codex or Claude Code installation uses the
same memory and the same setup command.

The active memory is a SQLite database at `~/.wikibricks/wikibricks.db`.
Search, recall, writes, and maintenance work without Databricks, PostgreSQL,
credentials, or a network connection. Lakebase and a weekly Databricks job are
optional. They archive history and propose guarded cleanup patches for the
local database.

## The architecture

```text
            Omnigent (optional)
        UI, sessions, runtime selection
                       |
 Codex  Claude  Debby  Goose  Hermes  Kimi
 Kiro   OpenCode  Pi   Polly  Qwen
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

After installation, the selected harness loads the WikiBricks skill and MCP
server from its user configuration. A Codex session can inform later Claude
Code, Kimi, or Goose work without selecting a memory agent or running a memory
command.

WikiBricks uses agent-driven curation. It stores findings, decisions,
comparisons, and reusable answers that the agent judges useful instead of
copying every message. This keeps the maintained wiki smaller than the chat
history and follows the LLM Wiki pattern.

The integration uses public client configuration and the Omnigent 0.11 harness
configuration. WikiBricks does not patch or import the Omnigent source tree.
Generic MCP has no portable session lifecycle, so WikiBricks does not claim
automatic transcript recording.

## Install

WikiBricks requires Python 3.10 or newer. SQLite is included with Python, so
there is no local database service to install.

```bash
uv tool install wikibricks
wikibricks install
```

`wikibricks install` initializes `~/.wikibricks/wikibricks.db`, installs the
shared memory skill, and detects supported clients on `PATH`. If Omnigent is
absent, it configures only the clients it finds. This is enough for a machine
that has only Codex or only Claude Code.

### Claude Code without Omnigent

You do not need Omnigent to use WikiBricks. If Claude Code is your only agent
client, run:

```bash
claude --version
uv tool install wikibricks
wikibricks install
claude mcp get wikibricks
```

The installer registers `wikibricks-mcp` in Claude Code's user configuration
and installs the `wikibricks-memory` skill. Start Claude Code as usual:

```bash
claude
```

Claude Code starts the MCP server when it needs it. WikiBricks does not create
an Omnigent configuration or require a background daemon. On a Codex-only
machine, the same `wikibricks install` command detects and configures Codex.

To test unreleased changes from `main`, replace the first command with:

```bash
uv tool install --force "wikibricks @ git+https://github.com/philtief/wikibricks.git"
```

### Omnigent

Install Omnigent 0.11.0 or newer with Homebrew, then run the same WikiBricks
installer:

```bash
brew tap omnigent-ai/tap
brew install omnigent-ai/tap/omnigent
omnigent --version

uv tool install wikibricks
wikibricks install
```

When Omnigent is present, the installer prepares all of its bundled harnesses:

```bash
omnigent claude
omnigent codex
omnigent debby
omnigent goose
omnigent hermes
omnigent kimi
omnigent kiro
omnigent opencode
omnigent pi
omnigent polly
omnigent qwen
```

Codex and Claude Code use their native user-level MCP commands. Goose, Hermes,
Kimi, Kiro, OpenCode, and Qwen use their standard configuration files. Pi gets
a user extension that forwards the same five tools over MCP. Debby and Polly
reuse Claude's user MCP settings. Small launch wrappers make WikiBricks visible
inside the isolated OpenCode and Hermes sessions created by Omnigent.

The installer validates existing JSON and YAML before writing, changes only
WikiBricks-owned keys, and preserves unrelated MCP servers and Omnigent
settings. Run it again after adding a client. `wikibricks install omnigent`
remains available as a compatibility alias that requires Omnigent.

Set `WIKIBRICKS_DATABASE_PATH` only when you want a non-default database path.
All configured clients use the same path.

### Other MCP clients

Clients outside Omnigent use the same installation. For an MCP client that the
installer does not detect, register `wikibricks-mcp` in its user configuration:

```json
{
  "mcpServers": {
    "wikibricks": {
      "command": "wikibricks-mcp"
    }
  }
}
```

Restart the client after registration. It will share the default database with
every other configured client.

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
uv tool install --force "wikibricks[lakebase]"
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
