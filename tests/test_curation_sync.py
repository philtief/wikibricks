from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks.cli import main as cli_main
from wikibricks.curation_sync import (
    apply_run,
    build_manifest,
    create_patch,
    get_or_create_replica_id,
    list_conflicts,
    plan_run,
    publish_manifest,
    pull_manifests,
    resolve_conflict,
)
from wikibricks.maintenance import curate_database, initialize_database
from wikibricks.postgres_store import PostgresStore
from wikibricks.remote.lakebase import pull_curation_patches, sync_to_archive
from wikibricks.storage.sqlite_store import SQLiteStore


def _database_url(base_url: str, database: str) -> str:
    params = conninfo_to_dict(base_url)
    params["dbname"] = database
    return make_conninfo(**params)


def test_curation_package_supports_stable_public_imports():
    from wikibricks.curation import (
        apply_run,
        build_manifest,
        create_patch,
        get_or_create_replica_id,
        list_conflicts,
        plan_run,
        publish_manifest,
        pull_manifests,
        resolve_conflict,
        validate_manifest,
    )

    assert all(
        callable(operation)
        for operation in (
            apply_run,
            build_manifest,
            create_patch,
            get_or_create_replica_id,
            list_conflicts,
            plan_run,
            publish_manifest,
            pull_manifests,
            resolve_conflict,
            validate_manifest,
        )
    )


@pytest.fixture(scope="module")
def curation_remote_url(postgres_url: str) -> str:
    target = _database_url(postgres_url, "wikibricks_curation_remote_test")
    initialize_database(target)
    return target


def _proposal(title: str, body: str, *, tags: list[str] | None = None) -> dict:
    return {
        "title": title,
        "page_type": "concept",
        "content": {"summary": title, "body": body},
        "content_text": f"{title} {body}",
        "tags": tags or [],
        "source_ids": ["session:test"],
        "parent_id": None,
        "chunk_index": None,
    }


def _reset(*stores: PostgresStore) -> None:
    for store in stores:
        store.migrate()
        store.clear_all()


def _publish_and_pull(
    local: PostgresStore,
    remote: PostgresStore,
    patches: list[dict],
    *,
    input_watermark: int = 1,
) -> dict:
    replica_id = get_or_create_replica_id(local)
    manifest = build_manifest(
        replica_id=replica_id,
        input_watermark=input_watermark,
        patches=patches,
    )
    assert publish_manifest(remote, manifest) is True
    assert publish_manifest(remote, manifest) is False
    assert pull_manifests(local, remote, replica_id=replica_id) == {
        "received_runs": 1,
        "received_patches": len(patches),
    }
    assert pull_manifests(local, remote, replica_id=replica_id) == {
        "received_runs": 0,
        "received_patches": 0,
    }
    return manifest


def test_remote_update_becomes_a_versioned_local_write_and_is_idempotent(
    tmp_path,
    curation_remote_url: str,
):
    local = SQLiteStore(tmp_path / "local.db")
    remote = PostgresStore(curation_remote_url)
    local.migrate()
    _reset(remote)
    local.write_page(
        "topics/local-memory",
        "Local memory",
        {"summary": "Local memory", "body": "old"},
        source_ids=["session:test"],
    )
    base = local.current_page_state("topics/local-memory")
    patch = create_patch(
        operation="update_page",
        path="topics/local-memory",
        proposal=_proposal("Local memory", "curated remotely", tags=["curated"]),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:test"],
        reason="Consolidate the archived evidence.",
    )
    manifest = _publish_and_pull(local, remote, [patch])

    planned = plan_run(local, UUID(manifest["run_id"]))
    applied = apply_run(local, UUID(manifest["run_id"]))
    repeated = apply_run(local, UUID(manifest["run_id"]))

    assert planned["counts"] == {"applicable": 1}
    assert applied["counts"] == {"applied": 1}
    assert repeated["counts"] == {"already_processed": 1}
    assert local.read_page("topics/local-memory")["content"]["body"] == "curated remotely"
    assert len(local.history("topics/local-memory")) == 2
    with local.connection() as conn:
        version = conn.execute(
            "SELECT created_by, curation_patch_id FROM page_versions "
            "WHERE version_id = (SELECT current_version_id FROM pages WHERE path = ?)",
            ("topics/local-memory",),
        ).fetchone()
        receipt_events = conn.execute(
            "SELECT count(*) FROM sync_outbox WHERE entity_kind = 'curation_receipt'"
        ).fetchone()[0]
    assert tuple(version) == ("remote-curator", patch["patch_id"])
    assert receipt_events == 1


