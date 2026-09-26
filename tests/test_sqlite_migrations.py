"""Regression tests for forward-only SQLite migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from wikibricks.storage.sqlite_store import SQLiteStore


def _legacy_tables() -> list[str]:
    """Tables missing from a DB applied between 5893121 and b7ea656."""
    return ["links", "sources", "operations", "background_leases", "sync_state"]


def _build_post5893121_db(db_path: Path) -> None:
    """
    Simulate the live DB that applied 0001_core.sql between commits 5893121
    (15:21 +02:00) and b7ea656 (15:28 +02:00) on 2026-09-01.

    At that moment 0001 contained only the core tables from 5893121. The three
    tables added by b7ea656 (links, sources, operations) and the two added by
    633d277 (background_leases, sync_state) were not yet present.

    Strategy: run the current full migrate() on a fresh DB (which creates all
    migrations including 0003), then DROP the 5 late-added tables and remove the
    0003 row from schema_migrations so that migrate() sees a legacy DB with only
    0001 and 0002 recorded.
    """
    store = SQLiteStore(db_path)
    store.migrate()

    conn = sqlite3.connect(db_path)
    try:
        for table in _legacy_tables():
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute(
            "DELETE FROM schema_migrations WHERE name NOT IN ('0001_core.sql', '0002_sync.sql')"
        )
        conn.commit()
    finally:
        conn.close()


def _schema_objects(db_path: Path) -> set[tuple[str, str]]:
    """Return (type, name) for all tables and indexes, excluding FTS shadow tables."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'index') "
            "AND name NOT LIKE '%_fts%' "
            "AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        return {(row[0], row[1]) for row in rows}
    finally:
        conn.close()


def test_migrate_recovers_all_five_late_added_tables(tmp_path: Path) -> None:
    """
    migrate() must add all 5 tables missing from the live legacy DB.
    """
    db_path = tmp_path / "legacy.db"
    _build_post5893121_db(db_path)

    store = SQLiteStore(db_path)
    store.migrate()

    # sync_state: get_sync_cursor must not raise OperationalError
    cursor = store.get_sync_cursor("lakebase")
    assert cursor == {}
    store.set_sync_cursor("lakebase", {"after": 42})
    assert store.get_sync_cursor("lakebase") == {"after": 42}

    # background_leases: acquire_lease must succeed
    assert store.acquire_lease("bg-test", "worker", 60)

    # operations: log must succeed
    store.log("test_op", path="topics/test", details={"x": 1})

    # sources: ingest_source must succeed
    source_id = store.ingest_source("https://example.com", title="Test source")
    assert source_id

    # links: commit_edges requires two pages; write them first
    store.write_page("topics/a", "Page A", {"summary": "A", "body": ""})
    store.write_page("topics/b", "Page B", {"summary": "B", "body": ""})
    written = store.commit_edges(
        [{"source_path": "topics/a", "target_path": "topics/b", "link_type": "related"}]
    )
    assert written == 1


def test_upgraded_schema_matches_fresh_schema(tmp_path: Path) -> None:
    """
    After migrate() runs on the post-5893121 legacy schema the set of tables
    and indexes (excluding FTS shadow tables) must be identical to a fresh DB.
    This generic check would have caught the 5-table gap.
    """
    legacy_path = tmp_path / "legacy.db"
    fresh_path = tmp_path / "fresh.db"

    _build_post5893121_db(legacy_path)
    SQLiteStore(legacy_path).migrate()

    SQLiteStore(fresh_path).migrate()

    assert _schema_objects(legacy_path) == _schema_objects(fresh_path)


def test_migrate_is_idempotent_on_fresh_db(tmp_path: Path) -> None:
    """Calling migrate() twice on a new DB must not raise or duplicate rows."""
    store = SQLiteStore(tmp_path / "fresh.db")
    store.migrate()

    with store.connection() as conn:
        count_before = conn.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()[0]

    store.migrate()

    with store.connection() as conn:
        count_after = conn.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()[0]

    assert count_after == count_before
    assert count_after >= 3
