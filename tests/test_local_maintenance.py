from __future__ import annotations

from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks.maintenance import (
    backup_database,
    check_database,
    database_fingerprint,
    initialize_database,
    restore_database,
    vacuum_database,
)
from wikibricks.postgres_store import PostgresStore


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
    before = database_fingerprint(postgres_url)
    backup = tmp_path / "wikibricks.dump"

    backup_database(postgres_url, backup)
    restored_url = _database_url(postgres_url, "wikibricks_restore_test")
    restore_database(backup, restored_url)
    after = database_fingerprint(restored_url)

    assert backup.stat().st_size > 0
    assert after == before
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