def test_applied_page_hash_matches_the_exact_remote_proposal(
    postgres_url: str,
    curation_remote_url: str,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(curation_remote_url)
    _reset(local, remote)
    local.write_page(
        "topics/exact-proposal",
        "Exact proposal",
        {"summary": "Exact proposal", "body": "base"},
        tags=["llm:old-model"],
    )
    base = local.current_page_state("topics/exact-proposal")
    patch = create_patch(
        operation="update_page",
        path="topics/exact-proposal",
        proposal=_proposal("Exact proposal", "curated", tags=["curated"]),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:test"],
        reason="Replace the full page state.",
    )
    manifest = _publish_and_pull(local, remote, [patch])

    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"applied": 1}
    current = local.current_page_state("topics/exact-proposal")
    assert current["tags"] == ["curated"]
    assert current["content_hash"] == patch["proposed_hash"]


def test_remote_link_patch_applies_once_with_remote_provenance(
    tmp_path,
    curation_remote_url: str,
):
    local = SQLiteStore(tmp_path / "links.db")
    remote = PostgresStore(curation_remote_url)
    local.migrate()
    _reset(remote)
    local.write_page(
        "topics/source",
        "Source",
        {"summary": "Source", "body": "First concept."},
    )
    local.write_page(
        "topics/target",
        "Target",
        {"summary": "Target", "body": "Related concept."},
    )
    source = local.current_page_state("topics/source")
    patch = create_patch(
        operation="add_link",
        path="topics/source",
        proposal={"target_path": "topics/target", "link_type": "related"},
        base_version_id=source["version_id"],
        base_content_hash=source["content_hash"],
        evidence_ids=["archive-event:test"],
        reason="The pages cover related but distinct concepts.",
    )
    manifest = _publish_and_pull(local, remote, [patch])

    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"applied": 1}
    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {
        "already_processed": 1
    }
    assert local.graph_neighbors("topics/source") == [
        {
            "path": "topics/target",
            "title": "Target",
            "link_type": "related",
            "origin": "remote-curator",
            "metadata": {"curation_patch_id": patch["patch_id"]},
        }
    ]


def test_divergent_local_edit_creates_a_three_way_conflict_and_keep_local_receipt(
    tmp_path,
    curation_remote_url: str,
):
    local = SQLiteStore(tmp_path / "conflict.db")
    remote = PostgresStore(curation_remote_url)
    local.migrate()
    _reset(remote)
    local.write_page("topics/conflict", "Conflict", {"summary": "Conflict", "body": "base"})
    base = local.current_page_state("topics/conflict")
    patch = create_patch(
        operation="update_page",
        path="topics/conflict",
        proposal=_proposal("Conflict", "remote edit"),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:remote"],
        reason="Remote consolidation.",
    )
    manifest = _publish_and_pull(local, remote, [patch])
    local.write_page("topics/conflict", "Conflict", {"summary": "Conflict", "body": "local edit"})

    result = apply_run(local, UUID(manifest["run_id"]))
    conflicts = list_conflicts(local)

    assert result["counts"] == {"conflict": 1}
    assert local.read_page("topics/conflict")["content"]["body"] == "local edit"
    assert len(conflicts) == 1
    assert conflicts[0]["details"][0]["base_content_hash"] == base["content_hash"]
    assert conflicts[0]["details"][0]["local"]["content"]["body"] == "local edit"
    assert conflicts[0]["details"][0]["remote"]["content"]["body"] == "remote edit"

    resolved = resolve_conflict(
        local,
        UUID(manifest["run_id"]),
        UUID(patch["group_id"]),
        action="keep_local",
    )

    assert resolved["resolution"] == "keep_local"
    assert list_conflicts(local) == []
    assert local.read_page("topics/conflict")["content"]["body"] == "local edit"


