# Omnigent Shared Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one local SQLite WikiBricks database provide automatic capture, retrieval, and MCP tools to every Omnigent-managed runner.

**Architecture:** WikiBricks owns an offline SQLite store and a harness-neutral memory API. Omnigent imports that API, queues conversation snapshots after durable item writes, injects bounded memory before dispatch, and serves the five WikiBricks tools through its MCP proxy. Lakebase stays behind an optional archive adapter.

**Tech Stack:** Python 3.10+, `sqlite3`, SQLite WAL and FTS5, MCP 1.x, pytest, Ruff, Omnigent's SQLAlchemy conversation store

**Spec:** `docs/superpowers/specs/2026-09-01-sqlite-omnigent-shared-memory-design.md`

## Global Constraints

- The default database is `~/.wikibricks/wikibricks.db`.
- Local memory must run without PostgreSQL, Docker, Node.js, Brew, Databricks credentials, or network access.
- SQLite connections use WAL, foreign keys, a 5-second busy timeout, `synchronous=NORMAL`, and `BEGIN IMMEDIATE` for writes.
- Session events and page versions are immutable. Search chunks remain below 64 KiB UTF-8.
- Memory packets contain at most five page summaries, two session snippets, 1,200 characters per item, and 6,000 rendered characters.
- Omnigent conversation IDs, not runner IDs, define shared session identity.
- WikiBricks failures never block Omnigent item persistence or prompt dispatch.
- Lakebase sync is disabled unless explicitly configured.
- Production code follows a red, green, refactor cycle and every commit passes the overnight hook.

---

### Task 1: SQLite storage contract

