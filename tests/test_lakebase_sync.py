from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from wikibricks.curation import get_or_create_replica_id
from wikibricks.maintenance import initialize_database
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.postgres_store import PostgresStore
from wikibricks.remote.lakebase import (
    LakebaseTarget,
    build_batch,
    pull_curated_snapshot,
    sync_to_archive,
)
from wikibricks.storage.sqlite_store import SQLiteStore


def _database_url(base_url: str, database: str) -> str:
    params = conninfo_to_dict(base_url)
    params["dbname"] = database
    return make_conninfo(**params)


def test_lakebase_adapter_is_optional_and_lazy():
    from wikibricks.remote.lakebase import LakebaseTarget

    target = LakebaseTarget(
        "project",
        "production",
        "primary",
        "wikibricks",
        "profile",
    )

    assert target.database == "wikibricks"


@pytest.fixture(scope="module")
def archive_url(postgres_url: str) -> str:
    target = _database_url(postgres_url, "wikibricks_archive_sync_test")
    initialize_database(target)
    return target


def _seed_local(store: PostgresStore | SQLiteStore) -> None:
    store.migrate()
    if isinstance(store, PostgresStore):
        store.clear_all()
    store.write_page(
        "topics/monthly-archive",
        "Monthly archive",
        {"summary": "remote", "body": "copy this page"},
    )
    store.ingest_session(
        SessionRecord(
            harness="omnigent",
            external_id="archive-session",
            user_id="u",
            agent="codex",
            events=[SessionEvent("0", "tool_result", "large session content")],
        )
    )


def test_batch_manifest_is_deterministic_and_references_immutable_rows(
    tmp_path,
):
    store = SQLiteStore(tmp_path / "local.db")
    store.migrate()
    store.write_page(
        "topics/monthly-archive",
        "Monthly archive",
        {"summary": "remote", "body": "copy this page"},
    )
    store.ingest_session(
        SessionRecord(
            harness="omnigent",
            external_id="archive-session",
            user_id="u",
            agent="codex",
            events=[SessionEvent("0", "tool_result", "large session content")],
        )
    )
    replica_id = get_or_create_replica_id(store)

    first = build_batch(store, limit=100)
    second = build_batch(store, limit=100)

    assert first.manifest == second.manifest
    assert first.manifest["replica_id"] == str(replica_id)
    assert first.manifest["event_count"] == 2
    assert len(first.manifest["digest"]) == 64
    assert {event.entity_kind for event in first.events} == {
        "page_version",
        "session_event_version",
    }
    assert {event.replica_id for event in first.events} == {replica_id}
    assert all("payload" not in row for row in store.pending_outbox())


def test_retry_after_remote_commit_is_idempotent_and_acknowledges_locally(
    tmp_path,
    archive_url: str,
):
    local = SQLiteStore(tmp_path / "retry.db")
    remote = PostgresStore(archive_url)
    _seed_local(local)
    remote.migrate()
    remote.clear_all()

    def fail(stage: str) -> None:
        if stage == "after_remote_commit":
            raise RuntimeError("simulated disconnect")

    with pytest.raises(RuntimeError, match="simulated disconnect"):
        sync_to_archive(local, archive_url, failpoint=fail)
    assert local.outbox_count() == 2

    result = sync_to_archive(local, archive_url)
    repeated = sync_to_archive(local, archive_url)

    assert result["acknowledged"] == 2
    assert repeated == {"status": "idle", "acknowledged": 0}
    assert local.outbox_count() == 0
    with remote.connection() as conn:
        assert conn.execute("SELECT count(*) FROM archive_batches").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM archive_events").fetchone()[0] == 2
        batch_replica = conn.execute(
            "SELECT replica_id FROM archive_batches"
        ).fetchone()[0]
        event_replicas = {
            row[0] for row in conn.execute("SELECT replica_id FROM archive_events")
        }
    assert batch_replica == get_or_create_replica_id(local)
    assert event_replicas == {batch_replica}


def test_drain_stops_at_the_batch_bound_and_can_resume(
    tmp_path,
    archive_url: str,
):
    local = SQLiteStore(tmp_path / "drain.db")
    remote = PostgresStore(archive_url)
    local.migrate()
    remote.migrate()
    remote.clear_all()
    for number in range(5):
        local.write_page(
            f"topics/drain-{number}",
            f"Drain {number}",
            {"summary": "bounded drain", "body": str(number)},
        )

    partial = sync_to_archive(
        local,
        archive_url,
        limit=2,
        drain=True,
        max_batches=2,
    )
    resumed = sync_to_archive(
        local,
        archive_url,
        limit=2,
        drain=True,
        max_batches=2,
    )

    assert partial == {
        "status": "partial",
        "batches": 2,
        "acknowledged": 4,
        "remaining": 1,
    }
    assert resumed == {
        "status": "drained",
        "batches": 1,
        "acknowledged": 1,
        "remaining": 0,
    }
    with remote.connection() as conn:
        assert conn.execute("SELECT count(*) FROM archive_batches").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM archive_events").fetchone()[0] == 5


def test_curated_snapshot_is_versioned_and_never_overwrites_local_pages(
    postgres_url: str,
    archive_url: str,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(archive_url)
    local.migrate()
    remote.migrate()
    local.clear_all()
    remote.clear_all()
    local.write_page("topics/conflict", "Local", {"summary": "local", "body": "keep"})
    with remote.connection() as conn, conn.transaction():
        rows = [
            (
                "topics/conflict",
                "Remote conflict",
                Jsonb({"summary": "remote", "body": "replace"}),
                "replace",
                ["curated"],
                7,
                "a" * 64,
            ),
            (
                "topics/archive-only",
                "Archive only",
                Jsonb({"summary": "archive", "body": "read only"}),
                "read only",
                ["curated"],
                7,
                "b" * 64,
            ),
        ]
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO curated_pages "
                "(path, title, content, content_text, tags, snapshot_version, content_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                rows,
            )

    pulled = pull_curated_snapshot(local, archive_url)
    stale = pull_curated_snapshot(local, archive_url)

    assert pulled == 1
    assert stale == 0
    assert local.read_page("topics/conflict")["title"] == "Local"
    assert local.read_page("topics/archive-only")["title"] == "Archive only"
    assert local.search("read only")[0]["path"] == "topics/archive-only"


def test_lakebase_target_requires_an_explicit_profile_before_sdk_use():
    target = LakebaseTarget(
        project="wikibricks",
        branch="production",
        endpoint="primary",
        database="wikibricks",
        profile=None,
    )

    with pytest.raises(ValueError, match="Databricks profile is required"):
        target.fresh_database_url()
