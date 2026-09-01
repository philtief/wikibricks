from __future__ import annotations

from uuid import UUID

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks.cli import main as cli_main
from wikibricks.client import WikiClient
from wikibricks.curation import (
    apply_run,
    build_manifest,
    create_patch,
    get_or_create_replica_id,
    list_conflicts,
    publish_manifest,
)
from wikibricks.migrate_postgres import migrate_postgres
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.postgres_store import PostgresStore
from wikibricks.storage.sqlite_store import SQLiteStore


def _proposal(body: str) -> dict:
    return {
        "title": "Migration",
        "page_type": "concept",
        "content": {"summary": "Migration", "body": body},
        "content_text": f"Migration {body}",
        "tags": ["curated"],
        "source_ids": ["session:migration"],
        "parent_id": None,
        "chunk_index": None,
    }


def test_postgres_migration_preserves_memory_history_and_pending_work(
    postgres_url: str,
    tmp_path,
):
    source = PostgresStore(postgres_url)
    source.migrate()
    source.clear_all()
    source.write_page(
        "topics/migration",
        "Migration",
        {"summary": "Migration", "body": "base"},
        source_ids=["session:migration"],
    )
    base = source.current_page_state("topics/migration")
    source.ingest_session(
        SessionRecord(
            harness="omnigent",
            external_id="migration-session",
            user_id="philipp",
            agent="codex",
            events=[SessionEvent("1", "user", "preserve this session")],
        )
    )
    patch = create_patch(
        operation="update_page",
        path="topics/migration",
        proposal=_proposal("remote"),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:migration"],
        reason="Exercise migration of unresolved work.",
    )
    manifest = build_manifest(
        replica_id=get_or_create_replica_id(source),
        input_watermark=1,
        patches=[patch],
    )
    publish_manifest(source, manifest)
    source.write_page(
        "topics/migration",
        "Migration",
        {"summary": "Migration", "body": "local"},
        source_ids=["session:migration"],
    )
    assert apply_run(source, UUID(manifest["run_id"]))["counts"] == {"conflict": 1}

    destination_path = tmp_path / "wikibricks.db"
    report = migrate_postgres(postgres_url, destination_path)
    destination = SQLiteStore(destination_path)

    assert report.verified is True
    assert destination.current_page_state("topics/migration")["page_id"] == base["page_id"]
    assert destination.read_page("topics/migration")["content"]["body"] == "local"
    assert len(destination.history("topics/migration")) == 2
    assert destination.read_session_events("omnigent", "migration-session")[0].content == (
        "preserve this session"
    )
    assert [item["conflict_id"] for item in list_conflicts(destination)] == [
        item["conflict_id"] for item in list_conflicts(source)
    ]
    with source.connection() as source_conn, destination.connection() as destination_conn:
        source_outbox = {
            str(row[0])
            for row in source_conn.execute(
                "SELECT event_id FROM sync_outbox WHERE acknowledged_at IS NULL"
            )
        }
        destination_outbox = {
            row[0]
            for row in destination_conn.execute(
                "SELECT event_id FROM sync_outbox WHERE acknowledged_at IS NULL"
            )
        }
    assert destination_outbox == source_outbox


def test_cli_migrates_postgres_into_a_new_sqlite_file(postgres_url: str, tmp_path):
    source = PostgresStore(postgres_url)
    source.migrate()
    source.clear_all()
    source.write_page("topics/cli-migration", "CLI", {"summary": "CLI", "body": "copy"})
    destination = tmp_path / "from-postgres.db"

    assert cli_main(
        ["migrate-postgres", "--source-url", postgres_url, str(destination)]
    ) == 0
    assert SQLiteStore(destination).read_page("topics/cli-migration")["content"]["body"] == (
        "copy"
    )


def test_keyword_postgres_dsn_is_never_treated_as_a_local_path(
    postgres_url: str,
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    dsn = make_conninfo(**conninfo_to_dict(postgres_url))

    client = WikiClient(dsn)

    assert client.database_url == dsn
    assert list(tmp_path.iterdir()) == []
