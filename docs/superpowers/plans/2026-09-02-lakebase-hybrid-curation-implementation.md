# Lakebase Hybrid Curation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use remote-only Lakebase vector and BM25 search to select related wiki pages for the weekly curator, then synchronize validated page, link, and merge patches back to local SQLite without local embeddings.

**Architecture:** A remote-only search package projects immutable Lakebase archive events into bounded text chunks, generates missing 1,024-dimensional embeddings through a Databricks embedding endpoint, and uses `lakebase_ann`, `lakebase_bm25`, and Reciprocal Rank Fusion to select current pages. The existing curator remains the semantic judge and the existing manifest protocol remains the only route back to local state.

**Tech Stack:** Python 3.10+, SQLite FTS5, PostgreSQL 16, Lakebase Autoscaling, `lakebase_vector`, `lakebase_text`, Databricks Python SDK, pytest, Ruff, Databricks Asset Bundles

**Spec:** `docs/superpowers/specs/2026-09-02-lakebase-hybrid-curation-design.md`

## Global constraints

- Local SQLite stores no vectors and imports no Databricks or vector package.
- `lakebase_vector` indexes vectors; the weekly job generates them through a Databricks embedding endpoint.
- The public MCP contract remains exactly five tools.
- Search candidates never directly mutate a page or graph edge.
- Every local change uses immutable manifests, exact base IDs and hashes, transactional groups, and receipts.
- Lakebase Search setup is remote-only and falls back when its beta extensions are unavailable.
- Search, projection, embedding, and model input are bounded and idempotent.
- All remote resources remain paused by default and scale to zero.
- Production code follows a red, green, refactor cycle and each commit passes the overnight hook.

---

### Task 1: Idempotent remote graph links

**Files:**
- Modify: `tests/test_curation_sync.py`
- Modify: `tests/test_remote_maintenance.py`
- Modify: `src/wikibricks/curation/protocol.py`
- Modify: `src/wikibricks/curation/planning.py`
- Modify: `src/wikibricks/curation/application.py`
- Modify: `src/wikibricks_remote/maintenance.py`
- Modify: `src/wikibricks_remote/resources/curation-proposals.schema.json`
- Modify: `src/wikibricks_remote/resources/remote-policy.yml`
- Modify: `src/wikibricks_remote/resources.py`
- Modify: `src/wikibricks/resources/schemas/curation-manifest-v1.schema.json`

**Interfaces:**
- Extends: `create_patch(operation="add_link", proposal={"target_path": str, "link_type": str}, ...)`.
- Extends: `RemotePolicy.allowed_link_types: tuple[str, ...]`.
- Preserves: curation manifest schema version 1 because `proposal` is already operation-specific JSON.

- [ ] **Step 1: Write a failing SQLite behavior test**

```python
def test_remote_link_patch_applies_once_and_requires_an_exact_source_base(tmp_path, curation_remote_url):
    local = SQLiteStore(tmp_path / "links.db")
    remote = PostgresStore(curation_remote_url)
    local.migrate()
    _reset(remote)
    local.write_page("topics/source", "Source", {"summary": "source", "body": "one"})
    local.write_page("topics/target", "Target", {"summary": "target", "body": "two"})
    source = local.current_page_state("topics/source")
    patch = create_patch(
        operation="add_link",
        path="topics/source",
        proposal={"target_path": "topics/target", "link_type": "related"},
        base_version_id=source["version_id"],
        base_content_hash=source["content_hash"],
        evidence_ids=["archive-event:test"],
        reason="The pages cover related but distinct concepts.",
    )
    manifest = _publish_and_pull(local, remote, [patch])
    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"applied": 1}
    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"already_processed": 1}
    assert local.graph_neighbors("topics/source")[0]["path"] == "topics/target"
```

- [ ] **Step 2: Run `uv run --no-sync pytest tests/test_curation_sync.py::test_remote_link_patch_applies_once_and_requires_an_exact_source_base -q` and confirm `add_link` is rejected as unsupported**
- [ ] **Step 3: Extend protocol validation so `add_link` accepts only `target_path` and `link_type`, requires a source base, and remains low risk**
- [ ] **Step 4: Implement real link preflight and transactional insertion for SQLite and PostgreSQL; record `origin=remote-curator` and the patch ID in metadata**
- [ ] **Step 5: Add a failing remote proposal test whose `add_link` uses an unapproved link type, then enforce `allowed_link_types` during `build_patches`**
- [ ] **Step 6: Update both JSON schemas and run `uv run --no-sync pytest tests/test_curation_sync.py tests/test_remote_maintenance.py tests/test_resources.py -q`**
- [ ] **Step 7: Commit with `git commit -m "feat: add guarded remote wiki links"`**

### Task 2: Remote search document projection

