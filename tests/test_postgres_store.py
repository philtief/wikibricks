from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.postgres_store import MAX_SEARCH_CHUNK_BYTES, PostgresStore


def _session(events: list[SessionEvent]) -> SessionRecord:
    return SessionRecord(
        harness="omnigent",
        external_id="conversation-1",
        user_id="u",
        agent="codex-native-ui",
        workspace="/tmp/project",
        started_at="2026-08-30T10:00:00Z",
        updated_at="2026-08-30T11:00:00Z",
        events=events,
        metadata={"title": "PostgreSQL memory design"},
    )


def test_migrations_are_repeatable_and_create_required_indexes(postgres_url: str):
    store = PostgresStore(postgres_url)
    store.migrate()
    store.migrate()

    with store.connection() as conn:
        extension = conn.execute(
            "SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'"
        ).fetchone()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            ).fetchall()
        }
        migration_count = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]

    assert extension == ("pg_trgm",)
    assert {"page_search_chunks_vector_idx", "session_search_chunks_vector_idx"} <= indexes
    assert {"pages_path_trgm_idx", "page_versions_title_trgm_idx"} <= indexes
    assert {"pages_active_path_idx", "curation_conflicts_pending_idx"} <= indexes
    assert migration_count == 5


def test_store_exposes_focused_repositories(store: PostgresStore):
    assert store.pages.store is store
    assert store.sessions.store is store
    assert store.search_index.store is store
    assert store.outbox.store is store
    assert store.graph.store is store


def test_page_write_is_transactional_and_keeps_immutable_history(store: PostgresStore):
    assert store.write_page("topics/postgres", "Postgres", {"summary": "one", "body": "alpha"})
    assert store.write_page("topics/postgres", "Postgres 2", {"summary": "two", "body": "beta"})

    page = store.read_page("topics/postgres")
    history = store.history("topics/postgres")

    assert page is not None
    assert page["version"] == 2
    assert page["content"]["body"] == "beta"
    assert [row["version"] for row in history] == [2, 1]
    assert store.outbox_count() == 2


def test_page_write_rolls_back_every_side_effect(store: PostgresStore):
    def fail(stage: str) -> None:
        if stage == "before_commit":
            raise RuntimeError("injected failure")

    failing = PostgresStore(store.database_url, failpoint=fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        failing.write_page("topics/rollback", "Rollback", {"summary": "s", "body": "b"})

    assert store.read_page("topics/rollback") is None
    assert store.outbox_count() == 0


def test_full_text_search_uses_bounded_chunks(store: PostgresStore):
    body = ("paragraph about lakebase and transactional memory\n\n" * 5000).strip()
    store.write_page("topics/lakebase", "Lakebase archive", {"summary": "archive", "body": body})

    hits = store.search("transactional memory")
    with store.connection() as conn:
        largest = conn.execute(
            "SELECT max(octet_length(substring(v.content_text FROM c.start_offset + 1 "
            "FOR c.end_offset - c.start_offset))) "
            "FROM page_search_chunks c JOIN page_versions v USING (version_id)"
        ).fetchone()[0]

    assert hits[0]["path"] == "topics/lakebase"
    assert largest <= MAX_SEARCH_CHUNK_BYTES


def test_session_reimport_is_idempotent_and_changed_events_get_history(store: PostgresStore):
    initial = _session(
        [
            SessionEvent("0", "user", "question"),
            SessionEvent("1", "assistant", "first answer"),
        ]
    )
    first = store.ingest_session(initial)
    second = store.ingest_session(initial)
    changed = store.ingest_session(
        _session(
            [
                SessionEvent("0", "user", "question"),
                SessionEvent("1", "assistant", "corrected answer"),
                SessionEvent("2", "tool_result", "new result"),
            ]
        )
    )

    assert (first.created_events, first.updated_events, first.unchanged_events) == (2, 0, 0)
    assert (second.created_events, second.updated_events, second.unchanged_events) == (0, 0, 2)
    assert (changed.created_events, changed.updated_events, changed.unchanged_events) == (1, 1, 1)
    assert store.session_event_versions("omnigent", "conversation-1", "1") == 2
    assert store.outbox_count() == 4


def test_25mb_tool_result_round_trips_and_searches_without_tsvector_limit(store: PostgresStore):
    marker = "WIKIBRICKS_UNIQUE_LONG_SESSION_MARKER"
    content = (("0123456789abcdef" * 1024) + "\n\n") * 1600 + marker
    assert len(content.encode("utf-8")) >= 25 * 1024 * 1024
    record = _session([SessionEvent("large", "tool_result", content)])

    store.ingest_session(record)
    restored = store.read_session_events("omnigent", "conversation-1")
    hits = store.search(marker)

    assert restored[0].content == content
    assert hits[0]["path"].startswith("omnigent-sessions/")
    with store.connection() as conn:
        largest = conn.execute(
            "SELECT max(end_offset - start_offset) FROM session_search_chunks"
        ).fetchone()[0]
    assert largest <= MAX_SEARCH_CHUNK_BYTES


def test_concurrent_migrations_and_session_writes_are_safe(postgres_url: str):
    stores = [PostgresStore(postgres_url), PostgresStore(postgres_url)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda item: item.migrate(), stores))
    stores[0].clear_all()

    records = [
        SessionRecord(
            harness="generic",
            external_id=f"session-{index}",
            user_id="u",
            events=[SessionEvent("0", "user", f"question {index}")],
        )
        for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: pair[0].ingest_session(pair[1]), zip(stores, records)))

    assert len(stores[0].list_pages()) == 2
    assert stores[0].outbox_count() == 2


def test_search_indexes_are_queryable_by_postgresql_planner(store: PostgresStore):
    store.write_page(
        "topics/indexed-lakebase",
        "Indexed Lakebase",
        {"summary": "searchable", "body": "distinctive archive phrase"},
    )
    with store.connection() as conn:
        conn.execute("SET enable_seqscan = off")
        fts_plan = "\n".join(
            row[0]
            for row in conn.execute(
                "EXPLAIN SELECT * FROM page_search_chunks "
                "WHERE search_vector @@ websearch_to_tsquery('simple', 'distinctive archive')"
            )
        )
        trigram_plan = "\n".join(
            row[0]
            for row in conn.execute(
                "EXPLAIN SELECT * FROM pages WHERE path LIKE '%indexed-lakebase%'"
            )
        )

    assert "page_search_chunks_vector_idx" in fts_plan
    assert "pages_path_trgm_idx" in trigram_plan
