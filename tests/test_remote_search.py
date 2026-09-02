from __future__ import annotations

from uuid import uuid4

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