def test_accept_remote_resolves_a_conflict_as_a_new_local_version(
    tmp_path,
    curation_remote_url: str,
):
    local = SQLiteStore(tmp_path / "accept-remote.db")
    remote = PostgresStore(curation_remote_url)
    local.migrate()
    _reset(remote)
    local.write_page("topics/accepted", "Accepted", {"summary": "Accepted", "body": "base"})
    base = local.current_page_state("topics/accepted")
    patch = create_patch(
        operation="update_page",
        path="topics/accepted",
        proposal=_proposal("Accepted", "remote wins"),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:remote"],
        reason="Use the reviewed remote text.",
    )
    manifest = _publish_and_pull(local, remote, [patch])
    local.write_page("topics/accepted", "Accepted", {"summary": "Accepted", "body": "local edit"})
    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"conflict": 1}

    resolved = resolve_conflict(
        local,
        UUID(manifest["run_id"]),
        UUID(patch["group_id"]),
        action="accept_remote",
    )

    assert resolved["resolution"] == "accept_remote"
    assert local.read_page("topics/accepted")["content"]["body"] == "remote wins"
    assert len(local.history("topics/accepted")) == 3


def test_duplicate_cleanup_group_updates_links_aliases_and_search_atomically(
    tmp_path,
    curation_remote_url: str,
):
    local = SQLiteStore(tmp_path / "cleanup.db")
    remote = PostgresStore(curation_remote_url)
    local.migrate()
    _reset(remote)
    local.write_page("topics/canonical", "Canonical", {"summary": "Canonical", "body": "short"})
    local.write_page("topics/duplicate", "Duplicate", {"summary": "Duplicate", "body": "duplicate-only phrase"})
    local.write_page("topics/referrer", "Referrer", {"summary": "Referrer", "body": "links"})
    canonical = local.current_page_state("topics/canonical")
    duplicate = local.current_page_state("topics/duplicate")
    referrer = local.current_page_state("topics/referrer")
    with local.connection(write=True) as conn:
        conn.execute(
            "INSERT INTO links (link_id, source_page_id, target_page_id, link_type, "
            "origin, metadata, created_at) VALUES (?, ?, ?, 'related', 'test', '{}', datetime('now'))",
            (str(uuid4()), referrer["page_id"], duplicate["page_id"]),
        )
    group_id = uuid4()
    patches = [
        create_patch(
            operation="update_page",
            path="topics/canonical",
            proposal=_proposal("Canonical", "short and duplicate-only phrase", tags=["curated"]),
            base_version_id=canonical["version_id"],
            base_content_hash=canonical["content_hash"],
            group_id=group_id,
            position=0,
            evidence_ids=["topics/duplicate"],
            reason="Merge duplicate evidence.",
            risk_class="high",
        ),
        create_patch(
            operation="retarget_links",
            path="topics/duplicate",
            proposal={"target_path": "topics/canonical"},
            base_version_id=duplicate["version_id"],
            base_content_hash=duplicate["content_hash"],
            group_id=group_id,
            position=1,
            evidence_ids=["topics/duplicate"],
            reason="Move duplicate links to the canonical page.",
            risk_class="high",
        ),
        create_patch(
            operation="add_alias",
            path="topics/duplicate",
            proposal={"target_path": "topics/canonical"},
            base_version_id=duplicate["version_id"],
            base_content_hash=duplicate["content_hash"],
            group_id=group_id,
            position=2,
            evidence_ids=["topics/duplicate"],
            reason="Preserve the old path.",
            risk_class="high",
        ),
        create_patch(
            operation="supersede_page",
            path="topics/duplicate",
            proposal={"target_path": "topics/canonical"},
            base_version_id=duplicate["version_id"],
            base_content_hash=duplicate["content_hash"],
            group_id=group_id,
            position=3,
            evidence_ids=["topics/duplicate"],
            reason="Hide the duplicate from active search.",
            risk_class="high",
        ),
    ]
    manifest = _publish_and_pull(local, remote, patches)

    safe = apply_run(local, UUID(manifest["run_id"]), policy="safe")
    applied = apply_run(local, UUID(manifest["run_id"]), policy="all")

    assert safe["counts"] == {"review_required": 1}
    assert applied["counts"] == {"applied": 1}
    assert local.read_page("topics/duplicate")["path"] == "topics/canonical"
    assert "topics/duplicate" not in {row["path"] for row in local.list_pages()}
    assert local.search("duplicate-only phrase")[0]["path"] == "topics/canonical"
    with local.connection() as conn:
        status = conn.execute(
            "SELECT status FROM pages WHERE path = 'topics/duplicate'"
        ).fetchone()[0]
        alias = conn.execute(
            "SELECT p.path FROM page_aliases a JOIN pages p ON p.page_id = a.target_page_id "
            "WHERE a.alias_path = 'topics/duplicate'"
        ).fetchone()[0]
        link_target = conn.execute(
            "SELECT p.path FROM links l JOIN pages p ON p.page_id = l.target_page_id "
            "WHERE l.source_page_id = ?",
            (referrer["page_id"],),
        ).fetchone()[0]
    assert status == "superseded"
    assert alias == "topics/canonical"
    assert link_target == "topics/canonical"
    maintenance = curate_database(local.database_path)
    assert "topics/duplicate" not in maintenance["orphan_pages"]
    assert all(
        "topics/duplicate" not in group["paths"]
        for group in maintenance["duplicate_page_groups"]
    )


