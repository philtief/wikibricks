# SQLite and Omnigent shared memory design

## Decision

WikiBricks will provide one local memory shared by the agent harnesses that a
person runs through Omnigent. Omnigent is the user interface, session owner,
and harness orchestrator. WikiBricks is the durable memory layer. Codex,
Claude Code, Kimi, and other Omnigent runners will use the same memory without
requiring separate WikiBricks plugins.

SQLite will replace native PostgreSQL as the default and only active local
backend. The database will live at `~/.wikibricks/wikibricks.db`. Direct
harness sessions outside Omnigent will continue to use the standard
`wikibricks-mcp` server. Lakebase and the weekly Databricks curation job will
remain optional.

## Product contract

An Omnigent user should install or update Omnigent, choose any supported
runner, and work normally. WikiBricks will:

1. retrieve relevant memory before each turn;
2. pass a bounded memory packet to the selected runner;
3. record completed conversation events after Omnigent commits them;
4. expose the same wiki tools to every runner through Omnigent's MCP relay;
5. maintain the local search index and wiki metadata in the background; and
6. contact Lakebase only when the user has configured remote archival.

Changing the runner within a conversation must not create a second memory
record. The Omnigent conversation ID is the stable session identity. The
runner name and external runner session ID are provenance.

Direct Codex, Claude Code, Kimi, and other sessions outside Omnigent get the
same search, read, and write tools through MCP. WikiBricks does not promise
automatic transcript capture or context injection outside Omnigent.

## Goals

- One local memory for all Omnigent-managed agent sessions.
- No PostgreSQL, Docker, Node.js, Brew, or Databricks prerequisite for local
  memory.
- No memory commands in normal Omnigent use.
- Offline recording, retrieval, wiki updates, and deterministic maintenance.
- A small, source-linked wiki that compounds across sessions and runners.
- Bounded retrieval that improves recall without filling the prompt with old
  transcripts.
- A stable MCP contract for use outside Omnigent.
- Safe migration of existing WikiBricks PostgreSQL data.
- Optional, failure-isolated Lakebase archive and weekly remote curation.

## Non-goals

- Automatic capture from every standalone agent CLI.
- Real-time synchronization between several laptops.
- A local background language model.
- PGlite or a local database daemon in this release.
- A new memory management UI in Omnigent.
- Embedding search or a required vector service.
- Replacing Omnigent's conversation database.

## System boundary

```text
                            one Omnigent conversation
                                       |
                         +-------------+-------------+
                         |             |             |
                       Codex       Claude Code      Kimi ...
                         \             |             /
                          +------------+------------+
                                       |
                              Omnigent memory bridge
                         before turn   |   after event
                                       |
                         ~/.wikibricks/wikibricks.db
                          raw evidence + curated wiki
                                       |
                               optional daily sync
                                       |
                                    Lakebase
                                       |
                           optional weekly curation
```

Omnigent depends on WikiBricks. WikiBricks does not depend on Omnigent for its
core storage or MCP server. The shared boundary consists of neutral Python
types and methods for session ingestion and context retrieval.

The initial integration will not introduce a general Omnigent plugin system.
Omnigent will have one small memory bridge with a no-op fallback when
WikiBricks cannot start. This keeps the first implementation narrow. A future
provider interface can be extracted if Omnigent needs a second memory
implementation.

## Local storage

### Database configuration

Every WikiBricks process will open the same SQLite file with these settings:

- WAL journal mode;
- foreign keys enabled;
- a 5-second busy timeout;
- `synchronous=NORMAL`; and
- explicit write transactions using `BEGIN IMMEDIATE`.

The package will use Python's standard `sqlite3` module. FTS5 will index page
and session chunks. JSON values will be stored as canonical JSON text. UUIDs
and timestamps will use canonical text encodings. Long sessions will remain
split into immutable ordered event-version rows. No transcript will be
appended to one growing value.

Each search chunk will retain the existing 64 KiB UTF-8 ceiling. Page and
session FTS indexes will be separate so retrieval can prefer curated pages.
Path and title matching will use exact matches, prefixes, FTS tokens, and a
small in-process fallback for spelling errors. SQLite does not need a trigram
extension for the first release.

### Schema and migrations

The existing entities will remain recognizable: pages, page versions, links,
sessions, session events, event versions, search chunks, operations, sync
outbox, sync cursors, archive records, curation manifests, receipts,
conflicts, aliases, and maintenance runs.

SQLite migrations will be numbered, forward-only SQL files recorded in
`schema_migrations`. Migration execution will take a database write lock. A
failed migration will roll back completely.

The local storage API will no longer expose a PostgreSQL connection. Each
repository will own its SQL behind a small `SQLiteStore` facade. Remote
Lakebase code will consume logical outbox records and curation manifests, not
local SQL connections.

