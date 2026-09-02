# Lakebase hybrid curation design

## Decision

WikiBricks will use Lakebase Search only inside the optional weekly remote
curation job. Local SQLite remains the active memory store and continues to
use FTS5. Embeddings will not be generated, stored, queried, or synchronized
on the user's machine.

The Databricks job will generate embeddings for previously unseen remote
content, store them in Lakebase, and use `lakebase_vector` and
`lakebase_text` to find related pages. Search results are candidates, not
curation decisions. The existing model-backed curator will decide whether to
update a page, add a graph link, or propose a duplicate-page cleanup group.
Every local change will still arrive through an immutable manifest and pass
the existing exact-version and content-hash checks.

## Product boundary

The remote enhancement must preserve these properties:

- WikiBricks works locally when Databricks, Lakebase Search, credentials, and
  the network are unavailable.
- The public MCP server continues to expose exactly five tools.
- Raw sessions and page versions remain the portable source of truth.
- Remote vectors are disposable derived state. They are never part of the
  sync protocol.
- Lakebase Search remains optional even when Lakebase archival is enabled.
- A missing Lakebase Search preview falls back to the current bounded
  curator. An enabled but failing embedding or search call fails the weekly
  job without advancing its archive watermark.
- The Databricks job never connects to a local WikiBricks database.

## Current limitation

Remote maintenance currently selects at most 200 new archive events and the
latest 200 page versions ordered by path. It sends both collections directly
to the curator. PostgreSQL trigram and `tsvector` search exist in the
compatibility store, but the weekly job does not use them for candidate
selection.

This limits duplicate detection and page linking to the pages that happen to
fit in one prompt. Page count also increases model input even when most pages
are unrelated to the new evidence.

## Data flow

```text
local SQLite
  immutable page and session versions
        |
        | existing guarded archive push
        v
Lakebase archive_events
        |
        | weekly remote projection
        v
remote_search_documents
  bounded text chunks + tsvector + VECTOR(1024)
        |
        | lakebase_bm25 + lakebase_ann + RRF
        v
related page candidates
        |
        | existing Databricks curator
        v
immutable page/link/merge manifest
        |
        | existing background pull and exact-base apply
        v
local SQLite
  new page versions and graph links, no vectors
```

## Lakebase Search setup

The job will check `pg_available_extensions` for `lakebase_vector` and
`lakebase_text`. If both are available, it will install them and apply a
remote-only SQL migration. The bundle will not enable Lakebase Search on a
project because enabling the beta feature is irreversible and restarts all
project compute.

The migration creates `remote_search_documents` with:

- a deterministic document ID;
- replica, archive event, entity, version, and sequence identifiers;
- page path, title, document kind, and chunk index;
- the exact text returned to the curator;
- a SHA-256 content hash;
- a `tsvector` column for BM25; and
- a nullable `VECTOR(1024)` embedding.

A unique constraint on archive event and chunk index makes projection
idempotent. Old vectors can remain as immutable derived history. Queries use
only the latest page version at the selected archive watermark and exclude a
page after an applied `supersede_page` receipt.

The table has a `lakebase_ann` cosine index and a `lakebase_bm25` index. The
job rebuilds the BM25 index after adding documents because its corpus
statistics are calculated at build time.

## Embedding generation

`lakebase_vector` provides pgvector-compatible types, distance operators, and
the `lakebase_ann` index. It does not call an embedding model. The weekly job
will query a Databricks embedding endpoint through the Python SDK and insert
the returned vectors into Lakebase.

The first implementation uses `databricks-gte-large-en`, which returns 1,024
dimensions and accepts up to 8,192 tokens. Search chunks are limited to 12,000
characters and split at paragraph boundaries. Only page text and `user` or
`assistant` session events are embedded. Tool calls, tool results, lifecycle
events, and errors remain immutable evidence but do not consume embedding
calls.

The job generates one vector per missing `(embedding_model, content_hash)` and
copies it to any duplicate document rows. Embedding work, evidence queries,
page projection, and candidate counts are bounded by readable policy values.
Changing the embedding endpoint requires a matching 1,024-dimensional model
and causes content to be embedded again under the new model identifier.

## Hybrid candidate retrieval

For each bounded new page, user, or assistant evidence chunk, the job runs two
Lakebase searches against current page chunks from the same replica:

1. cosine-distance search through `lakebase_ann`;
2. BM25 keyword search through `lakebase_bm25`.

The job combines the two ranked lists with Reciprocal Rank Fusion. It groups
chunk matches by page, keeps the best score per page, and sends only the top
page candidates to the curator. Each candidate includes its archived page
evidence ID and the vector, keyword, and fused ranks. The source page is also
included for a page-version event so the curator can compare it with its
neighbors.

Similarity does not imply identity. The curator must classify each useful
relationship as one of:

- the same topic, so update the existing page;
- related but distinct, so add a typed graph link;
- contradictory, so add a `contradicts` link and preserve both claims;
- a true duplicate, so publish the existing grouped merge sequence; or
- unrelated, so return no proposal.

## New `add_link` patch

The curation protocol will add an idempotent `add_link` operation. Its
proposal is:

```json
{
  "target_path": "topics/target",
  "link_type": "related"
}
```

Allowed remote link types are `related`, `supports`, `contradicts`, and
`depends_on`. The source path carries the normal base version and content
hash. Local preflight requires an active source with an exact base match and
an active target. An existing identical link is `already_applied`; otherwise
the application inserts the edge with `origin=remote-curator` in the same
transaction as its receipt.

`add_link` is low risk because it does not replace text or supersede a page.
Duplicate cleanup remains high risk and retains the existing atomic sequence:
update the canonical page, retarget links, add an alias, and supersede the
duplicate. Search scores alone can never publish or apply that sequence.

## Failure behavior

- If the two extensions are unavailable, the job uses the existing bounded
  page selection and reports `search_status=unavailable`.
- If a vector has the wrong dimension or the embedding response is malformed,
  the job fails before calling the curator.
- If the job fails after some document or embedding inserts, the next run
  reuses them by deterministic IDs and content hashes.
- A failed hybrid query does not publish a no-change run or advance the
  watermark.
- Local pull, apply, conflict resolution, FTS repair, and normal agent work do
  not know that embeddings exist.

## Acceptance criteria

1. A synthetic user session about an existing page retrieves that page when
   keyword phrasing differs but its embedding is close.
2. Exact terms can retrieve a page through BM25 when vector rank is weak.
3. RRF produces a stable page order and excludes other replicas and
   superseded pages.
4. Repeating projection or embedding work creates no duplicate rows or model
   calls for the same model and content hash.
5. A curator can publish a low-risk `add_link` patch that applies once to
   SQLite and is idempotent on replay.
6. Existing grouped duplicate cleanup and exact-base conflict behavior remain
   unchanged.
7. Without Lakebase Search extensions, all existing remote maintenance tests
   follow the current fallback path.
8. The normal and `UV_OFFLINE=1` suites pass without importing Databricks or
   vector libraries into local runtime paths.
9. A clean wheel contains the remote SQL resource and passes the MCP smoke
   test.
10. Staging validation records extension availability, indexed and embedded
    counts, hybrid candidates, manifest hashes, and the local apply result.

## References

1. [Lakebase Search](https://docs.databricks.com/aws/en/oltp/projects/lakebase-search)
2. [lakebase_vector](https://docs.databricks.com/aws/en/oltp/projects/lakebase-vector)
3. [lakebase_text](https://docs.databricks.com/aws/en/oltp/projects/lakebase-text)
4. [Databricks Foundation Model APIs supported models](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models)