def test_cleanup_group_rolls_back_when_one_participant_changed(
    tmp_path,
    curation_remote_url: str,
):
    local = SQLiteStore(tmp_path / "rollback.db")
    remote = PostgresStore(curation_remote_url)
    local.migrate()
    _reset(remote)
    local.write_page("topics/canonical", "Canonical", {"summary": "Canonical", "body": "base"})
    local.write_page("topics/duplicate", "Duplicate", {"summary": "Duplicate", "body": "base"})
    canonical = local.current_page_state("topics/canonical")
    duplicate = local.current_page_state("topics/duplicate")
    group_id = uuid4()
    patches = [
        create_patch(
            operation="update_page",
            path="topics/canonical",
            proposal=_proposal("Canonical", "remote merge"),
            base_version_id=canonical["version_id"],
            base_content_hash=canonical["content_hash"],
            group_id=group_id,
            position=0,
            evidence_ids=["topics/duplicate"],
            reason="Merge duplicate.",
            risk_class="high",
        ),
        create_patch(
            operation="supersede_page",
            path="topics/duplicate",
            proposal={"target_path": "topics/canonical"},
            base_version_id=duplicate["version_id"],
            base_content_hash=duplicate["content_hash"],
            group_id=group_id,
            position=1,
            evidence_ids=["topics/duplicate"],
            reason="Supersede duplicate.",
            risk_class="high",
        ),
    ]
    manifest = _publish_and_pull(local, remote, patches)
    local.write_page("topics/duplicate", "Duplicate", {"summary": "Duplicate", "body": "new local fact"})

    result = apply_run(local, UUID(manifest["run_id"]), policy="all")

    assert result["counts"] == {"conflict": 1}
    assert local.read_page("topics/canonical")["content"]["body"] == "base"
    assert local.read_page("topics/duplicate")["content"]["body"] == "new local fact"


def test_failed_group_commit_is_retryable_without_partial_page_changes(
    postgres_url: str,
    curation_remote_url: str,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(curation_remote_url)
    _reset(local, remote)
    local.write_page("topics/retry", "Retry", {"summary": "Retry", "body": "base"})
    base = local.current_page_state("topics/retry")
    patch = create_patch(
        operation="update_page",
        path="topics/retry",
        proposal=_proposal("Retry", "applied once"),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:test"],
        reason="Crash recovery test.",
    )
    manifest = _publish_and_pull(local, remote, [patch])

    def fail(stage: str) -> None:
        if stage == "before_group_commit":
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        apply_run(local, UUID(manifest["run_id"]), failpoint=fail)
    assert local.read_page("topics/retry")["content"]["body"] == "base"
    assert len(local.history("topics/retry")) == 1

    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"applied": 1}
    assert len(local.history("topics/retry")) == 2