### Background ownership

Omnigent, standalone MCP servers, and CLI commands may open the database at
the same time. A `background_leases` table will elect one process for
maintenance and remote sync. Lease acquisition and renewal will be atomic.
An expired lease can be reclaimed after a crash.

Normal page and event writes do not depend on the lease. SQLite serializes
short write transactions while allowing concurrent readers through WAL.

### Backup and recovery

`wikibricks backup` will use SQLite's online backup API. Restore will validate
the source database, copy it to a temporary file, and replace the target only
after validation. The CLI will continue to expose `check`, `curate`, and
`vacuum` commands for diagnostics.

## Omnigent integration

### Lifecycle hooks

Omnigent will call WikiBricks at four points:

1. Host startup creates the local schema and starts a bounded capture worker.
2. Before prompt dispatch, the bridge requests a memory packet.
3. After a conversation item commits, the bridge queues its conversation ID.
4. When a session becomes idle or ends, the bridge flushes pending capture.

The capture worker will debounce repeated item notifications, read the latest
normalized Omnigent conversation, and call the idempotent WikiBricks session
ingest API. The source of truth remains Omnigent's `chat.db`. If WikiBricks is
locked or temporarily unavailable, startup reconciliation will resume from a
durable cursor and import missed conversations.

The bridge must never delay persistence of an Omnigent conversation item. It
will use a bounded queue and background thread. Queue saturation can collapse
several notifications for one conversation into one later snapshot because
session ingestion is idempotent.

### Stable identity and provenance

An Omnigent session will use:

- `harness="omnigent"`;
- the Omnigent conversation ID as `external_id`;
- `runner_id` as the active agent implementation;
- `external_session_id` as runner provenance; and
- Omnigent item IDs as event IDs.

Runner changes update session metadata. They do not change the WikiBricks
session ID. Re-importing an unchanged event is a no-op. A corrected source
event creates an immutable event version.

### MCP relay

Omnigent's existing shared MCP relay will expose the five public WikiBricks
tools:

- `wiki_search`
- `wiki_read_full`
- `wiki_index`
- `wiki_write_page`
- `wiki_promote_answer`

The tools will call the same local `WikiClient` used by the memory bridge.
Runner-specific MCP configuration will not be required for
Omnigent-managed sessions.

## Retrieval and context injection

WikiBricks will add a neutral request and response contract:

```python
MemoryQuery(
    text: str,
    user_id: str,
    workspace: str | None,
    current_session_id: str | None,
    max_chars: int = 6000,
)

MemoryPacket(
    items: tuple[MemoryItem, ...],
    rendered: str,
    truncated: bool,
)
```

The query will exclude the current session to prevent prompt echo. Retrieval
will rank exact page matches and curated page summaries first. Same-workspace
items receive a ranking boost. User-level pages remain available across
workspaces. Raw session snippets are fallback evidence when the curated wiki
does not supply enough useful results.

A packet may contain at most five curated page summaries and two session
snippets. Each item is limited to 1,200 characters. The rendered packet has a
hard 6,000-character limit. Weak matches are omitted. An empty result adds no
text to the runner prompt.

Omnigent will inject the packet as framework-owned context, separate from the
user's visible message. The packet will identify every item by WikiBricks
path and label stored text as reference material, not instructions. A runner
can call `wiki_read_full` when it needs more detail.

The same rendering rules will apply to every runner. A runner switch therefore
changes the model and tool adapter, not the memory content.

## Curation loop

Raw sessions and curated pages remain separate layers. Recording a transcript
does not create a page for every conversation.

Omnigent will add a short memory policy to each runner's framework
instructions. The policy tells the runner to search before writing, update an
existing page when possible, preserve source paths, and save only knowledge
that is likely to help later work. WikiBricks tools let the active runner
perform this work during the user's normal task. There is no second model call
after every turn.

Local daily maintenance is deterministic. It will repair FTS rows, rebuild
the wiki index, report duplicate candidates and orphan pages, expire stale
background leases, and apply safe downloaded curation patches. It will not
make semantic edits.

If configured, the local background cycle will push immutable evidence and
page versions to Lakebase once a day. The Databricks job may analyze that
archive once a week and publish data-only curation manifests. Local
WikiBricks will apply only low-risk patches with matching base IDs and hashes.
Local edits win conflicts. No remote component connects to the laptop.

## Failure behavior

WikiBricks is fail-open for Omnigent:

- Retrieval errors produce an empty memory packet.
- Capture errors remain recoverable from Omnigent's conversation database.
- A locked database triggers bounded retries and then skips the current
  background attempt.
- A failed migration disables memory for that Omnigent process and records a
  diagnostic. It does not prevent a session from starting.
- Lakebase authentication, network, and job failures do not affect local
  memory.
- Corrupt manifests and hash mismatches are rejected before local page state
  changes.

