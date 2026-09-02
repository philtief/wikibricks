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

## Scale acceptance validation, 2026-09-02

The reusable runner in `scripts/staging_acceptance.py` tested a larger isolated
replica against the same paused staging job. The corpus contained 100 pages, 10
labeled page pairs, and two 24,000-character session events. It archived 102
immutable events and produced 104 search documents after session chunking.

```bash
uv run --extra lakebase python scripts/staging_acceptance.py \
  --profile pt \
  --job-id 222630699179712
```

### Scale defect and fix

Run `772537663828193` exposed a vector parameter error in both task attempts.
Embedding JSON can contain integers and floats, but `psycopg` rejects mixed
numeric lists. Commit `26fae9d` converts every query value to a float and sends
the vector as a typed literal. A focused regression test covers the failing
shape `[0, 1.5, 0]`.

### Bounded maintenance and retrieval

The 120,000-character input limit split the corpus across two maintenance runs.
This confirmed that the watermark resumes from the prior bounded slice.

| Run | Result | Projected | Embedded | Queries | Proposals |
|---|---:|---:|---:|---:|---:|
| `595222299926521` | success | 64 | 64 | 50 | 14 |
| `325154314388536` | success | 40 | 40 | 40 | 0 |
| `723082922198730` | idle | 0 | 0 | 0 | 0 |

The third run left every replica-scoped row count unchanged. The completed
index contained 104 documents with 1,024-dimensional embeddings.

| Labeled retrieval check | Result |
|---|---:|
| Evaluated queries | 20 |
| Vector recall at 10 | 100% |
| BM25 recall at 10 | 100% |
| Hybrid RRF recall at 10 | 100% |

### Local safety and cleanup

The runner published a guarded update, changed the corresponding SQLite page,
then ran the automatic sync cycle. The conflict resolved as `keep_local`; the
newer local title and content remained active, and no pending conflict remained.

Cleanup removed 104 search documents, 103 archive events, two archive batches,
two curation runs, and two maintenance runs for replica
`2e6467a5-543b-444a-9073-4f60cf78e366`. The staging counts returned to one
pre-existing row in each archive and curation table and zero search documents.
The weekly schedule remained paused.

### Local gates

| Check | Command | Result |
|---|---|---|
| Ruff | `uv run --no-sync ruff check src tests scripts` | pass |
| Full suite | `uv run --no-sync pytest -q` | 126 passed |
| Offline suite | `UV_OFFLINE=1 uv run --no-sync pytest -q` | 126 passed |
| Offline build | `UV_OFFLINE=1 uv build` | wheel and source archive built |
| Installed wheel | `tests/wheel_smoke.py` in an isolated virtual environment | MCP smoke passed |
| Bundle | `databricks bundle validate --strict -t staging --profile pt` | pass |

| File | SHA-256 |
|---|---|
| `wikibricks-0.11.0-py3-none-any.whl` | `8f502ea225a00db4b8a5857ca038f2b2206a6b9441260e11c5c7d05b484627a6` |