def test_manifest_hash_detects_tampering(postgres_url: str):
    store = PostgresStore(postgres_url)
    _reset(store)
    replica_id = get_or_create_replica_id(store)
    patch = create_patch(
        operation="create_page",
        path="topics/tampered",
        proposal=_proposal("Tampered", "original"),
        evidence_ids=["session:test"],
        reason="Create a curated page.",
    )
    manifest = build_manifest(replica_id=replica_id, input_watermark=1, patches=[patch])
    manifest["patches"][0]["proposal"]["content"]["body"] = "tampered"

    with pytest.raises(ValueError, match="manifest hash"):
        publish_manifest(store, manifest)


def test_cleanup_operations_cannot_bypass_high_risk_review():
    with pytest.raises(ValueError, match="high risk"):
        create_patch(
            operation="supersede_page",
            path="topics/duplicate",
            proposal={"target_path": "topics/canonical"},
            base_version_id=uuid4(),
            base_content_hash="a" * 64,
            evidence_ids=["topics/duplicate"],
            reason="Attempt unsafe cleanup.",
            risk_class="low",
        )


def test_applied_patch_receipt_returns_through_the_normal_archive_outbox(
    postgres_url: str,
    curation_remote_url: str,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(curation_remote_url)
    _reset(local, remote)
    local.write_page("topics/receipt", "Receipt", {"summary": "Receipt", "body": "base"})
    base = local.current_page_state("topics/receipt")
    patch = create_patch(
        operation="update_page",
        path="topics/receipt",
        proposal=_proposal("Receipt", "curated"),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:test"],
        reason="Verify the return receipt.",
    )
    manifest = _publish_and_pull(local, remote, [patch])
    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"applied": 1}

    result = sync_to_archive(local, curation_remote_url, limit=100)

    assert result["acknowledged"] == 3
    with remote.connection() as conn:
        kinds = dict(
            conn.execute(
                "SELECT entity_kind, count(*) FROM archive_events GROUP BY entity_kind"
            ).fetchall()
        )
        receipt = conn.execute(
            "SELECT payload FROM archive_events WHERE entity_kind = 'curation_receipt'"
        ).fetchone()[0]
    assert kinds == {"curation_receipt": 1, "page_version": 2}
    assert receipt["patch_id"] == patch["patch_id"]
    assert receipt["status"] == "applied"


