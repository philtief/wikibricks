from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from wikibricks.postgres_store import PostgresStore


def _event(entity_kind: str, payload: dict) -> dict:
    return {
        "replica_id": str(uuid4()),
        "event_id": str(uuid4()),
        "sequence": 1,
        "entity_kind": entity_kind,
        "entity_id": str(uuid4()),
        "version_id": str(uuid4()),
        "payload": payload,
    }


def test_projection_chunks_only_pages_and_conversation_evidence():
    from wikibricks_remote.search import project_event

    page = _event(
        "page_version",
        {
            "path": "topics/a",
            "title": "A",
            "content_text": "x" * 12001,
        },
    )
    tool = _event(
        "session_event_version",
        {"kind": "tool_result", "content": "noise"},
    )
    user = _event(
        "session_event_version",
        {"kind": "user", "content": "remember this", "page_path": "sessions/a"},
    )

    first = project_event(page)
    repeated = project_event(page)

    assert [item.chunk_index for item in first] == [0, 1]
    assert all(len(item.content_text) <= 12000 for item in first)
    assert [item.document_id for item in first] == [item.document_id for item in repeated]
    assert project_event(tool) == ()
    assert [item.content_text for item in project_event(user)] == ["remember this"]


def test_search_schema_is_not_created_without_lakebase_extensions(postgres_url: str):
    from wikibricks_remote.search import LakebaseHybridSearch

    store = PostgresStore(postgres_url)
    search = LakebaseHybridSearch(
        store,
        embedding_model="databricks-gte-large-en",
    )

    assert search.available() is False
    assert search.migrate() is False
    with store.connection() as conn:
        table = conn.execute("SELECT to_regclass('remote_search_documents')").fetchone()[0]
    assert table is None


def test_projection_inserts_each_archive_chunk_once(postgres_url: str, monkeypatch):
    from wikibricks_remote.search import LakebaseHybridSearch

    store = PostgresStore(postgres_url)
    store.migrate()
    replica_id = uuid4()
    event = _event(
        "page_version",
        {"path": "topics/once", "title": "Once", "content_text": "stable text"},
    )
    event["replica_id"] = str(replica_id)
    search = LakebaseHybridSearch(
        store,
        embedding_model="databricks-gte-large-en",
    )
    monkeypatch.setattr(search, "migrate", lambda: True)
    monkeypatch.setattr(search, "_refresh_keyword_index", lambda: None)
    with store.connection() as conn, conn.transaction():
        conn.execute(
            "CREATE TABLE remote_search_documents ("
            "document_id uuid PRIMARY KEY, replica_id uuid NOT NULL, "
            "archive_event_id uuid NOT NULL, local_sequence bigint NOT NULL, "
            "entity_kind text NOT NULL, entity_id uuid NOT NULL, version_id uuid NOT NULL, "
            "page_path text, title text, document_kind text NOT NULL, chunk_index integer NOT NULL, "
            "content_text text NOT NULL, content_hash text NOT NULL, "
            "UNIQUE (archive_event_id, chunk_index))"
        )
    try:
        first = search.project(replica_id, 1, [event], max_pages=0)
        repeated = search.project(replica_id, 1, [event], max_pages=0)
        assert (first, repeated) == (1, 0)
    finally:
        with store.connection() as conn, conn.transaction():
            conn.execute("DROP TABLE remote_search_documents")


def test_embedding_batches_call_model_once_per_content_hash():
    from wikibricks_remote.search import build_embedding_updates, project_event

    first = _event(
        "session_event_version",
        {"kind": "user", "content": "same text"},
    )
    second = _event(
        "session_event_version",
        {"kind": "assistant", "content": "same text"},
    )
    documents = [project_event(first)[0], project_event(second)[0]]
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    updates = build_embedding_updates(
        documents,
        embed,
        model="databricks-gte-large-en",
        dimension=3,
        batch_size=4,
    )

    assert len(updates) == 2
    assert calls == [["same text"]]


@pytest.mark.parametrize(
    ("response", "message"),
    [([], "count"), ([[1.0, 0.0]], "dimension")],
)
def test_embedding_response_shape_is_validated(response, message):
    from wikibricks_remote.search import build_embedding_updates, project_event

    document = project_event(
        _event("session_event_version", {"kind": "user", "content": "text"})
    )[0]

    with pytest.raises(ValueError, match=message):
        build_embedding_updates(
            [document],
            lambda _texts: response,
            model="databricks-gte-large-en",
            dimension=3,
            batch_size=1,
        )


