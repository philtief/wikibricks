# Local-only modular architecture implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy Databricks wiki, make local PostgreSQL the only runtime, split the two oversized core modules, and reduce the test suite to contract and integration tests.

**Architecture:** `WikiClient` becomes a local PostgreSQL client with no dispatch to a SQL Warehouse implementation. `PostgresStore` remains the transaction facade and delegates to focused storage repositories. Curation separates its pure signed protocol from PostgreSQL persistence, planning, and application. Markdown, YAML, and JSON resources define agent guidance, operator defaults, MCP tools, and interchange schemas.

**Tech Stack:** Python 3.10+, PostgreSQL 16/17, Psycopg 3, MCP, PyYAML, JSON Schema development validation, pytest, Ruff, uv, and the overnight-development pre-commit hook.

**Spec:** `docs/superpowers/specs/2026-08-31-local-only-modular-architecture-design.md`

## Global constraints

- Local reads, writes, search, imports, maintenance, and MCP calls must work without a network or Databricks SDK.
- Lakebase is contacted only by `wikibricks sync lakebase`.
- Preserve the current PostgreSQL schema, immutable versions, outbox atomicity, hashes, curation conflict rules, and 64 KiB UTF-8 search chunk ceiling.
- Keep `pg_trgm` as the only required PostgreSQL extension.
- Keep LLM calls out of `src/wikibricks/`.
- Preserve the five MCP tool names and their runtime-neutral descriptions.
- Preserve `WikiClient()`, `PostgresStore`, local CLI commands, JSONL and Omnigent imports, the Claude Code local hook, Karpathy Markdown import/export, and the curation API.
- Remove SQL Warehouse compatibility, Unity Catalog and Vector Search helpers, remote recorder code, Databricks Apps, bundle resources, notebooks, and their tests.
- Do not change remote resources, deploy, push, tag, publish, or modify `project.yaml`.
- Use an existing green characterization test as the red-green-refactor starting point for internal-only file extraction. Write and observe a failing test before every new behavior.

---

Tasks 1 through 3 form one atomic breaking change. Run their focused checks as
written, but do not commit an intermediate state. The old remote recorder
depends on the SQL Warehouse constructor removed in Task 1. Task 3 deletes
that recorder and runs the first complete overnight gate.

### Task 1: cut the public API to local PostgreSQL

**Files:**
- Rewrite: `tests/test_client.py`
- Modify: `tests/test_package_boundaries.py`
- Rewrite: `src/wikibricks/client.py`
- Modify: `src/wikibricks/__init__.py`
- Delete: `src/wikibricks/local_client.py`

**Interfaces:**
- Produces: `WikiClient(database_url: str | None = None, *, migrate: bool = True)`
- Preserves: page reads and writes, search, sessions, graph links, promotion, index materialization, and recent-session lookup
- Removes: `warehouse_id`, `workspace_client`, exported Databricks constants, and SQL helper functions

- [ ] **Step 1: Replace the legacy client tests with a failing local API contract**

```python
def test_wiki_client_is_the_local_client(postgres_url: str):
    client = WikiClient(postgres_url)
    client.write_page("topics/local", "Local", {"summary": "local", "body": "postgres"})
    assert isinstance(client, WikiClient)
    assert client.read_page("topics/local")["content"]["body"] == "postgres"


def test_wiki_client_rejects_removed_sql_warehouse_arguments():
    with pytest.raises(TypeError):
        WikiClient(warehouse_id="warehouse", workspace_client=object())
```

Update the package boundary assertion to require this exact public list:

```python
assert wikibricks.__all__ == ["WikiClient", "PostgresStore", "make_agent_tools"]
```

- [ ] **Step 2: Run the contract tests and confirm the old constructor or exports fail them**

Run: `uv run pytest tests/test_client.py tests/test_package_boundaries.py -q`

Expected: FAIL because the old `WikiClient` returns `LocalWikiClient`, accepts SQL Warehouse arguments, and exports Databricks constants.

- [ ] **Step 3: Move the retained local client implementation into `client.py`**

Use the existing `LocalWikiClient` body as the implementation and rename the class:

```python
class WikiClient:
    def __init__(self, database_url: str | None = None, *, migrate: bool = True) -> None:
        self.store = PostgresStore(database_url)
        self.database_url = self.store.database_url
        if migrate:
            self.store.migrate()
```

Keep all local methods currently implemented by `LocalWikiClient`. Remove the `__new__` selector, Databricks type-checking imports, SQL statement execution, retries, and SQL Warehouse methods.

Set the package exports to:

```python
from wikibricks.agent_tools import make_agent_tools
from wikibricks.client import WikiClient
from wikibricks.postgres_store import PostgresStore

__all__ = ["WikiClient", "PostgresStore", "make_agent_tools"]
```

- [ ] **Step 4: Update internal local imports and run the focused tests**

Replace `from wikibricks.local_client import LocalWikiClient` with `from wikibricks.client import WikiClient` in retained code. Run:

```bash
uv run pytest tests/test_client.py tests/test_agent_tools.py tests/test_local_maintenance.py tests/test_mcp_end_to_end.py tests/test_package_boundaries.py -q
```

