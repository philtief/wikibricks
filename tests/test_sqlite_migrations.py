"""Regression tests for forward-only SQLite migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from wikibricks.storage.sqlite_store import SQLiteStore


def _build_pre633d277_db(db_path: Path) -> None:
    """
    Simulate a DB created before commit 633d277.

    That commit retroactively added background_leases and sync_state to
    0001_core.sql. A real pre-633d277 DB has schema_migrations rows for 0001
    and 0002 but is missing those two tables.

    Strategy: run the current migrate() (which creates all three migrations on
    a fresh DB), then DROP the tables that were added in 633d277, and remove
    the 0003 row so migrate() sees a "legacy" DB with only 0001 + 0002 applied.
    """
    store = SQLiteStore(db_path)
    # First, ensure 0003 does not exist yet (pre-fix: only 0001 and 0002)
    store.migrate()  # creates all currently-shipped migrations

    conn = sqlite3.connect(db_path)
    try:
        # Remove the late-added tables that 633d277 put in 0001.
        conn.execute("DROP TABLE IF EXISTS sync_state")
        conn.execute("DROP TABLE IF EXISTS background_leases")
        # Delete the 0003 migration record so migrate() will re-run it.
        conn.execute(
            "DELETE FROM schema_migrations WHERE name NOT IN ('0001_core.sql', '0002_sync.sql')"
        )
        conn.commit()
    finally:
        conn.close()


def test_migrate_recovers_missing_sync_state_and_background_leases(tmp_path: Path) -> None:
    """
    migrate() must add sync_state and background_leases to a legacy DB that
    already has 0001 and 0002 recorded in schema_migrations.
    """
    db_path = tmp_path / "legacy.db"
    _build_pre633d277_db(db_path)

    store = SQLiteStore(db_path)
    store.migrate()

    # get_sync_cursor must not raise OperationalError: no such table: sync_state
    cursor = store.get_sync_cursor("lakebase")
    assert cursor == {}

    store.set_sync_cursor("lakebase", {"after": 42})
    assert store.get_sync_cursor("lakebase") == {"after": 42}

    # background_leases must also be present
    assert store.acquire_lease("bg-test", "worker", 60)


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
