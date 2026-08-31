# Local-only modular architecture design

## Decision

WikiBricks will have one primary runtime: local PostgreSQL. `WikiClient()` will
always connect to local PostgreSQL, and the MCP server and CLI will use the
same implementation. The Databricks SQL Warehouse client, Unity Catalog SQL
generator, Databricks App, jobs, notebooks, and remote recorder will be
removed.

Lakebase remains an optional archive target. The archive command will connect
to Lakebase only when a user runs `wikibricks sync lakebase`. No local read,
write, search, import, maintenance, or MCP path may import the Databricks SDK
or contact a remote service.

This is a clean break. Compatibility with the old SQL Warehouse API is not a
goal. Existing local data and the PostgreSQL schema remain compatible.

## Product boundary

The default installation must support this path:

```bash
pip install wikibricks
wikibricks init
wikibricks-mcp
```

The base package will have three runtime dependencies: MCP, Psycopg, and
PyYAML. PyYAML reads the user configuration file. Databricks SDK remains in
the optional `lakebase` extra.

The supported interfaces are:

- `WikiClient()` for local page, session, graph, and search operations
- `PostgresStore` for lower-level PostgreSQL access
- `wikibricks` for database lifecycle, imports, maintenance, and explicit sync
- `wikibricks-mcp` for the five local memory tools
- `wikibricks-recorder-hook` as the optional Claude Code recording adapter
- JSONL and Omnigent import adapters for other agent runtimes

The public package will no longer export catalog names, Unity Catalog table
names, Vector Search names, SQL Warehouse helpers, or UC function generators.
Passing `warehouse_id` or `workspace_client` to `WikiClient` will no longer be
supported.

## Source layout

The implementation will use one package namespace. Thin compatibility modules
will remain only where they protect the local API.

```text
src/wikibricks/
  __init__.py                 Small public local API
  client.py                   WikiClient alias and local facade
  postgres_store.py           PostgresStore compatibility exports
  curation_sync.py            Curation compatibility exports
  mcp_server.py               Stdio MCP lifecycle and dispatch
  agent_tools.py              Framework-independent callable tools

  config/
    __init__.py               Typed loader and precedence rules
    defaults.yml              Versioned non-secret defaults

  resources/
    agent-instructions.md     Maintained-wiki instructions sent over MCP
    mcp-tools.json            Tool names, descriptions, and input schemas
    schemas/
      session-record.schema.json
      curation-manifest-v1.schema.json

  storage/
    __init__.py               PostgresStore and result exports
    store.py                  Connection, migrations, and repository assembly
    content.py                Canonical hashes and bounded search chunks
    pages.py                  Page versions, aliases, reads, and history
    sessions.py               Session and event version storage
    search.py                 Page, session, and archive search
    outbox.py                 Archive outbox, batches, and cursors
    graph.py                  Sources, operation log, links, and neighbours

  curation/
    __init__.py               Stable curation API
    protocol.py               Patch and manifest construction and validation
    repository.py             Runs, patches, receipts, and conflict records
    planning.py               Grouping, preflight checks, and plans
    application.py            Atomic patch application and resolution

  maintenance.py             Database checks, curation, backup, and restore

  adapters/
    claude_code.py            Claude event normalization
    claude_code_hook.py       Claude hook process and durable buffer
    jsonl.py                  Harness-neutral interchange import
    omnigent.py               Omnigent import

  remote/
    lakebase.py               Explicit archive push and curation pull
```

`PostgresStore` will remain the transactional facade. It will assemble focused
repositories and delegate its existing public methods, so callers do not need
to know which repository owns an operation. Code that needs an existing
transaction, such as curation application, will call a documented page
repository method with a Psycopg connection instead of a private store method.

`curation_sync.py` will re-export the local curation API during the first
local-only release. New code and documentation will import from
`wikibricks.curation`. This keeps existing curation scripts working without
preserving the old Databricks API.

## Declarative files

Readable files will contain policy and interface descriptions. They will not
contain secrets or protocol invariants.

`defaults.yml` will define values that an operator can change without altering
the database protocol:

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

WikiBricks will load packaged defaults first, then
`~/.wikibricks/config.yml`, then a path from `WIKIBRICKS_CONFIG`. Environment
variables and explicit CLI arguments win. Unknown keys, unsupported versions,
invalid types, and unsafe values will fail with a path-specific error. Missing
user files are normal. Config files will never trigger network access.

`agent-instructions.md` will contain the maintained-wiki workflow currently in
`WIKIBRICKS.MD`. MCP clients receive this text as server instructions, so the
same policy applies to Codex, Claude, Omnigent, and any other MCP client.

`mcp-tools.json` will be the source for the five MCP tool declarations. The
dispatch code remains Python. A startup validation step will reject an invalid
tool catalog or a catalog whose tool names do not match the dispatch table.