Expected: PASS.

---

### Task 2: delete the old Databricks product and its test surface

**Files:**
- Delete: `app/`
- Delete: `notebooks/`
- Delete: `resources/`
- Delete: `scripts/`
- Delete: `src/wikibricks_graph/`
- Delete: `src/wikibricks/ops.py`
- Delete: `src/wikibricks/curate_logic.py`
- Delete: `src/wikibricks/graph_logic.py`
- Delete: `src/wikibricks/health.py`
- Delete: `src/wikibricks/promote_logic.py`
- Delete: `src/wikibricks/segregate_logic.py`
- Delete: `src/wikibricks/tag_logic.py`
- Delete: `src/wikibricks/title_repair.py`
- Delete: `src/wikibricks/topic_clustering.py`
- Delete: `src/wikibricks/seeds/`
- Delete: `databricks.yml`
- Delete: `databricks.override.example.yml`
- Modify: `.overnight-dev.json`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

Delete these test files with the removed code:

```text
tests/test_app.py
tests/test_auto_title.py
tests/test_build_hotpot_seed.py
tests/test_client_citation_rerank.py
tests/test_client_pagerank_default.py
tests/test_curate_logic.py
tests/test_deploy_notebook.py
tests/test_eval_metrics.py
tests/test_graph_logic.py
tests/test_health.py
tests/test_job_dag.py
tests/test_job_notifications.py
tests/test_promote_edges_notebook.py
tests/test_promote_logic.py
tests/test_promote_notebook.py
tests/test_promote_topics_notebook.py
tests/test_purge_noise.py
tests/test_recent_by_cwd.py
tests/test_seed_data.py
tests/test_segregate_logic.py
tests/test_tag_logic.py
tests/test_title_repair.py
tests/test_topic_clustering.py
tests/test_topic_clustering_community.py
tests/test_vocabulary.py
tests/test_wiki_ops.py
```

**Interfaces:**
- Consumes: the local-only API from Task 1
- Produces: a repository with no active SQL Warehouse, Unity Catalog, Vector Search, Databricks App, or job code

- [ ] **Step 1: Record the green local characterization set**

Run:

```bash
uv run pytest tests/test_client.py tests/test_postgres_store.py tests/test_curation_sync.py tests/test_local_ingest.py tests/test_local_maintenance.py tests/test_mcp_end_to_end.py -q
```

Expected: PASS. This is the green state for a deletion-only refactor.

- [ ] **Step 2: Delete the listed legacy source, assets, and tests**

Use patch-based deletions. Do not remove `src/wikibricks_databricks/lakebase_sync.py`, PostgreSQL migrations, local curation, local adapters, Karpathy import/export, plugin files, or validation documents.

- [ ] **Step 3: Remove obsolete dependencies and entry points**

Keep the base dependencies focused:

```toml
dependencies = [
    "mcp>=1.0,<2",
    "psycopg[binary]>=3.2",
]

[project.optional-dependencies]
dev = [
    "jsonschema>=4.0",
    "pytest>=8.0",
    "ruff>=0.15",
]
lakebase = [
    "databricks-sdk>=0.44.0",
]
```

Remove the `wiki-target` script and the `recorder`, `graph`, and `databricks` extras. Retain `wikibricks`, `wiki-init`, `wikibricks-mcp`, and `wikibricks-recorder-hook`; Task 3 changes the hook target.

Change the overnight lint command to `uv run ruff check src tests` because `scripts/` no longer exists. Run `uv lock` after editing `pyproject.toml`.

- [ ] **Step 4: Run the retained local suite and import scan**

```bash
rg -n "wikibricks\.ops|LocalWikiClient|warehouse_id|WorkspaceClient|Vector Search|Unity Catalog" src tests
uv run ruff check src tests
uv run pytest tests/test_client.py tests/test_agent_tools.py tests/test_postgres_store.py tests/test_curation_sync.py tests/test_local_ingest.py tests/test_local_maintenance.py tests/test_mcp_end_to_end.py tests/test_lakebase_sync.py tests/test_package_boundaries.py -q
```

Expected: `rg` matches only the recorder files scheduled for Task 3; Ruff and
the retained local tests pass.

- [ ] **Step 5: Keep the product cut unstaged until the recorder is removed**

Run `git status --short` and confirm the listed removals do not include
`project.yaml`. Continue directly to Task 3.

---

### Task 3: consolidate the Claude adapter and remove the remote recorder

**Files:**
- Create: `src/wikibricks/adapters/claude_code_buffer.py`
- Create: `src/wikibricks/adapters/claude_code_hook.py`
- Modify: `src/wikibricks/adapters/__init__.py`
- Modify: `tests/test_local_ingest.py`
- Rewrite: `tests/test_plugin_manifest.py`
- Modify: `pyproject.toml`
- Modify: `plugin/bin/launch.sh`
- Delete: `src/wikibricks_recorder/`

Delete these obsolete recorder tests:

```text
tests/test_omnigent_sync.py
tests/test_recorder_auto_summary.py
tests/test_recorder_auto_tag.py
tests/test_recorder_citation_logging.py
tests/test_recorder_citations.py
tests/test_recorder_config.py
tests/test_recorder_context_injection.py
tests/test_recorder_envelope.py
tests/test_recorder_hooks.py
tests/test_recorder_init_cli.py
tests/test_recorder_page_builder.py
tests/test_recorder_session_prelude.py
tests/test_recorder_target_cli.py
tests/test_recorder_wiki_mcp.py
```

**Interfaces:**
- Produces: `wikibricks.adapters.claude_code_hook:main`
- Preserves: the `wikibricks-recorder-hook` executable and Claude plugin hook events
- Removes: Databricks recorder configuration, remote MCP duplication, model calls, target switching, and recorder-only optional dependencies

- [ ] **Step 1: Write failing tests for the consolidated hook**

Update imports in `test_local_ingest.py` and add skip cases:

```python
from wikibricks.adapters import claude_code_buffer, claude_code_hook


@pytest.mark.parametrize("cwd", ["/tmp", "/private/tmp/job", "/var/tmp/tool"])
def test_claude_hook_skips_temporary_sessions(cwd, monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_RECORDER_MIN_EVENTS", "0")
    state = {"session_id": "utility", "cwd": cwd, "events": [{"kind": "prompt", "prompt": "x"}]}
    assert claude_code_hook.should_skip(state) is True


def test_claude_hook_skips_single_system_prompt(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_RECORDER_MIN_EVENTS", "1")
    state = {
        "session_id": "utility",
        "cwd": "/Users/u/project",
        "first_prompt": "You are summarizing a transcript",
        "events": [{"kind": "prompt", "prompt": "You are summarizing a transcript"}],
    }
    assert claude_code_hook.should_skip(state) is True


def test_claude_buffer_recovers_from_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_RECORDER_DIR", str(tmp_path))
    path = claude_code_buffer.state_path("broken")
    path.parent.mkdir(parents=True)
    path.write_text("not-json")
    assert claude_code_buffer.load("broken")["events"] == []
```

Add a `pyproject.toml` assertion to `test_plugin_manifest.py`:

```python
assert scripts["wikibricks-recorder-hook"] == "wikibricks.adapters.claude_code_hook:main"
```

- [ ] **Step 2: Run the new tests and confirm imports or script targets fail**

Run: `uv run pytest tests/test_local_ingest.py tests/test_plugin_manifest.py -q`

Expected: FAIL because the consolidated modules and entry-point target do not exist.

- [ ] **Step 3: Move the durable buffer and implement the small local hook**

Move the file-based state functions from `wikibricks_recorder.session` into
`claude_code_buffer.py`. Implement this public decision function in
`claude_code_hook.py`:

```python
def should_skip(state: dict[str, Any]) -> bool:
    cwd = str(state.get("cwd") or "").rstrip("/")
    if cwd in {"/tmp", "/private/tmp", "/var/tmp"}:
        return True
    if cwd.startswith(("/tmp/", "/private/tmp/", "/private/var/folders/", "/var/tmp/", "/var/folders/")):
        return True
    try:
        minimum = int(os.environ.get("WIKIBRICKS_RECORDER_MIN_EVENTS", "2"))
    except ValueError:
        minimum = 2
    events = state.get("events") or []
    if len(events) < minimum:
        return True
    prompts = [event for event in events if event.get("kind") == "prompt"]
    first = str(state.get("first_prompt") or "").strip().lower()
    utility_prefixes = ("you are ", "summarize ", "read the transcript", "extract ")
    return len(prompts) <= 1 and first.startswith(utility_prefixes)
```

Keep the five event handlers from the existing local hook. `Stop` and
`SessionEnd` call
`WikiClient().ingest_session(state_to_session(state, user_id=_user_id()))` only
when `should_skip` is false.

- [ ] **Step 4: Point the executable and plugin installer at the base package**

```toml
wikibricks-recorder-hook = "wikibricks.adapters.claude_code_hook:main"
```

In `plugin/bin/launch.sh`, install `wikibricks @ git+${GIT_URL}@${REF}` without
the removed `[recorder]` extra.

- [ ] **Step 5: Replace 16 plugin structure tests with five contract tests**

Cover: manifest identity/version, all five hook events, hook command and
timeouts, MCP command, and executable Bash launcher. Use parametrization for
the five events rather than one test per manifest field.

- [ ] **Step 6: Delete the old recorder package and tests, then verify**

```bash
uv lock
uv run pytest tests/test_local_ingest.py tests/test_plugin_manifest.py tests/test_session_contract.py -q
uv run ruff check src tests
```

Expected: PASS.

- [ ] **Step 7: Run the complete gate and commit the atomic product cut**

```bash
uv run ruff check src tests
uv run pytest -q
git add .overnight-dev.json pyproject.toml uv.lock plugin src tests
git add -u
git commit -m "refactor: remove legacy Databricks runtime"
```

The overnight hook must pass without `--no-verify`.

---

### Task 4: add the YAML configuration contract