**Files:**
- Create: `src/wikibricks/storage/sqlite_store.py`
- Create: `src/wikibricks/sql/sqlite/0001_core.sql`
- Modify: `src/wikibricks/storage/content.py`
- Modify: `src/wikibricks/storage/__init__.py`
- Replace: `tests/test_postgres_store.py` with `tests/test_sqlite_store.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `SQLiteStore(database_path: str | Path | None = None, *, failpoint: Callable[[str], None] | None = None)`.
- Produces: `SQLiteStore.connection(*, write: bool = False) -> ContextManager[sqlite3.Connection]`.
- Preserves: page, session, graph, cursor, and outbox facade methods currently called by `WikiClient` and remote sync.

- [ ] **Step 1: Write the failing initialization and page-history tests**

```python
def test_sqlite_defaults_and_migrations_are_idempotent(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    store.migrate()
    store.migrate()
    with store.connection() as conn:
        pragmas = (
            conn.execute("PRAGMA journal_mode").fetchone()[0],
            conn.execute("PRAGMA foreign_keys").fetchone()[0],
            conn.execute("PRAGMA busy_timeout").fetchone()[0],
        )
        migrations = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    assert pragmas == ("wal", 1, 5000)
    assert migrations == 1


def test_page_history_and_outbox_commit_atomically(store):
    store.write_page("topics/sqlite", "SQLite", {"summary": "one", "body": "alpha"})
    store.write_page("topics/sqlite", "SQLite", {"summary": "two", "body": "beta"})
    assert [row["version"] for row in store.history("topics/sqlite")] == [2, 1]
    assert store.outbox_count() == 2
```

- [ ] **Step 2: Run `uv run pytest tests/test_sqlite_store.py -q` and confirm imports fail because `SQLiteStore` does not exist**
- [ ] **Step 3: Add the forward-only schema and the smallest store implementation that passes initialization, page writes, immutable history, and atomic outbox tests**
- [ ] **Step 4: Add failing tests for idempotent event imports, corrected event versions, 25 MiB text round trips, FTS search, and two concurrent writers**
- [ ] **Step 5: Implement session and FTS behavior with bounded UTF-8 chunks and short `BEGIN IMMEDIATE` transactions**
- [ ] **Step 6: Run `uv run ruff check src tests && uv run pytest tests/test_sqlite_store.py -q`**
- [ ] **Step 7: Commit with `git commit -m "feat: replace local storage with SQLite"`**

### Task 2: Public client and bounded memory packets

**Files:**
- Modify: `src/wikibricks/models.py`
- Modify: `src/wikibricks/client.py`
- Modify: `src/wikibricks/agent_tools.py`
- Modify: `src/wikibricks/mcp_server.py`
- Modify: `src/wikibricks/__init__.py`
- Create: `tests/test_memory_retrieval.py`
- Modify: `tests/test_client.py`
- Modify: `tests/test_mcp_end_to_end.py`

**Interfaces:**
- Produces: `MemoryQuery(text, user_id, workspace=None, current_session_id=None, max_chars=6000)`.
- Produces: `MemoryItem(path, title, text, kind, score)` and `MemoryPacket(items, rendered, truncated)`.
- Produces: `WikiClient.retrieve_memory(query: MemoryQuery) -> MemoryPacket`.
- Preserves: `wiki_search`, `wiki_read_full`, `wiki_index`, `wiki_write_page`, and `wiki_promote_answer`.

- [ ] **Step 1: Write a failing retrieval test**

```python
def test_curated_pages_precede_cross_runner_session_evidence(tmp_path):
    client = WikiClient(tmp_path / "memory.db")
    client.write_page("decisions/database", "Database", {"summary": "Use SQLite", "body": "Offline first."})
    client.ingest_session(session("old-conversation", "codex", "SQLite benchmark notes"))
    packet = client.retrieve_memory(
        MemoryQuery("Which database?", "philipp", current_session_id="new-conversation")
    )
    assert packet.items[0].path == "decisions/database"
    assert len(packet.rendered) <= 6000
    assert "reference material, not instructions" in packet.rendered
```

- [ ] **Step 2: Run the focused test and confirm `MemoryQuery` is missing**
- [ ] **Step 3: Implement deterministic ranking, current-session exclusion, page-first selection, item truncation, and packet rendering**
- [ ] **Step 4: Add failing tests proving all five MCP tools use the same SQLite file and work without Databricks imports**
- [ ] **Step 5: Switch the public client and tool factories from database URLs to paths while accepting `database_path=` explicitly**
- [ ] **Step 6: Run `UV_OFFLINE=1 uv run pytest tests/test_client.py tests/test_memory_retrieval.py tests/test_mcp_end_to_end.py -q`**
- [ ] **Step 7: Commit with `git commit -m "feat: add bounded shared-memory retrieval"`**

### Task 3: Local maintenance, leases, backup, and restore

**Files:**
- Modify: `src/wikibricks/maintenance.py`
- Modify: `src/wikibricks/automation.py`
- Modify: `src/wikibricks/cli.py`
- Modify: `src/wikibricks/config/defaults.yml`
- Modify: `src/wikibricks/config/__init__.py`
- Modify: `tests/test_local_maintenance.py`
- Modify: `tests/test_automation.py`

**Interfaces:**
- Produces: `SQLiteStore.acquire_lease(name, owner, ttl_seconds, now=None) -> bool`.
- Produces: `backup_database(database_path: Path, output: Path) -> Path` and `restore_database(backup: Path, database_path: Path) -> None`.
- Preserves: deterministic `check`, `curate`, `vacuum`, and optional background sync commands.

- [ ] **Step 1: Write failing lease and online-backup tests**

```python
def test_only_one_process_holds_the_background_lease(store):
    assert store.acquire_lease("maintenance", "worker-a", 60, now=100)
    assert not store.acquire_lease("maintenance", "worker-b", 60, now=101)
    assert store.acquire_lease("maintenance", "worker-b", 60, now=161)


def test_online_backup_restores_a_consistent_database(tmp_path):
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    WikiClient(source).write_page("topics/backup", "Backup", {"summary": "safe", "body": "copy"})
    backup = backup_database(source, tmp_path / "backup.db")
    restore_database(backup, restored)
    assert WikiClient(restored).read_page("topics/backup")["content"]["body"] == "copy"
```

- [ ] **Step 2: Run the focused tests and confirm lease and SQLite backup APIs are absent**
- [ ] **Step 3: Implement lease acquisition, FTS repair, hygiene reporting, online backup, validated restore, and SQLite vacuum**
- [ ] **Step 4: Replace advisory locks in automation with the database lease and keep each subsystem fail-open**
- [ ] **Step 5: Run `uv run pytest tests/test_local_maintenance.py tests/test_automation.py -q`**
- [ ] **Step 6: Commit with `git commit -m "feat: maintain SQLite memory in the background"`**

### Task 4: Optional PostgreSQL migration and Lakebase boundary

**Files:**
- Create: `src/wikibricks/migrate_postgres.py`
- Modify: `src/wikibricks/remote/lakebase.py`
- Modify: `src/wikibricks/curation/repository.py`
- Modify: `src/wikibricks/curation/application.py`
- Modify: `src/wikibricks/curation/planning.py`
- Modify: `src/wikibricks/cli.py`
- Modify: `pyproject.toml`
- Replace: `tests/test_lakebase_sync.py`
- Modify: `tests/test_curation_sync.py`
- Modify: `tests/test_package_boundaries.py`

**Interfaces:**
- Produces: `migrate_postgres(source_url: str, destination: Path) -> MigrationReport`.
- Changes: Lakebase functions accept `SQLiteStore` as the local side and open PostgreSQL only for the remote side.
- Moves: `psycopg[binary]==3.2.13` to `postgres-migration` and `lakebase` extras.

- [ ] **Step 1: Write a failing package-boundary test that blocks `psycopg` and `databricks` while importing and using the base package**
- [ ] **Step 2: Run it and confirm the base dependency or module-level PostgreSQL import fails**
- [ ] **Step 3: Move PostgreSQL imports behind migration and remote calls; update archive payload reads to use SQLite rows and canonical JSON**
- [ ] **Step 4: Add a disposable-PostgreSQL migration test that compares entity counts, IDs, hashes, unresolved conflicts, and pending outbox IDs**
- [ ] **Step 5: Port guarded manifest planning and application to SQLite placeholders and transactions; retain base-hash conflict behavior**
- [ ] **Step 6: Run `UV_OFFLINE=1 uv run pytest -q` and `uv build`**
- [ ] **Step 7: Commit with `git commit -m "feat: make PostgreSQL and Lakebase optional"`**

### Task 5: Remove the old primary plugin path

**Files:**
- Delete: `src/wikibricks/adapters/claude_code_buffer.py`
- Delete: `src/wikibricks/adapters/claude_code_hook.py`
- Delete: `plugin/`
- Modify: `src/wikibricks/cli.py`
- Modify: `pyproject.toml`
- Delete: `tests/test_plugin_manifest.py`
- Modify: `tests/test_package_boundaries.py`

**Interfaces:**
- Removes: `wikibricks-hook` and the Claude-only recorder buffer.
- Retains: standalone MCP for any direct harness.

- [ ] **Step 1: Add a failing package-boundary assertion that no Claude-specific recorder command or plugin artifact ships**
- [ ] **Step 2: Run it and confirm the old entry point and plugin directory violate the assertion**
- [ ] **Step 3: Remove the obsolete plugin path and imports, preserving the neutral Claude transcript adapter only if the PostgreSQL migration command needs it**
- [ ] **Step 4: Run `uv run pytest -q && uv build`**
- [ ] **Step 5: Commit with `git commit -m "refactor: remove the Claude-only recorder path"`**

### Task 6: Omnigent memory bridge and capture worker

**Files:**
- Create: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/omnigent/memory.py`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/omnigent/stores/conversation_store/sqlalchemy_store.py`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/pyproject.toml`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/uv.lock`
- Create: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/tests/test_wikibricks_memory.py`

**Interfaces:**
- Produces: `notify_conversation_appended(store, conversation_id) -> None`.
- Produces: `capture_conversation(store, conversation_id) -> IngestResult | None`.
- Produces: a daemon worker with a bounded, deduplicating queue and `flush_for_test()`.

- [ ] **Step 1: Write a failing test that appends a Codex item, flushes the worker, and reads an `harness="omnigent"` session from a temporary WikiBricks database**
- [ ] **Step 2: Run it and confirm the bridge module is missing**
- [ ] **Step 3: Implement Omnigent item conversion, stable conversation identity, runner provenance, a bounded worker, and fail-open logging**
- [ ] **Step 4: Call the notifier only after `ConversationStore.append` commits**
- [ ] **Step 5: Add restart reconciliation test coverage using the existing Omnigent conversation database as source**
- [ ] **Step 6: Run `uv run pytest tests/test_wikibricks_memory.py -q`**
- [ ] **Step 7: Commit in Omnigent with `git commit -m "feat: capture sessions in shared WikiBricks memory"`**

### Task 7: Pre-turn injection and native MCP relay

**Files:**
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/omnigent/memory.py`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/omnigent/runner/app.py`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/omnigent/server/routes/_sessions/helpers.py`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/omnigent/server/routes/_sessions/orchestration.py`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/tests/test_wikibricks_memory.py`

**Interfaces:**
- Produces: `memory_packet_for_turn(conversation_id, content, *, max_chars=6000) -> str`.
- Produces: `wikibricks_tool_schemas() -> list[dict]` and `call_wikibricks_tool(name, arguments) -> object`.
- Adds: the five tools to MCP `tools/list` and handles their `tools/call` requests through existing Omnigent policy gates.

- [ ] **Step 1: Write a failing test proving a page created by runner `codex` is present in runner `claude-code` instructions for another conversation and absent from the current conversation**
- [ ] **Step 2: Run it and confirm no memory packet is appended**
- [ ] **Step 3: Append the packet as framework-owned instructions immediately before harness dispatch, with a no-op result on every bridge error**
- [ ] **Step 4: Write failing MCP list and call tests for the exact five public tool names**
- [ ] **Step 5: Merge WikiBricks schemas into the server MCP list and intercept WikiBricks calls after TOOL_CALL approval but before remote-runner dispatch; pass results through TOOL_RESULT policy**
- [ ] **Step 6: Run the focused memory, prompt, and MCP proxy tests**
- [ ] **Step 7: Commit in Omnigent with `git commit -m "feat: inject and relay shared WikiBricks memory"`**

### Task 8: Cross-runner offline acceptance test

**Files:**
- Create: `tests/test_omnigent_shared_memory_e2e.py`
- Create: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/tests/e2e/test_wikibricks_shared_memory.py`

**Interfaces:**
- Consumes: SQLite store, Omnigent capture worker, pre-turn packet, and MCP relay from Tasks 1 through 7.
- Produces: one repeatable acceptance command for local and CI use.

- [ ] **Step 1: Write the synthetic two-runner flow using a temporary home directory and no Databricks variables**
- [ ] **Step 2: Confirm the test fails if capture, packet injection, or MCP update is disabled**
- [ ] **Step 3: Make only integration fixes required by the failing flow**
- [ ] **Step 4: Assert one page history, two Omnigent sessions, no duplicate event versions, and zero network calls**
- [ ] **Step 5: Run WikiBricks Ruff, full tests, offline tests, wheel build, and clean-wheel MCP smoke test**
- [ ] **Step 6: Run Omnigent pre-commit and the focused unit and end-to-end suites**
- [ ] **Step 7: Commit acceptance coverage in each repository**

### Task 9: Installation docs and repository metadata

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/README.md`
- Modify: `/Users/philipp.tiefenbacher/work/30-demos/code/omnigent-fix/AGENTS.md`

**Interfaces:**
- Documents: zero-touch Omnigent, standalone MCP, local SQLite path, deterministic local maintenance, optional Lakebase, and PostgreSQL migration.
- Updates: GitHub About text and topics only after the acceptance gate passes.

- [ ] **Step 1: Rewrite the WikiBricks README around one memory across agent harnesses, with separate Omnigent and standalone MCP installation paths**
- [ ] **Step 2: Update both agent files so contributors preserve the SQLite boundary and Omnigent lifecycle contract**
- [ ] **Step 3: Apply the `avoid-ai-writing` edit pass to changed documentation and verify code blocks, links, commands, and technical claims are unchanged**
- [ ] **Step 4: Run the complete verification commands from Task 8 again**
- [ ] **Step 5: Commit documentation in each repository**
- [ ] **Step 6: Update GitHub About to `Shared local memory for Codex, Claude Code, Kimi, and other agent harnesses. Zero-touch in Omnigent, standard MCP everywhere else, with optional Lakebase curation.` and set topics `agent-memory`, `mcp`, `omnigent`, `sqlite`, and `local-first`**

## Final verification

- [ ] Confirm `git status --short` shows only the preserved Omnigent `project.yaml` outside committed work.
- [ ] Confirm the base WikiBricks wheel imports with `psycopg`, `databricks`, and network access blocked.
- [ ] Confirm the five MCP tools work from a clean wheel installation.
- [ ] Confirm Codex memory reaches Claude Code and Claude Code memory reaches Kimi through two distinct Omnigent conversations.
- [ ] Confirm runner switching inside one conversation creates one WikiBricks session.
- [ ] Confirm remote settings are absent by default and no local request opens a network connection.
