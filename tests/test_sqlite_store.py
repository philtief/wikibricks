from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.storage.content import MAX_SEARCH_CHUNK_BYTES
from wikibricks.storage.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    result = SQLiteStore(tmp_path / "memory.db")
    result.migrate()
    return result


def test_sqlite_defaults_and_migrations_are_idempotent(tmp_path: Path):
    store = SQLiteStore(tmp_path / "memory.db")
    store.migrate()
    with store.connection() as conn:
        first_migration_count = conn.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()[0]
    store.migrate()

    with store.connection() as conn:
        pragmas = (
            conn.execute("PRAGMA journal_mode").fetchone()[0],
            conn.execute("PRAGMA foreign_keys").fetchone()[0],
            conn.execute("PRAGMA busy_timeout").fetchone()[0],
        )
        migrations = conn.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()[0]

    assert pragmas == ("wal", 1, 5000)
    assert migrations == first_migration_count
    assert migrations >= 1


def test_page_history_and_outbox_commit_atomically(store: SQLiteStore):
    store.write_page(
        "topics/sqlite",
        "SQLite",
        {"summary": "one", "body": "alpha"},
    )
    store.write_page(
        "topics/sqlite",
        "SQLite",
        {"summary": "two", "body": "beta"},
    )

    assert [row["version"] for row in store.history("topics/sqlite")] == [2, 1]
    assert store.outbox_count() == 2


def _session(events: list[SessionEvent], *, external_id: str = "conversation-1"):
    return SessionRecord(
        harness="omnigent",
        external_id=external_id,
        user_id="philipp",
        agent="codex",
        workspace="/tmp/project",
        events=events,
        metadata={"title": "Shared memory"},
    )


def test_session_reimport_is_idempotent_and_corrections_create_versions(
    store: SQLiteStore,
):
    initial = _session(
        [
            SessionEvent("user-1", "user", "question"),
            SessionEvent("assistant-1", "assistant", "first answer"),
        ]
    )
    first = store.ingest_session(initial)
    second = store.ingest_session(initial)
    changed = store.ingest_session(
        _session(
            [
                SessionEvent("user-1", "user", "question"),
                SessionEvent("assistant-1", "assistant", "corrected answer"),
                SessionEvent("tool-1", "tool_result", "new result"),
            ]
        )
    )

    assert (first.created_events, first.updated_events, first.unchanged_events) == (
        2,
        0,
        0,
    )
    assert (second.created_events, second.updated_events, second.unchanged_events) == (
        0,
        0,
        2,
    )
    assert (changed.created_events, changed.updated_events, changed.unchanged_events) == (
        1,
        1,
        1,
    )
    assert store.session_event_versions("omnigent", "conversation-1", "assistant-1") == 2


def test_25mb_session_event_round_trips_and_searches(store: SQLiteStore):
    marker = "WIKIBRICKS_UNIQUE_LONG_SESSION_MARKER"
    content = ("0123456789abcdef" * 1638400) + marker
    assert len(content.encode()) >= 25 * 1024 * 1024

    store.ingest_session(_session([SessionEvent("large", "tool_result", content)]))

    assert store.read_session_events("omnigent", "conversation-1")[0].content == content
    assert store.search(marker)[0]["path"].startswith("omnigent-sessions/")
    with store.connection() as conn:
        largest = conn.execute(
            "SELECT max(length(CAST(chunk_text AS BLOB))) FROM session_search_chunks"
        ).fetchone()[0]
    assert largest <= MAX_SEARCH_CHUNK_BYTES


def test_two_store_instances_write_without_duplicate_versions(tmp_path: Path):
    path = tmp_path / "concurrent.db"
    stores = [SQLiteStore(path), SQLiteStore(path)]
    stores[0].migrate()
    records = [
        _session(
            [SessionEvent("user-1", "user", f"question {index}")],
            external_id=f"conversation-{index}",
        )
        for index in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: pair[0].ingest_session(pair[1]), zip(stores, records)))

    assert len(stores[0].list_pages()) == 2
    assert stores[0].outbox_count() == 2


def test_only_one_process_holds_the_background_lease(store: SQLiteStore):
    assert store.acquire_lease("maintenance", "worker-a", 60, now=100)
    assert not store.acquire_lease("maintenance", "worker-b", 60, now=101)
    assert store.acquire_lease("maintenance", "worker-b", 60, now=161)