def test_missing_embeddings_are_generated_once_and_reused(postgres_url: str, monkeypatch):
    from wikibricks_remote.search import LakebaseHybridSearch, project_event

    store = PostgresStore(postgres_url)
    documents = [
        project_event(
            _event(
                "session_event_version",
                {"kind": kind, "content": "shared meaning"},
            )
        )[0]
        for kind in ("user", "assistant")
    ]
    search = LakebaseHybridSearch(
        store,
        embedding_model="databricks-gte-large-en",
        embedding_dimension=3,
    )
    monkeypatch.setattr(search, "migrate", lambda: True)
    with store.connection() as conn, conn.transaction():
        conn.execute("CREATE DOMAIN vector AS text")
        conn.execute(
            "CREATE TABLE remote_search_documents ("
            "document_id uuid PRIMARY KEY, replica_id uuid NOT NULL, "
            "archive_event_id uuid NOT NULL, local_sequence bigint NOT NULL, "
            "entity_kind text NOT NULL, entity_id uuid NOT NULL, version_id uuid NOT NULL, "
            "page_path text, title text, document_kind text NOT NULL, chunk_index integer NOT NULL, "
            "content_text text NOT NULL, content_hash text NOT NULL, "
            "embedding_model text, embedding vector, "
            "UNIQUE (archive_event_id, chunk_index))"
        )
        for item in documents:
            conn.execute(
                "INSERT INTO remote_search_documents VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL)",
                (
                    item.document_id,
                    item.replica_id,
                    item.archive_event_id,
                    item.local_sequence,
                    item.entity_kind,
                    item.entity_id,
                    item.version_id,
                    item.page_path,
                    item.title,
                    item.document_kind,
                    item.chunk_index,
                    item.content_text,
                    item.content_hash,
                ),
            )
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    try:
        assert search.embed_missing(embed, maximum=10, batch_size=4) == 2
        assert search.embed_missing(embed, maximum=10, batch_size=4) == 0
        assert calls == [["shared meaning"]]
    finally:
        with store.connection() as conn, conn.transaction():
            conn.execute("DROP TABLE remote_search_documents")
            conn.execute("DROP DOMAIN vector")


def test_rrf_combines_vector_and_keyword_page_ranks_stably():
    from wikibricks_remote.search import reciprocal_rank_fusion

    ranked = reciprocal_rank_fusion(
        ["topics/semantic", "topics/dual", "topics/dual"],
        ["topics/exact", "topics/dual"],
        maximum=3,
    )

    assert [item.path for item in ranked] == [
        "topics/dual",
        "topics/exact",
        "topics/semantic",
    ]
    assert ranked[0].vector_rank == 2
    assert ranked[0].keyword_rank == 2


def test_hybrid_candidates_include_ranked_current_pages(monkeypatch):
    from wikibricks_remote.search import LakebaseHybridSearch

    search = LakebaseHybridSearch(
        None,
        embedding_model="databricks-gte-large-en",
        embedding_dimension=3,
    )
    query_id = uuid4()
    monkeypatch.setattr(search, "migrate", lambda: True)
    monkeypatch.setattr(
        search,
        "_query_documents",
        lambda *_args, **_kwargs: [
            {
                "document_id": query_id,
                "evidence_id": "archive-event:query",
                "page_path": None,
                "content_text": "remember the database",
                "embedding": [1.0, 0.0, 0.0],
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        search,
        "_vector_paths",
        lambda *_args, **_kwargs: ["topics/semantic", "topics/dual"],
        raising=False,
    )
    monkeypatch.setattr(
        search,
        "_keyword_paths",
        lambda *_args, **_kwargs: ["topics/exact", "topics/dual"],
        raising=False,
    )
    monkeypatch.setattr(
        search,
        "_current_pages",
        lambda *_args, **_kwargs: {
            path: {"path": path, "evidence_id": f"archive-event:{path}"}
            for path in ("topics/dual", "topics/exact", "topics/semantic")
        },
        raising=False,
    )

    result = search.candidates(
        uuid4(),
        10,
        [{"evidence_id": "archive-event:query"}],
        maximum_queries=5,
        pages_per_query=3,
    )

    assert [page["path"] for page in result.pages] == [
        "topics/dual",
        "topics/exact",
        "topics/semantic",
    ]
    assert result.similarity_candidates[0]["candidates"][0]["path"] == "topics/dual"


def test_remote_embedder_uses_the_configured_databricks_endpoint():
    from wikibricks_remote.main import _embedder

    calls = []

    def query(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=0, embedding=[1.0, 0.0, 0.0]),
                SimpleNamespace(index=1, embedding=[0.0, 1.0, 0.0]),
            ]
        )

    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(query=query),
    )

    vectors = _embedder(workspace, "databricks-gte-large-en")(["one", "two"])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert calls == [
        {"name": "databricks-gte-large-en", "input": ["one", "two"]}
    ]


def test_candidate_provider_projects_and_embeds_before_search():
    from wikibricks_remote.main import _candidate_provider
    from wikibricks_remote.resources import load_policy
    from wikibricks_remote.search import CandidateSelection

    calls = []
    selection = CandidateSelection("available", (), (), 1, 2, 3)
    search = SimpleNamespace(
        available=lambda: True,
        project=lambda *args, **kwargs: calls.append(("project", args, kwargs)) or 4,
        embed_missing=lambda *args, **kwargs: calls.append(("embed", args, kwargs)) or 5,
        candidates=lambda *args, **kwargs: calls.append(("search", args, kwargs)) or selection,
    )
    replica_id = uuid4()
    evidence = [{"evidence_id": f"archive-event:{uuid4()}"}]

    result = _candidate_provider(search, lambda _texts: [], load_policy())(
        replica_id,
        9,
        evidence,
    )

    assert [call[0] for call in calls] == ["project", "embed", "search"]
    assert result.projected_documents == 4
    assert result.embedded_documents == 5