**Files:**
- Create: `src/wikibricks/config/__init__.py`
- Create: `src/wikibricks/config/defaults.yml`
- Create: `tests/test_config.py`
- Modify: `src/wikibricks/postgres_store.py`
- Modify: `src/wikibricks/cli.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `WikiBricksConfig` and `load_config(path=None, *, home=None, environ=None)`
- Precedence: packaged defaults, `~/.wikibricks/config.yml`, `WIKIBRICKS_CONFIG`, environment variables, explicit function or CLI arguments

- [ ] **Step 1: Write failing configuration tests**

```python
def test_config_precedence(tmp_path: Path):
    home = tmp_path / "home"
    user = home / ".wikibricks" / "config.yml"
    user.parent.mkdir(parents=True)
    user.write_text("search:\n  default_results: 7\n")
    override = tmp_path / "override.yml"
    override.write_text("sync:\n  batch_size: 25\n")

    config = load_config(
        home=home,
        environ={
            "WIKIBRICKS_CONFIG": str(override),
            "WIKIBRICKS_DATABASE_URL": "postgresql:///explicit",
        },
    )

    assert config.database_url == "postgresql:///explicit"
    assert config.search_default_results == 7
    assert config.sync_batch_size == 25
    assert config.sync_apply_policy == "safe"


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("unknown: true\n", "unknown"),
        ("version: 2\n", "version"),
        ("search:\n  default_results: 0\n", "search.default_results"),
        ("sync:\n  apply_policy: unsafe\n", "sync.apply_policy"),
    ],
)
def test_config_rejects_invalid_values(tmp_path, yaml_text, message):
    path = tmp_path / "config.yml"
    path.write_text(yaml_text)
    with pytest.raises(ValueError, match=message):
        load_config(path)
```

- [ ] **Step 2: Run tests and confirm the loader is missing**

Run: `uv run pytest tests/test_config.py -q`

Expected: FAIL during import of `wikibricks.config`.

- [ ] **Step 3: Add PyYAML and implement the typed flat configuration**

```python
@dataclass(frozen=True, slots=True)
class WikiBricksConfig:
    database_url: str
    search_default_results: int
    search_maximum_results: int
    prune_archived_sessions_after_days: int | None
    sync_batch_size: int
    sync_apply_policy: Literal["safe", "all"]
```

Use `yaml.safe_load`, recursive dictionary merge, an exact allowed-key map,
and path-specific validation errors. Add `PyYAML>=6.0` to base dependencies.

- [ ] **Step 4: Connect defaults without changing explicit arguments**

`PostgresStore(None)` reads `load_config().database_url`. Explicit constructor
values still win. `build_parser(config)` uses YAML values for search count,
session retention, sync batch size, and apply policy. `main()` loads the config
before building the parser.

- [ ] **Step 5: Verify configuration and local behavior**

```bash
uv lock
uv run pytest tests/test_config.py tests/test_client.py tests/test_local_maintenance.py tests/test_curation_sync.py -q
uv run ruff check src tests
```

Expected: PASS.

- [ ] **Step 6: Commit configuration**

```bash
git add pyproject.toml uv.lock src/wikibricks/config src/wikibricks/postgres_store.py src/wikibricks/cli.py tests/test_config.py
git commit -m "feat: add readable local configuration"
```

---

### Task 5: move MCP guidance and contracts into Markdown and JSON

**Files:**
- Create: `src/wikibricks/resources/agent-instructions.md`
- Create: `src/wikibricks/resources/mcp-tools.json`
- Create: `src/wikibricks/resources/__init__.py`
- Create: `src/wikibricks/resources/schemas/session-record.schema.json`
- Create: `src/wikibricks/resources/schemas/curation-manifest-v1.schema.json`
- Create: `tests/test_resources.py`
- Modify: `src/wikibricks/mcp_server.py`
- Delete: `src/wikibricks/WIKIBRICKS.MD`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `get_server_instructions()` and `get_tool_schemas()` loaded from packaged resources
- Preserves: exactly five MCP tools and curation/session payload behavior

- [ ] **Step 1: Write failing resource behavior tests**

```python
def test_mcp_resources_drive_the_server_contract():
    instructions = get_server_instructions()
    schemas = get_tool_schemas()
    assert "maintained knowledge layer" in instructions
    assert [item["name"] for item in schemas] == [
        "wiki_search",
        "wiki_read_full",
        "wiki_index",
        "wiki_write_page",
        "wiki_promote_answer",
    ]


def test_interchange_schemas_accept_real_payloads(postgres_url: str):
    session = {"schema_version": 1, "session": _session_record().to_dict()}
    manifest = _curation_manifest(PostgresStore(postgres_url))
    Draft202012Validator(_schema("session-record.schema.json")).validate(session)
    Draft202012Validator(_schema("curation-manifest-v1.schema.json")).validate(manifest)
