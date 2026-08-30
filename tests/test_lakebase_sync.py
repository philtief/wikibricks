from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from wikibricks.maintenance import initialize_database
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.postgres_store import PostgresStore
from wikibricks_databricks.lakebase_sync import (
    LakebaseTarget,
    build_batch,
    pull_curated_snapshot,
    sync_to_archive,
)


def _database_url(base_url: str, database: str) -> str:
    params = conninfo_to_dict(base_url)
    params["dbname"] = database
    return make_conninfo(**params)


@pytest.fixture(scope="module")
def archive_url(postgres_url: str) -> str:
    target = _database_url(postgres_url, "wikibricks_archive_sync_test")
    initialize_database(target)
    return target


def _seed_local(store: PostgresStore) -> None:
    store.migrate()
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
    postgres_url: str,
):
    store = PostgresStore(postgres_url)
    _seed_local(store)

    first = build_batch(store, limit=100)
    second = build_batch(store, limit=100)

    assert first.manifest == second.manifest
    assert first.manifest["event_count"] == 2
    assert len(first.manifest["digest"]) == 64
    assert {event.entity_kind for event in first.events} == {
        "page_version",
        "session_event_version",
    }
    assert all("payload" not in row for row in store.pending_outbox())


def test_retry_after_remote_commit_is_idempotent_and_acknowledges_locally(
    postgres_url: str,
    archive_url: str,
):
    local = PostgresStore(postgres_url)
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


def test_curated_snapshot_is_versioned_and_never_overwrites_local_pages(
    postgres_url: str,
    archive_url: str,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(archive_url)
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