The JSON Schema files document and test the session and curation interchange
formats. Runtime curation validation remains in Python because it also checks
canonical hashes, exact base versions, group constraints, and operation
semantics. JSON Schema validation will run in the development test suite, not
in the local hot path.

These values remain fixed in Python and SQL:

- canonical JSON encoding and SHA-256 inputs
- curation schema version and supported operations
- risk classes and cleanup operations that require high risk
- exact base version and content hash checks
- advisory locks and transaction boundaries
- outbox atomicity and immutable version rules
- the 64 KiB UTF-8 search chunk ceiling
- the `pg_trgm` requirement and database constraints

Changing one of these values requires a protocol or migration change. An
editable YAML file must not be able to weaken sync safety.

## Removed code and assets

The cleanup will delete active code that depends on the old remote wiki:

- the Databricks implementation inside `wikibricks.client`
- `wikibricks.ops` and its Unity Catalog, Vector Search, and UC function SQL
- remote curation, tagging, segregation, promotion, and graph job logic that
  exists only for the old Delta tables
- the Databricks App and graph App
- the current Databricks bundle, job resources, and deployment notebooks
- the remote Claude recorder, its target selector, Databricks model helpers,
  and duplicate remote MCP server
- scripts and tests whose only subject is one of these removed components

The cleanup will keep the local Claude hook, generic session model, Omnigent
and JSONL adapters, PostgreSQL migrations, local curation protocol, Lakebase
archive transport, Karpathy Markdown import and export, local MCP server, and
local maintenance commands.

Research reports may remain under `docs/research` because they record prior
evaluation results. They will be marked historical if they describe a removed
runtime. Generated deployment artifacts will not ship in the wheel.

## Data and control flow

Local writes follow one path:

```text
agent runtime or importer
  -> SessionRecord or page command
  -> WikiClient
  -> PostgresStore
  -> focused repository
  -> PostgreSQL transaction
  -> immutable version plus outbox event
```

Local reads stop at PostgreSQL. They never fall back to Lakebase.

Archive sync is explicit:

```text
wikibricks sync lakebase
  -> lazy import of wikibricks.remote.lakebase
  -> bounded outbox batch
  -> Lakebase transaction
  -> local acknowledgement
  -> optional curation manifest pull
```

Downloaded curation manifests enter the durable local inbox. Planning and
application use the existing exact-base checks. Low-risk groups may apply
under the `safe` policy. Medium- and high-risk groups remain review items until
the user selects the `all` policy or resolves a conflict. Local writes that
happen after remote curation input create conflicts instead of being
overwritten.

## Error behavior

Configuration errors will name the file and key. Resource files missing from
an installed wheel will fail at startup. PostgreSQL errors will keep their
current exception types. A failed write, patch group, or outbox update will
roll back its transaction.

The local CLI must remain usable when Databricks SDK is absent. Running the
Lakebase command without the `lakebase` extra will return a short installation
instruction. Network and authentication failures will stop the sync command
without changing acknowledged outbox state.

## Migration and release boundary

The refactor will not change the PostgreSQL schema or rewrite stored content.
Existing local databases must open and pass `wikibricks check` without a data
migration. Public Python compatibility is required only for the local symbols
listed in this document.

The version will move to the next breaking release before public publication.
The exact number will be chosen during release preparation, when the public
mirror and changelog are in scope. No remote deployment, public push, tag, or
Lakebase migration belongs to this cleanup.

## Test strategy

Characterization tests will protect the current local results before modules
move. Each implementation slice will use a failing test, the smallest code
change, and a focused passing test before the full gate runs.

The final local gate will verify:

- every retained local public method and CLI command
- page and session immutability, outbox atomicity, and 25 MB event round trips
- deterministic search and bounded UTF-8 chunks
- curation hashes, idempotency, exact-base conflicts, cleanup groups, receipts,
  and concurrent application
- YAML precedence and validation
- JSON resource and JSON Schema validation
- the five MCP schemas, real stdio calls, and harness-neutral descriptions
- imports and local operation with Databricks modules blocked
- no required Databricks dependency in wheel metadata
- PostgreSQL 16 and 17 integration tests
- full tests and Ruff, followed by the offline test gate
- clean wheel installation, packaged resources, backup and restore hashes, and
  remote failure with idempotent retry against a second local PostgreSQL
  database

The overnight-development pre-commit hook will run Ruff and the full test
suite for each commit. The existing untracked `project.yaml` is outside this
work and will not be staged.

## Completion criteria

The cleanup is complete when a new user can identify the local runtime from
the top-level README, install it without Databricks, configure it through one
YAML file, inspect the MCP contract in JSON, and edit the wiki guidance in
Markdown. No retained production file should mix local PostgreSQL work with
legacy SQL Warehouse behavior. The complete local release gate must pass
before any Lakebase migration or remote WikiBricks update begins.