def test_explicit_pull_and_local_cli_plan_apply_without_a_remote_dependency_during_apply(
    postgres_url: str,
    curation_remote_url: str,
    capsys: pytest.CaptureFixture[str],
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(curation_remote_url)
    _reset(local, remote)
    local.write_page("topics/cli", "CLI", {"summary": "CLI", "body": "base"})
    base = local.current_page_state("topics/cli")
    replica_id = get_or_create_replica_id(local)
    patch = create_patch(
        operation="update_page",
        path="topics/cli",
        proposal=_proposal("CLI", "pulled and applied locally"),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:test"],
        reason="Exercise the harness-neutral CLI.",
    )
    manifest = build_manifest(replica_id=replica_id, input_watermark=1, patches=[patch])
    publish_manifest(remote, manifest)

    assert pull_curation_patches(local, curation_remote_url) == {
        "received_runs": 1,
        "received_patches": 1,
    }
    assert cli_main(
        ["--database-url", postgres_url, "sync", "plan", manifest["run_id"]]
    ) == 0
    planned = capsys.readouterr().out
    assert '"applicable": 1' in planned

    assert cli_main(
        [
            "--database-url",
            postgres_url,
            "sync",
            "apply",
            manifest["run_id"],
            "--policy",
            "safe",
        ]
    ) == 0
    applied = capsys.readouterr().out
    assert '"applied": 1' in applied
    assert local.read_page("topics/cli")["content"]["body"] == "pulled and applied locally"


def test_create_patch_never_overwrites_an_existing_local_page(
    postgres_url: str,
    curation_remote_url: str,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(curation_remote_url)
    _reset(local, remote)
    replica_id = get_or_create_replica_id(local)
    patch = create_patch(
        operation="create_page",
        path="topics/remote-only",
        proposal=_proposal("Remote only", "created from archived evidence"),
        evidence_ids=["session:test"],
        reason="Create a missing curated topic.",
    )
    manifest = build_manifest(replica_id=replica_id, input_watermark=1, patches=[patch])
    publish_manifest(remote, manifest)
    pull_manifests(local, remote, replica_id=replica_id)
    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"applied": 1}

    conflicting = create_patch(
        operation="create_page",
        path="topics/remote-only",
        proposal=_proposal("Remote only", "different remote page"),
        evidence_ids=["session:other"],
        reason="A stale curator still believes the path is absent.",
    )
    second = build_manifest(replica_id=replica_id, input_watermark=2, patches=[conflicting])
    publish_manifest(remote, second)
    pull_manifests(local, remote, replica_id=replica_id)

    assert apply_run(local, UUID(second["run_id"]))["counts"] == {"conflict": 1}
    assert local.read_page("topics/remote-only")["content"]["body"] == "created from archived evidence"


def test_merged_resolution_records_the_existing_local_merge_after_a_defer(
    postgres_url: str,
    curation_remote_url: str,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(curation_remote_url)
    _reset(local, remote)
    local.write_page("topics/merged", "Merged", {"summary": "Merged", "body": "base"})
    base = local.current_page_state("topics/merged")
    patch = create_patch(
        operation="update_page",
        path="topics/merged",
        proposal=_proposal("Merged", "remote contribution"),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:remote"],
        reason="Merge remote evidence.",
    )
    manifest = _publish_and_pull(local, remote, [patch])
    local.write_page("topics/merged", "Merged", {"summary": "Merged", "body": "local contribution"})
    assert apply_run(local, UUID(manifest["run_id"]))["counts"] == {"conflict": 1}
    assert resolve_conflict(
        local,
        UUID(manifest["run_id"]),
        UUID(patch["group_id"]),
        action="defer",
    )["resolution"] == "deferred"
    assert len(list_conflicts(local)) == 1

    local.write_page(
        "topics/merged",
        "Merged",
        {"summary": "Merged", "body": "local contribution and remote contribution"},
    )
    resolved = resolve_conflict(
        local,
        UUID(manifest["run_id"]),
        UUID(patch["group_id"]),
        action="merged",
    )

    assert resolved["resolution"] == "merged"
    assert list_conflicts(local) == []
    assert local.read_page("topics/merged")["content"]["body"] == (
        "local contribution and remote contribution"
    )


def test_concurrent_apply_creates_only_one_page_version_and_one_receipt(
    postgres_url: str,
    curation_remote_url: str,
):
    local = PostgresStore(postgres_url)
    remote = PostgresStore(curation_remote_url)
    _reset(local, remote)
    local.write_page("topics/concurrent", "Concurrent", {"summary": "Concurrent", "body": "base"})
    base = local.current_page_state("topics/concurrent")
    patch = create_patch(
        operation="update_page",
        path="topics/concurrent",
        proposal=_proposal("Concurrent", "one update"),
        base_version_id=base["version_id"],
        base_content_hash=base["content_hash"],
        evidence_ids=["session:test"],
        reason="Verify serialized patch application.",
    )
    manifest = _publish_and_pull(local, remote, [patch])
    run_id = UUID(manifest["run_id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply_run(PostgresStore(postgres_url), run_id), range(2)))

    assert {next(iter(result["counts"])) for result in results} == {"applied", "already_processed"}
    assert len(local.history("topics/concurrent")) == 2
    with local.connection() as conn:
        assert conn.execute("SELECT count(*) FROM curation_receipts").fetchone()[0] == 1