**Files:**
- Create: `tests/test_remote_search.py`
- Create: `src/wikibricks_remote/search/__init__.py`
- Create: `src/wikibricks_remote/search/documents.py`
- Create: `src/wikibricks_remote/search/lakebase.py`
- Create: `src/wikibricks_remote/sql/0001_lakebase_search.sql`
- Modify: `src/wikibricks_remote/resources.py`
- Modify: `src/wikibricks_remote/resources/remote-policy.yml`

**Interfaces:**
- Produces: `project_event(event: dict[str, Any], *, max_chars: int = 12000) -> tuple[SearchDocument, ...]`.
- Produces: `LakebaseHybridSearch(store: PostgresStore, *, embedding_model: str, embedding_dimension: int = 1024)`.
- Produces: `LakebaseHybridSearch.available() -> bool` and `LakebaseHybridSearch.migrate() -> None`.
- Produces: `LakebaseHybridSearch.project(replica_id, watermark, evidence, *, max_pages) -> int`.

- [ ] **Step 1: Write failing pure projection tests**

```python
def test_projection_chunks_only_page_user_and_assistant_evidence():
    page = event("page_version", {"path": "topics/a", "title": "A", "content_text": "x" * 12001})
    tool = event("session_event_version", {"kind": "tool_result", "content": "noise"})
    projected = project_event(page)
    assert [item.chunk_index for item in projected] == [0, 1]
    assert all(len(item.content_text) <= 12000 for item in projected)
    assert project_event(tool) == ()
```

- [ ] **Step 2: Run `uv run --no-sync pytest tests/test_remote_search.py::test_projection_chunks_only_page_user_and_assistant_evidence -q` and confirm the search package is missing**
- [ ] **Step 3: Implement immutable `SearchDocument` projection with paragraph-aware chunks, deterministic UUIDs, and SHA-256 content hashes**
- [ ] **Step 4: Add failing disposable-PostgreSQL tests showing `available()` returns false without Lakebase Search and does not create remote search tables**
- [ ] **Step 5: Add the remote-only SQL migration and migration lock; run it only after both extensions appear in `pg_available_extensions`**
- [ ] **Step 6: Implement idempotent projection of new evidence plus latest missing page versions, bounded by `max_index_pages`**
- [ ] **Step 7: Run `uv run --no-sync pytest tests/test_remote_search.py -q` and commit with `git commit -m "feat: project remote curation search documents"`**

### Task 3: Incremental embeddings and hybrid retrieval

**Files:**
- Modify: `tests/test_remote_search.py`
- Modify: `src/wikibricks_remote/search/lakebase.py`
- Modify: `src/wikibricks_remote/search/__init__.py`
- Modify: `src/wikibricks_remote/main.py`
- Modify: `src/wikibricks_remote/resources.py`
- Modify: `src/wikibricks_remote/resources/remote-policy.yml`

**Interfaces:**
- Produces: `Embedder = Callable[[list[str]], list[list[float]]]`.
- Produces: `build_embedding_updates(documents, embedder, *, model, dimension, batch_size) -> tuple[EmbeddingUpdate, ...]`.
- Produces: `LakebaseHybridSearch.embed_missing(embedder, *, maximum: int, batch_size: int) -> int`.
- Produces: `LakebaseHybridSearch.candidates(replica_id, watermark, evidence, *, maximum_queries, pages_per_query) -> CandidateSelection`.
- Produces: `_embedder(workspace, endpoint) -> Embedder` in the Databricks-only entry point.

- [ ] **Step 1: Write a failing embedding-cache test using projected document records and a deterministic local embedder**

```python
def test_embedding_batches_call_model_once_per_model_and_content_hash():
    calls = []
    embed = lambda texts: calls.append(list(texts)) or [[1.0] + [0.0] * 1023 for _ in texts]
    updates = build_embedding_updates(
        [document("same text"), document("same text")],
        embed,
        model="databricks-gte-large-en",
        dimension=1024,
        batch_size=4,
    )
    assert len(updates) == 2
    assert calls == [["same text"]]
```

- [ ] **Step 2: Run the focused test and confirm `embed_missing` is absent**
- [ ] **Step 3: Implement content-hash reuse, 1,024-dimension validation, bounded SDK batches, and vector updates in one transaction per batch**
- [ ] **Step 4: Add tests for malformed response counts and dimensions; confirm they fail before any archive watermark is recorded**
- [ ] **Step 5: Write a failing hybrid-ranking test with literal vector and BM25 ranks where RRF must return semantic, exact, and dual-match pages in a stable order**
- [ ] **Step 6: Implement Lakebase ANN and BM25 top-K queries, page-level rank aggregation, RRF, same-replica filtering, latest-version filtering, and superseded-page exclusion**
- [ ] **Step 7: Implement `_embedder` with `workspace.serving_endpoints.query(name=endpoint, input=texts)` and validate `response.data[index].embedding`**
- [ ] **Step 8: Run `uv run --no-sync pytest tests/test_remote_search.py tests/test_remote_maintenance.py -q` and commit with `git commit -m "feat: retrieve hybrid Lakebase curation candidates"`**

