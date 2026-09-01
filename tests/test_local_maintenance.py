from __future__ import annotations

from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks import WikiClient
from wikibricks.curation_sync import (
    build_manifest,
    create_patch,
    get_or_create_replica_id,
    publish_manifest,
)
from wikibricks.maintenance import (
    backup_database,
    check_database,
    curate_database,
    database_fingerprint,
    initialize_database,
    restore_database,
    vacuum_database,
)
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.postgres_store import PostgresStore
from wikibricks.storage.sqlite_store import SQLiteStore


def test_sqlite_online_backup_restores_a_consistent_database(tmp_path: Path):
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    client = WikiClient(source)
    client.write_page(
        "topics/backup",
        "Backup",
        {"summary": "safe", "body": "copy"},
    )

    backup = backup_database(source, tmp_path / "backup.db")
    restore_database(backup, restored)

    assert WikiClient(restored).read_page("topics/backup")["content"]["body"] == "copy"
    assert check_database(restored)["ok"] is True


def test_sqlite_curation_repairs_search_and_reports_hygiene(tmp_path: Path):
    database_path = tmp_path / "memory.db"
    store = SQLiteStore(database_path)
    store.migrate()
    content = {"summary": "same", "body": "shared exact content"}
    store.write_page("topics/duplicate-a", "Duplicate", content)
    store.write_page("topics/duplicate-b", "Duplicate", content)
    store.ingest_session(
        SessionRecord(
            harness="omnigent",
            external_id="repair-search",
            user_id="u",
            events=[SessionEvent("0", "tool_result", "search chunk repair marker")],
        )
    )
    with store.connection(write=True) as conn:
        conn.execute("DELETE FROM page_search_fts")
        conn.execute("DELETE FROM page_search_chunks")
        conn.execute("DELETE FROM session_search_fts")
        conn.execute("DELETE FROM session_search_chunks")

    result = curate_database(database_path)

    assert result["ok"] is True
    assert result["repaired_page_search_versions"] == 2
    assert result["repaired_session_search_versions"] == 1
    assert result["duplicate_page_groups"][0]["count"] == 2
    assert WikiClient(database_path).search("repair marker")[0]["page_type"] == "session"


def _database_url(base_url: str, database: str) -> str:
    params = conninfo_to_dict(base_url)
    params["dbname"] = database
    return make_conninfo(**params)


def test_initialize_creates_a_dedicated_database(postgres_url: str):
    target = _database_url(postgres_url, "wikibricks_init_test")

    created = initialize_database(target)
    repeated = initialize_database(target)

    assert created is True
    assert repeated is False
    assert check_database(target)["ok"] is True


def test_backup_restore_preserves_all_local_content(
    postgres_url: str,
    tmp_path: Path,
):
    store = PostgresStore(postgres_url)
    store.migrate()
    store.clear_all()
    store.write_page(
        "topics/backup",
        "Backup",
        {"summary": "durable", "body": "restore me"},
    )
    patch = create_patch(
        operation="create_page",
        path="topics/restored-patch",
        proposal={
            "title": "Restored patch",
            "page_type": "concept",
            "content": {"summary": "Restored patch", "body": "pending"},
            "content_text": "Restored patch pending",
            "tags": [],
            "source_ids": ["session:backup"],
            "parent_id": None,
            "chunk_index": None,
        },
        evidence_ids=["session:backup"],
        reason="Verify pending curation state survives restore.",
    )
    publish_manifest(
        store,
        build_manifest(
            replica_id=get_or_create_replica_id(store),
            input_watermark=1,
            patches=[patch],
        ),
    )
    before = database_fingerprint(postgres_url)
    backup = tmp_path / "wikibricks.dump"

    backup_database(postgres_url, backup)
    restored_url = _database_url(postgres_url, "wikibricks_restore_test")
    restore_database(backup, restored_url)
    after = database_fingerprint(restored_url)

    assert backup.stat().st_size > 0
    assert after == before
    assert after["curation_runs"]["count"] == 1
    assert after["curation_patches"]["count"] == 1
    assert PostgresStore(restored_url).read_page("topics/backup")["content"]["body"] == "restore me"


def test_check_and_vacuum_report_a_healthy_database(postgres_url: str):
    store = PostgresStore(postgres_url)
    store.migrate()

    report = check_database(postgres_url)
    vacuum_database(postgres_url)

    assert report["ok"] is True
    assert report["pg_trgm"] is True
    assert report["broken_page_pointers"] == 0
    assert report["broken_session_pointers"] == 0


def test_local_curation_repairs_search_and_reports_wiki_hygiene(postgres_url: str):
    store = PostgresStore(postgres_url)
    store.migrate()
    store.clear_all()
    content = {"summary": "same", "body": "shared exact content"}
    store.write_page("topics/duplicate-a", "Duplicate", content)
    store.write_page("topics/duplicate-b", "Duplicate", content)
    store.ingest_session(
        SessionRecord(
            harness="test-harness",
            external_id="repair-search",
            user_id="u",
            events=[SessionEvent("0", "tool_result", "search chunk repair marker")],
        )
    )
    with store.connection() as conn, conn.transaction():
        conn.execute("DELETE FROM page_search_chunks")
        conn.execute("DELETE FROM session_search_chunks")

    first = curate_database(postgres_url)
    second = curate_database(postgres_url)

    assert first["repaired_page_search_versions"] == 2
    assert first["repaired_session_search_versions"] == 1
    assert first["duplicate_page_groups"] == [
        {"paths": ["topics/duplicate-a", "topics/duplicate-b"], "count": 2}
    ]
    assert set(first["orphan_pages"]) == {"topics/duplicate-a", "topics/duplicate-b"}
    assert second["repaired_page_search_versions"] == 0
    assert second["repaired_session_search_versions"] == 0
    assert len(store.history("_meta/index")) == 1
    index_body = store.read_page("_meta/index")["content"]["body"]
    assert "topics/duplicate-a" in index_body
    assert "test-harness-sessions/" not in index_body
    assert store.search("search chunk repair marker")[0]["page_type"] == "session"


def test_local_curation_prunes_only_old_fully_archived_sessions(postgres_url: str):
    store = PostgresStore(postgres_url)
    store.migrate()
    store.clear_all()
    for external_id in ("archived", "pending"):
        store.ingest_session(
            SessionRecord(
                harness="test-harness",
                external_id=external_id,
                user_id="u",
                events=[SessionEvent("0", "user", external_id)],
            )
        )
    with store.connection() as conn, conn.transaction():
        conn.execute("UPDATE sessions SET updated_at = now() - interval '90 days'")
        conn.execute(
            "UPDATE sync_outbox SET acknowledged_at = now() "
            "WHERE entity_kind = 'session_event_version' AND version_id IN ("
            "SELECT v.version_id FROM session_event_versions v "
            "JOIN session_events e ON e.event_id = v.event_id "
            "JOIN sessions s ON s.session_id = e.session_id "
            "WHERE s.external_id = 'archived')"
        )

    result = curate_database(
        postgres_url,
        prune_archived_sessions_after_days=30,
    )

    assert result["pruned_sessions"] == 1
    assert store.read_page("test-harness-sessions/u/1970/01/01/archived") is None
    assert store.read_page("test-harness-sessions/u/1970/01/01/pending") is not None