```

Add one invalid session and one invalid manifest assertion using
`jsonschema.ValidationError`.

- [ ] **Step 2: Run tests and confirm resource files are missing**

Run: `uv run pytest tests/test_resources.py -q`

Expected: FAIL because the resource paths and schemas do not exist.

- [ ] **Step 3: Move instructions and tool declarations**

Move the current `WIKIBRICKS.MD` content without semantic changes. Move the
list returned by `get_tool_schemas()` into JSON. Load both with
`importlib.resources.files("wikibricks.resources")`.

Validate that the JSON top level is an array and that its ordered names match
an `_TOOL_NAMES` tuple used by the dispatch table. Raise `RuntimeError` with
the resource name when parsing or validation fails. Reading schemas must not
construct `WikiClient` or connect to PostgreSQL.

- [ ] **Step 4: Define the Draft 2020-12 schemas**

The session schema must constrain `schema_version` to `1`, require the session
identity fields, permit only the six event kinds, and require event content to
be a string. The curation schema must require the current manifest and patch
fields, use UUID and 64-character lowercase hexadecimal formats, and enumerate
the five operations and three risk classes.

- [ ] **Step 5: Verify resources are packaged and executable**

```bash
uv run pytest tests/test_resources.py tests/test_session_contract.py tests/test_curation_sync.py tests/test_mcp_end_to_end.py -q
uv build
unzip -l dist/wikibricks-*.whl | rg "defaults.yml|agent-instructions.md|mcp-tools.json|session-record.schema.json|curation-manifest-v1.schema.json"
```

Expected: tests pass and all five resources appear in the wheel.

- [ ] **Step 6: Commit declarative resources**

```bash
git add pyproject.toml uv.lock src/wikibricks/resources src/wikibricks/mcp_server.py tests/test_resources.py
git add -u src/wikibricks/WIKIBRICKS.MD
git commit -m "feat: make memory contracts editable resources"
```

---

### Task 6: split PostgreSQL storage into focused repositories

**Files:**
- Create: `src/wikibricks/storage/__init__.py`
- Create: `src/wikibricks/storage/store.py`
- Create: `src/wikibricks/storage/content.py`
- Create: `src/wikibricks/storage/pages.py`
- Create: `src/wikibricks/storage/sessions.py`
- Create: `src/wikibricks/storage/search.py`
- Create: `src/wikibricks/storage/outbox.py`
- Create: `src/wikibricks/storage/graph.py`
- Rewrite: `src/wikibricks/postgres_store.py`
- Modify: `src/wikibricks/maintenance.py`
- Modify: `tests/test_postgres_store.py`

**Interfaces:**
- Produces: `PostgresStore` with its current public methods and repository attributes `pages`, `sessions`, `search_index`, `outbox`, and `graph`
- Produces: `PageRepository.write_in_connection(conn: Connection, path: str, title: str, content_json: str | dict[str, Any], **kwargs: Any) -> tuple[str, UUID | None]`
- Preserves: `IngestResult`, `MAX_SEARCH_CHUNK_BYTES`, `page_content_hash`, and `iter_search_chunks` compatibility exports

- [ ] **Step 1: Run the storage characterization tests before extraction**

Run:

```bash
uv run pytest tests/test_postgres_store.py tests/test_local_maintenance.py tests/test_local_ingest.py tests/test_curation_sync.py tests/test_lakebase_sync.py -q
```

Expected: PASS. These tests are the green state for the internal refactor.

- [ ] **Step 2: Extract pure content functions**

Move `MAX_SEARCH_CHUNK_BYTES`, `_canonical_hash`, `page_content_hash`,
`iter_search_chunks`, and search-chunk insertion into `storage/content.py`.
Expose this transaction-aware function:

```python
def insert_search_chunks(
    conn: Connection,
    table: Literal["page_search_chunks", "session_search_chunks"],
    version_id: UUID,
    text: str,
) -> None:
    if table not in {"page_search_chunks", "session_search_chunks"}:
        raise ValueError(f"unsupported search chunk table: {table}")
    statement = (
        f"INSERT INTO {table} "
        "(version_id, chunk_index, start_offset, end_offset, search_vector) "
        "VALUES (%s, %s, %s, %s, to_tsvector('simple', %s))"
    )
    for index, (start, end, chunk) in enumerate(iter_search_chunks(text)):
        conn.execute(statement, (version_id, index, start, end, chunk))
```

The literal table allowlist replaces dynamic arbitrary table names.

- [ ] **Step 3: Extract page, session, search, outbox, and graph repositories**

Each repository receives the store facade and owns the current method group:

```python
class PageRepository:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store
```

Move the current `write_page` body into `PageRepository.write` and the current
`_write_page_in_connection` body into `PageRepository.write_in_connection`.
Keep their current parameters and return values.

Method ownership:

```text
pages.py     write_page internals, current_page_state, read_page, list_pages, history
sessions.py  ingest_session, read_session_events, session_event_versions, IngestResult
search.py    page/session/archive search query
outbox.py    count, cursor, pending, batch assignment, acknowledgement
graph.py     operation log, source ingest, edge commit, graph neighbours
```

Use `TYPE_CHECKING` imports to avoid a runtime cycle back to `storage.store`.

- [ ] **Step 4: Assemble repositories and preserve explicit facade methods**

`storage/store.py` owns database URL resolution, `connection`, migrations,
`clear_all`, and failpoints. Construct repositories in `__init__` and keep
explicit delegating methods with the current signatures. Do not use
`__getattr__` or multiple-inheritance mixins.

Make `postgres_store.py` a compatibility export module:

```python
from wikibricks.storage import IngestResult, PostgresStore
from wikibricks.storage.content import MAX_SEARCH_CHUNK_BYTES, iter_search_chunks, page_content_hash