### Task 4: Weekly curator integration and fallback

**Files:**
- Modify: `tests/test_remote_maintenance.py`
- Modify: `src/wikibricks_remote/maintenance.py`
- Modify: `src/wikibricks_remote/main.py`
- Modify: `src/wikibricks_remote/resources/curation.md`
- Modify: `src/wikibricks_remote/resources/curation-proposals.schema.json`

**Interfaces:**
- Adds: `candidate_provider: Callable[..., CandidateSelection] | None` to `run_maintenance`.
- Adds: `similarity_candidates` to the curator request without changing immutable evidence IDs.
- Adds: search counters to the printed job report.

- [ ] **Step 1: Write a failing maintenance test whose provider returns two related pages outside the previous alphabetical 200-page window**
- [ ] **Step 2: Run it and confirm `run_maintenance` has no candidate-provider boundary**
- [ ] **Step 3: Add the optional provider, replace prompt-wide page selection only when it returns a valid selection, and preserve the existing fallback exactly**
- [ ] **Step 4: Update the curator prompt to treat ranks as candidate evidence, use `add_link` for distinct relationships, and reserve grouped cleanup for identity-level duplicates**
- [ ] **Step 5: Add failure tests proving candidate or embedding errors publish no manifest and record no processed watermark**
- [ ] **Step 6: Run `uv run --no-sync pytest tests/test_remote_maintenance.py tests/test_curation_sync.py tests/test_remote_search.py -q`**
- [ ] **Step 7: Commit with `git commit -m "feat: curate from hybrid page candidates"`**

### Task 5: Bundle and documentation

**Files:**
- Modify: `databricks.yml`
- Modify: `resources/wikibricks_remote.job.yml`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/curation-sync.md`
- Modify: `docs/validation/lakebase-remote-staging.md`
- Modify: `tests/test_remote_maintenance.py`
- Modify: `tests/test_package_boundaries.py`

**Interfaces:**
- Adds: bundle variable and wheel argument `embedding_endpoint`, defaulting to `databricks-gte-large-en`.
- Preserves: one weekly serverless wheel task, one retry, a one-hour timeout, paused schedules, and no local installation change.

- [ ] **Step 1: Write a failing bundle behavior test that parses the resource and expects the embedding endpoint argument while preserving the existing schedule and task count**
- [ ] **Step 2: Add the bundle variable and job parameter without creating a second task, warehouse, endpoint, or always-on resource**
- [ ] **Step 3: Document the remote-only vector flow, one-time Lakebase Search beta enablement, fallback behavior, costs, and the fact that vectors never reach SQLite**
- [ ] **Step 4: Document the `add_link` operation and hybrid candidate fields in the sync protocol**
- [ ] **Step 5: Extend package-boundary coverage so base and offline imports still reject Databricks and vector dependencies**
- [ ] **Step 6: Run `databricks bundle validate --strict -t staging --profile pt` without deploying or enabling the irreversible preview**
- [ ] **Step 7: Commit with `git commit -m "docs: explain remote hybrid curation"`**

### Task 6: Full and staging validation

**Files:**
- Modify: `docs/validation/lakebase-remote-staging.md`

**Interfaces:**
- Consumes: the existing Lakebase staging branch and explicit Databricks profile.
- Produces: repeatable evidence for projection, embedding reuse, hybrid candidates, manifests, local apply, and offline independence.

- [ ] **Step 1: Run `uv run --no-sync ruff check src tests`**
- [ ] **Step 2: Run `uv run --no-sync pytest -q` and `UV_OFFLINE=1 uv run --no-sync pytest -q`**
- [ ] **Step 3: Run `uv build` and install the wheel into a temporary environment; execute `tests/wheel_smoke.py`**
- [ ] **Step 4: Validate the staging bundle with an explicit profile and confirm all schedules remain paused**
- [ ] **Step 5: Check extension availability in the staging Lakebase project; do not enable the irreversible beta without separate approval**
- [ ] **Step 6: If extensions are available, run the paused job manually against staging evidence, pull its manifest into a temporary SQLite database, and assert the expected page link or merge proposal**
- [ ] **Step 7: Re-run the job and record that no unchanged document is embedded twice and no processed watermark is curated twice**
- [ ] **Step 8: Record exact commands, resource IDs, counts, hashes, and any unavailable beta gate in `docs/validation/lakebase-remote-staging.md`**
- [ ] **Step 9: Run `git status --short`, review the diff for secrets and unrelated changes, then commit the validation record**