The database directory will be created with user-only permissions where the
operating system supports them. WikiBricks will make no network call unless a
Lakebase target is configured.

## PostgreSQL migration

Native PostgreSQL will stop being a supported active local backend. Existing
users can install the `postgres-migration` extra and run one migration command:

```bash
wikibricks migrate postgres --source postgresql:///wikibricks
```

The importer will copy immutable IDs, versions, hashes, links, cursors,
outbox records, archive acknowledgements, and curation state into a new
SQLite file. It will not overwrite an existing non-empty SQLite database
without an explicit destination.

Validation will compare entity counts, current page hashes, session event
hashes, unresolved conflicts, and pending outbox IDs. The command will print
the backup location and validation report. PostgreSQL and its client libraries
will remain optional dependencies used only by this importer and Lakebase.

## Packaging and installation

The base WikiBricks package will depend only on MCP and YAML packages beyond
the Python standard library. `psycopg` and the Databricks SDK will move to
optional extras.

Omnigent will install a compatible WikiBricks version as a normal dependency,
so its local memory path requires no second installation command. On first
use, WikiBricks creates the SQLite file and migrations automatically. A local
configuration switch may disable memory, but local memory is enabled by
default and remote sync is disabled by default.

Standalone users can install WikiBricks and register `wikibricks-mcp` with
their client. All installations resolve the same default database path, even
when the executables live in different Python environments.

The Claude-specific WikiBricks plugin and its separate recorder buffer will be
removed from the primary documentation. Compatibility shims may remain for
one release if tests show that removal would break existing installations.

## Test and validation gates

Tests will cover behavior rather than SQL implementation details. Existing
PostgreSQL tests will be replaced where the behavior moves to SQLite instead
of duplicated.

### WikiBricks gates

- Fresh SQLite initialization and migration are idempotent.
- Page writes, event versioning, graph links, FTS retrieval, curation apply,
  backup, restore, and archive outbox behavior match the existing contract.
- Two processes can read and write the same WAL database without duplicate
  versions or partial transactions.
- One background lease owner runs maintenance while another process reports
  busy.
- PostgreSQL migration preserves counts, IDs, hashes, and pending sync state.
- The base package imports and runs offline without `psycopg`, the Databricks
  SDK, PostgreSQL binaries, or network access.
- The five MCP tools work from a clean installed wheel.

### Omnigent gates

- A committed Omnigent event reaches WikiBricks asynchronously.
- A restart imports an event missed while WikiBricks was unavailable.
- Memory failure never prevents prompt dispatch or event persistence.
- Codex-created memory is injected into a later Claude Code session.
- Claude Code-created memory is injected into a later Kimi session.
- Switching runners inside one conversation does not duplicate the session.
- Every runner receives the same MCP tool names and memory packet rendering.
- The current session is excluded from its own retrieval packet.

### End-to-end gate

The release acceptance test will run two synthetic Omnigent runner identities
against a clean temporary home directory:

1. Runner A records a decision and writes a source-linked wiki page.
2. A separate Omnigent conversation starts with Runner B.
3. Runner B receives the page summary and path before prompt dispatch without
   a memory command or explicit WikiBricks request.
4. Runner B reads or updates the same page through Omnigent's MCP relay.
5. The final database contains one page history, two Omnigent sessions, and
   no duplicate event versions.
6. The same flow passes with network access disabled and no Databricks
   credentials.

The complete gate also runs Ruff, the focused suites in both repositories,
the WikiBricks full and offline suites, wheel builds, and clean-install smoke
tests. Remote bundle validation is required only if remote resources change.

## Documentation and repository metadata

The README will lead with the shared-memory promise and the Omnigent data
flow. It will separate the zero-touch Omnigent path from the standalone MCP
path, state that SQLite is local, and describe Lakebase as optional. It will
remove PostgreSQL setup and the Claude plugin from the main installation
journey.

After the implementation passes the end-to-end gate, the GitHub About text
will be updated to:

> Shared local memory for Codex, Claude Code, Kimi, and other agent harnesses.
> Zero-touch in Omnigent, standard MCP everywhere else, with optional
> Lakebase curation.

Repository topics will include `agent-memory`, `mcp`, `omnigent`, `sqlite`,
and `local-first`.

## Delivery phases

1. Replace the active local PostgreSQL backend with SQLite and prove storage
   parity.
2. Add bounded context retrieval and the PostgreSQL migration importer.
3. Add the native Omnigent bridge and shared MCP relay tools.
4. Run the cross-runner end-to-end gate and failure tests.
5. Rewrite installation documentation, update repository metadata, and remove
   obsolete primary-path code.

Each phase must pass its focused tests, the overnight-development pre-commit
gate, and a fresh review before commit. Public documentation will describe a
feature only after its acceptance test passes.