__all__ = [
    "IngestResult",
    "MAX_SEARCH_CHUNK_BYTES",
    "PostgresStore",
    "iter_search_chunks",
    "page_content_hash",
]
```

- [ ] **Step 5: Remove private storage calls from maintenance and curation**

Replace the private search chunk call with
`insert_search_chunks(conn, table, version_id, text)`. Replace the private page
write call with
`store.pages.write_in_connection(conn, path, title, content_json, **kwargs)`.

- [ ] **Step 6: Run focused and full gates**

```bash
uv run pytest tests/test_postgres_store.py tests/test_local_maintenance.py tests/test_local_ingest.py tests/test_curation_sync.py tests/test_lakebase_sync.py -q
uv run ruff check src tests
uv run pytest -q
```

Expected: PASS with the same hashes, search results, row counts, and rollback
behavior.

- [ ] **Step 7: Commit storage modules**

```bash
git add src/wikibricks/storage src/wikibricks/postgres_store.py src/wikibricks/maintenance.py src/wikibricks/curation_sync.py tests/test_postgres_store.py
git commit -m "refactor: split PostgreSQL storage repositories"
```

---

### Task 7: split the curation protocol from persistence and application

**Files:**
- Create: `src/wikibricks/curation/__init__.py`
- Create: `src/wikibricks/curation/protocol.py`
- Create: `src/wikibricks/curation/repository.py`
- Create: `src/wikibricks/curation/planning.py`
- Create: `src/wikibricks/curation/application.py`
- Rewrite: `src/wikibricks/curation_sync.py`
- Modify: `tests/test_curation_sync.py`

**Interfaces:**
- Produces: `create_patch`, `build_manifest`, `validate_manifest`, `get_or_create_replica_id`, `publish_manifest`, `pull_manifests`, `plan_run`, `apply_run`, `list_conflicts`, and `resolve_conflict`
- Preserves: current dictionaries, hashes, exceptions, idempotency, conflict rules, and transaction behavior

- [ ] **Step 1: Record the green curation protocol and integration behavior**

Run: `uv run pytest tests/test_curation_sync.py -q`

Expected: 14 tests pass. This is the green state for the module extraction.

- [ ] **Step 2: Extract the pure protocol**

Move constants, canonical JSON, content hashing, path validation, proposal
hashing, patch construction, patch validation, manifest construction, and
manifest validation into `curation/protocol.py`. This module may import only
the standard library and `storage.content.page_content_hash`.

- [ ] **Step 3: Extract database persistence functions**

Move replica identity, manifest insertion, publish/pull, patch loading, page
state reads, receipts, run completion, and conflict listing into
`curation/repository.py`. Keep transaction ownership in the public operation
that currently owns it.

- [ ] **Step 4: Extract planning and application**

`planning.py` owns grouping, preflight, conflict detail, receipt counts, and
`plan_run`. `application.py` owns link retargeting, page mutation, group
application, `apply_run`, and `resolve_conflict`.

Keep the dependency direction exact:

```text
protocol <- repository <- planning <- application
```

`application` may import all earlier modules. No earlier module imports a later
one.

- [ ] **Step 5: Add stable exports**

Export the ten public functions from `wikibricks.curation.__init__`.
`curation_sync.py` imports and re-exports the same names for the first
local-only release.

- [ ] **Step 6: Verify protocol bytes and database behavior**

```bash
uv run pytest tests/test_curation_sync.py tests/test_local_maintenance.py tests/test_resources.py -q
uv run ruff check src tests
```

Expected: PASS. Existing literal manifest hashes in tests must not change.

- [ ] **Step 7: Commit curation modules**

```bash
git add src/wikibricks/curation src/wikibricks/curation_sync.py tests/test_curation_sync.py
git commit -m "refactor: split curation protocol and application"
```

---

### Task 8: move the optional Lakebase adapter under the main namespace

**Files:**
- Create: `src/wikibricks/remote/__init__.py`
- Create: `src/wikibricks/remote/lakebase.py`
- Modify: `src/wikibricks/cli.py`
- Modify: `tests/test_lakebase_sync.py`
- Modify: `tests/test_package_boundaries.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Delete: `src/wikibricks_databricks/`

**Interfaces:**
- Produces: `wikibricks.remote.lakebase`
- Preserves: `LakebaseTarget`, `build_batch`, `sync_to_archive`, `pull_curated_snapshot`, and `pull_curation_patches`
- Requires: Databricks SDK only for `LakebaseTarget.fresh_database_url()`

- [ ] **Step 1: Write a failing import and package-boundary test**

```python
def test_lakebase_adapter_is_optional_and_lazy():
    from wikibricks.remote.lakebase import LakebaseTarget

    target = LakebaseTarget("project", "production", "primary", "wikibricks", "profile")
    assert target.database == "wikibricks"
```

Update the blocked-Databricks subprocess to import
`wikibricks.remote.lakebase` and every base module. It must print `ok` without
trying to import `databricks`.

