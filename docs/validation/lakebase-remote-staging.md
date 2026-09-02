# Lakebase remote maintenance validation

## Live hybrid search validation, 2026-09-02

Branch: `feat/lakebase-hybrid-curation`

Commit tested: `73b45aa`

The staging job completed an end-to-end hybrid curation run. The weekly schedule
remained paused.

### Deployed resources

| Resource | Value |
|---|---|
| Lakebase project | `projects/wikibricks` |
| Branch | `projects/wikibricks/branches/staging` |
| Endpoint | `primary` |
| Database | `wikibricks` |
| Lakeflow Job | `222630699179712` |
| Wheel | `wikibricks-0.11.0-py3-none-any.whl` |
| Schedule | Sunday at 04:00 UTC, paused |
| Embedding endpoint | `databricks-gte-large-en` |
| Curator endpoint | `databricks-claude-sonnet-4-5` |

Lakebase Search was enabled for the project. PostgreSQL loaded these extensions:

| Extension | Version |
|---|---|
| `lakebase_text` | `0.1.1` |
| `lakebase_vector` | `1.1.0` |
| `vector` | `0.8.0` |

`lakebase_vector` requires `vector`. The job uses the `lakebase_ann` index and
does not use the pgvector HNSW or IVFFlat indexes.

### Deployment regression

Run `183582763984640` failed before database access. The serverless environment
did not contain `psycopg`. Commit `73b45aa` added
`psycopg[binary]==3.2.13` to the job environment and added a bundle contract
test. The overnight-dev hook passed Ruff and all 124 tests before the commit.

Run `632003367278097` then completed with an idle result. It confirmed that the
fixed serverless environment starts when no archive replica needs maintenance.

### Hybrid retrieval evidence

An isolated local SQLite store synced two page versions to replica
`f0ad0a30-1a20-42db-b8b7-0febdd8bbe01`. Run `435907047248518` processed that
replica.

The first task attempt projected and embedded the two documents. It rejected an
invalid model proposal because the proposal had no cleanup target. The configured
task retry reused the derived documents and completed successfully.

| Check | Result |
|---|---|
| Search status | `available` |
| Search documents | 2 |
| Embedded documents | 2 |
| Embedding dimensions | 1024 |
| Hybrid queries | 2 |
| ANN matches | 2 |
| BM25 matches | 2 |
| `lakebase_ann` index | valid and ready |
| `lakebase_bm25` index | valid and ready |

The successful attempt published manifest
`1adda338540c6b51f70d3587acd5f140c4685f51a6ef212fe600d1d9d4c6f0e5`.
It contained one guarded group with three patches: `update_page`, `add_alias`,
and `supersede_page`.

The isolated local store pulled one run and three patches. The safe policy
classified the high-risk group as `review_required`. The test then used the
explicit `all` policy. The group applied in one transaction. The canonical page
advanced to version 2, the duplicate became superseded, and the alias resolved
to the canonical page. The local store recorded three applied receipts.

### Cleanup

The test removed the isolated replica from staging after validation. It deleted
two search documents, two archive events, one archive batch, one curation run,
and one maintenance run. The pre-existing staging rows remained unchanged. The
Search table and both hybrid indexes remain available.

### Local gates

| Check | Command | Result |
|---|---|---|
| Ruff | `uv run --no-sync ruff check src tests` | pass |
| Full suite | `uv run --no-sync pytest -q` | 124 passed |
| Offline suite | `UV_OFFLINE=1 uv run --no-sync pytest -q` | 124 passed |
| Offline build | `UV_OFFLINE=1 uv build` | wheel and source archive built |
| Installed wheel | `tests/wheel_smoke.py` in an isolated virtual environment | MCP smoke passed |
| Bundle | `databricks bundle validate --strict -t staging --profile pt` | pass |

| File | SHA-256 |
|---|---|
| `wikibricks-0.11.0-py3-none-any.whl` | `204a6a4745f28c92401b13d5b3378995554ed8040c4f1b4887484114fad8371b` |