- [ ] **Step 2: Run tests and confirm the new namespace is absent**

Run: `uv run pytest tests/test_lakebase_sync.py tests/test_package_boundaries.py -q`

Expected: FAIL importing `wikibricks.remote.lakebase`.

- [ ] **Step 3: Move the adapter and update lazy imports**

Move the existing module without changing batch hashes or SQL. Update the CLI
and its internal curation import to `wikibricks.curation`. Keep the Databricks
SDK import inside `fresh_database_url()`.

- [ ] **Step 4: Build and inspect dependency metadata**

```bash
uv lock
uv run pytest tests/test_lakebase_sync.py tests/test_package_boundaries.py tests/test_curation_sync.py -q
uv build
unzip -p dist/wikibricks-*.whl '*/METADATA' | rg '^Requires-Dist:'
```

Expected: MCP, Psycopg, and PyYAML are base requirements. Databricks SDK is
qualified by the `lakebase` extra.

- [ ] **Step 5: Commit the optional adapter move**

```bash
git add pyproject.toml uv.lock src/wikibricks/remote src/wikibricks/cli.py tests/test_lakebase_sync.py tests/test_package_boundaries.py
git add -u src/wikibricks_databricks
git commit -m "refactor: isolate optional Lakebase sync"
```

---

### Task 9: consolidate the retained tests

**Files:**
- Create: `tests/test_karpathy.py`
- Modify: `tests/test_agent_tools.py`
- Modify: `tests/test_session_contract.py`
- Modify: `tests/test_plugin_manifest.py`
- Delete: `tests/test_export_karpathy.py`
- Delete: `tests/test_import_karpathy.py`
- Delete: `tests/test_karpathy_export.py`
- Delete: `tests/test_karpathy_logic.py`

**Interfaces:**
- Produces: a suite where each test protects a product contract, data invariant, or boundary failure
- Preserves: Karpathy Markdown parsing and round-trip behavior

- [ ] **Step 1: Map each retained test to a failure it detects**

Keep these test groups:

```text
public local API
YAML configuration
session contracts and adapters
PostgreSQL versions and rollback
curation protocol and conflict application
explicit Lakebase archive sync
MCP resource and stdio behavior
Claude plugin command contracts
Karpathy Markdown import and export
backup, restore, health, and local curation
```

Remove tests that can fail only because a function moved, a mock call changed,
or source text changed.

- [ ] **Step 2: Consolidate Karpathy tests into behavior scenarios**

The new file must cover: frontmatter scalar and list tags, page path mapping,
one complete import, one complete export, an import/export round trip,
idempotent re-import, links, and exclusion of session/archive pages. Reuse
`examples/karpathy_wiki` instead of rebuilding the same fixture in four files.

- [ ] **Step 3: Parametrize repeated validation cases**

Use tables for event-kind errors, configuration errors, and plugin hook events.
Keep large PostgreSQL and curation scenarios separate because their side
effects differ.

- [ ] **Step 4: Run the suite and inspect the collection**

```bash
uv run pytest --collect-only -q
uv run pytest -q
uv run ruff check src tests
```

Expected: the collected count is below 200, every collected test belongs to a
retained product area, and all tests pass. Do not delete a distinct invariant
only to lower the count.

- [ ] **Step 5: Commit the lean suite**

```bash
git add tests
git commit -m "test: keep only local product contracts"
```

---

### Task 10: rewrite adoption and architecture documentation

**Files:**
- Rewrite: `README.md`
- Modify: `AGENTS.md`
- Modify: `CONTRIBUTING.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/curation-sync.md`
- Modify: `docs/validation/local-first-local-gate.md`
- Delete: `NEXT_STEPS.md`
- Delete: `docs/hotpotqa_evaluation.md`
- Delete: `docs/twowiki_evaluation.md`

**Interfaces:**
- Produces: one install path, one local architecture description, and one optional Lakebase section

- [ ] **Step 1: Rewrite README around the three-command start**

Open with:

```markdown
# WikiBricks

WikiBricks gives AI coding agents durable local memory in PostgreSQL.

```bash
pip install wikibricks
wikibricks init
wikibricks-mcp
```
```

Then document MCP registration, Omnigent/JSONL import, optional Claude hooks,
long-session storage, maintenance, YAML configuration, Markdown/JSON resources,
explicit Lakebase archive sync, development, and license. Remove the legacy
Databricks compatibility section.

- [ ] **Step 2: Update repository rules and contributor commands**

Update the source tree in `AGENTS.md`. Remove SQL Warehouse and public-mirror
instructions that name deleted files. Change lint commands to
`uv run ruff check src tests` and describe the reduced test contract.

- [ ] **Step 3: Record the breaking removal in Unreleased**

Add concise `Changed` and `Removed` entries. Do not select or publish a release
version in this task.

- [ ] **Step 4: Apply the documentation editing pass**

Use the `avoid-ai-writing` skill in edit mode. Preserve commands, links, table
cells, code blocks, and exact technical meaning. Run:

```bash
rg -n "SQL Warehouse|Vector Search|Unity Catalog|wiki-target|wikibricks_recorder|wikibricks_databricks" README.md AGENTS.md CONTRIBUTING.md docs plugin
node /Users/philipp.tiefenbacher/.agents/skills/avoid-ai-writing/scripts/check-style.js README.md --config /Users/philipp.tiefenbacher/.agents/skills/avoid-ai-writing/examples/technical.json
```

Expected: only historical or migration references remain; the style check has
zero hard findings.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md AGENTS.md CONTRIBUTING.md CHANGELOG.md docs plugin
git add -u NEXT_STEPS.md
git commit -m "docs: explain the local-only WikiBricks"
```

---

### Task 11: run the complete local release gate

**Files:**
- Modify: `docs/validation/local-first-local-gate.md`
- Create: `tests/wheel_smoke.py`

**Interfaces:**
- Verifies: the approved specification and every retained release contract

- [ ] **Step 1: Run lint and full PostgreSQL 16 tests**

```bash
PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" uv run ruff check src tests
PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" uv run pytest -q
```

Expected: both exit zero. Record the exact test count and elapsed time.

- [ ] **Step 2: Run the offline gate**

```bash
PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" UV_OFFLINE=1 uv run pytest -q
```

Expected: the same test count passes without dependency or network access.

- [ ] **Step 3: Run PostgreSQL 17 integration tests**

```bash
PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH" uv run pytest tests/test_postgres_store.py tests/test_local_ingest.py tests/test_local_maintenance.py tests/test_curation_sync.py tests/test_lakebase_sync.py tests/test_mcp_end_to_end.py -q
```

Expected: all selected database tests pass on PostgreSQL 17.11.

- [ ] **Step 4: Build and inspect the wheel**

```bash
uv build
unzip -l dist/wikibricks-*.whl
unzip -p dist/wikibricks-*.whl '*/METADATA' | rg '^Requires-Dist:'
shasum -a 256 dist/wikibricks-*.whl
```

Expected: only the `wikibricks` package and declared resources ship. No
Databricks SDK base requirement, recorder package, graph App, notebook, bundle,
or script is present.

- [ ] **Step 5: Add and run the installed-wheel stdio smoke script**

`tests/wheel_smoke.py` must use `mcp.client.stdio.stdio_client` and
`ClientSession` to launch the `wikibricks-mcp` executable found on `PATH`. It
asserts the exact five tool names, writes `topics/wheel-smoke`, searches for
`installed wheel marker`, and reads the body back.

Run the script only through an environment containing the built wheel:

```bash
smoke_dir=$(mktemp -d /tmp/wikibricks-wheel.XXXXXX)
pg_data="$smoke_dir/postgres"
pg_socket="$smoke_dir/socket"
cleanup_smoke() {
  PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" pg_ctl -D "$pg_data" -m fast -w stop >/dev/null 2>&1 || true
}
trap cleanup_smoke EXIT
mkdir -p "$pg_socket"
PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" initdb -D "$pg_data" --auth=trust --no-locale -E UTF8
PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" pg_ctl -D "$pg_data" -o "-k $pg_socket -h '' -p 54329" -w start
PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH" createdb -h "$pg_socket" -p 54329 wikibricks
uv venv "$smoke_dir/venv" --python 3.14
uv pip install --python "$smoke_dir/venv/bin/python" dist/wikibricks-*.whl
database_url="dbname=wikibricks host=$pg_socket port=54329"
PATH="$smoke_dir/venv/bin:$PATH" WIKIBRICKS_DATABASE_URL="$database_url" wikibricks init
"$smoke_dir/venv/bin/python" -c "import importlib.util; assert importlib.util.find_spec('databricks') is None"
PATH="$smoke_dir/venv/bin:$PATH" WIKIBRICKS_DATABASE_URL="$database_url" "$smoke_dir/venv/bin/python" tests/wheel_smoke.py
cleanup_smoke
trap - EXIT
test -n "$smoke_dir" && test "$smoke_dir" != "/" && rm -rf -- "$smoke_dir"
```

Expected: all operations succeed with no Databricks package or credentials.

- [ ] **Step 6: Run backup, restore, adapter, and archive retry checks**

Run the retained integration tests that compare backup fingerprints, import an
Omnigent session with Codex metadata, archive to a second local PostgreSQL
database, inject one remote failure, and retry the same immutable batch.

Expected: restored fingerprints match, the source Omnigent SQLite database is
unchanged, failed archive rows remain unacknowledged, and retry creates no
duplicates.

- [ ] **Step 7: Update validation evidence and verify the worktree**

Record commands, versions, counts, elapsed times, wheel contents, and SHA-256
in `docs/validation/local-first-local-gate.md`. Then run:

```bash
git diff --check
git status --short
git diff --stat HEAD~10..HEAD
```

Expected: no whitespace errors; only the pre-existing untracked `project.yaml`
remains outside committed work.

- [ ] **Step 8: Apply the final documentation pass and commit evidence**

Use `avoid-ai-writing` in edit mode on the validation report, re-run the
technical style check, then commit:

```bash
git add docs/validation/local-first-local-gate.md
git commit -m "docs: record local-only validation"
```

Do not push, publish, deploy, tag, modify remote data, or start Lakebase
migration work.
