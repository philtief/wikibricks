"""PostgreSQL persistence for curation manifests and receipts."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from wikibricks.curation.protocol import content_hash, validate_manifest
from wikibricks.postgres_store import PostgresStore


def get_or_create_replica_id(store: PostgresStore) -> UUID:
    store.migrate()
    candidate = uuid4()
    with store.connection() as conn, conn.transaction():
        conn.execute(
            "INSERT INTO sync_replicas (singleton, replica_id) "
            "VALUES (true, %s) ON CONFLICT (singleton) DO NOTHING",
            (candidate,),
        )
        row = conn.execute(
            "SELECT replica_id FROM sync_replicas WHERE singleton"
        ).fetchone()
        return UUID(str(row[0]))


def _insert_manifest(
    conn: Connection,
    manifest: dict[str, Any],
    *,
    received: bool,
) -> bool:
    existing = conn.execute(
        "SELECT manifest_hash FROM curation_runs WHERE run_id = %s",
        (UUID(manifest["run_id"]),),
    ).fetchone()
    if existing:
        if existing[0] != manifest["manifest_hash"]:
            raise RuntimeError(
                f"curation run hash conflict: {manifest['run_id']}"
            )
        return False
    conn.execute(
        "INSERT INTO curation_runs "
        "(run_id, replica_id, input_watermark, schema_version, manifest_hash, "
        "manifest, received_at) VALUES "
        "(%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END)",
        (
            UUID(manifest["run_id"]),
            UUID(manifest["replica_id"]),
            manifest["input_watermark"],
            manifest["schema_version"],
            manifest["manifest_hash"],
            Jsonb(manifest),
            received,
        ),
    )
    for patch in manifest["patches"]:
        conn.execute(
            "INSERT INTO curation_patches "
            "(patch_id, run_id, group_id, position, operation, path, "
            "base_version_id, base_content_hash, proposed_hash, proposal, "
            "evidence_ids, reason, risk_class) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                UUID(patch["patch_id"]),
                UUID(manifest["run_id"]),
                UUID(patch["group_id"]),
                patch["position"],
                patch["operation"],
                patch["path"],
                (
                    UUID(patch["base_version_id"])
                    if patch["base_version_id"]
                    else None
                ),
                patch["base_content_hash"],
                patch["proposed_hash"],
                Jsonb(patch["proposal"]),
                patch["evidence_ids"],
                patch["reason"],
                patch["risk_class"],
            ),
        )
    return True


def publish_manifest(
    remote: PostgresStore,
    manifest: dict[str, Any],
) -> bool:
    remote.migrate()
    checked = validate_manifest(manifest)
    with remote.connection() as conn, conn.transaction():
        return _insert_manifest(conn, checked, received=False)


def pull_manifests(
    local: PostgresStore,
    remote: PostgresStore,
    *,
    replica_id: UUID,
) -> dict[str, int]:
    local.migrate()
    remote.migrate()
    with remote.connection() as conn:
        rows = conn.execute(
            "SELECT manifest FROM curation_runs WHERE replica_id = %s "
            "ORDER BY input_watermark, published_at, run_id",
            (replica_id,),
        ).fetchall()
    received_runs = 0
    received_patches = 0
    for row in rows:
        manifest = validate_manifest(dict(row[0]))
        with local.connection() as conn, conn.transaction():
            if _insert_manifest(conn, manifest, received=True):
                received_runs += 1
                received_patches += len(manifest["patches"])
    return {
        "received_runs": received_runs,
        "received_patches": received_patches,
    }


def patches_for_run(
    conn: Connection,
    run_id: UUID,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT patch_id, group_id, position, operation, path, base_version_id, "
        "base_content_hash, proposed_hash, proposal, evidence_ids, reason, "
        "risk_class FROM curation_patches WHERE run_id = %s "
        "ORDER BY group_id, position",
        (run_id,),
    ).fetchall()
    if not rows:
        raise KeyError(f"unknown curation run: {run_id}")
    keys = (
        "patch_id",
        "group_id",
        "position",
        "operation",
        "path",
        "base_version_id",
        "base_content_hash",
        "proposed_hash",
        "proposal",
        "evidence_ids",
        "reason",
        "risk_class",
    )
    patches = []
    for row in rows:
        patch = dict(zip(keys, row))
        patch["patch_id"] = str(patch["patch_id"])
        patch["group_id"] = str(patch["group_id"])
        patch["base_version_id"] = (
            str(patch["base_version_id"])
            if patch["base_version_id"]
            else None
        )
        patch["proposal"] = dict(patch["proposal"])
        patch["evidence_ids"] = list(patch["evidence_ids"])
        patches.append(patch)
    return patches


def page_state(
    conn: Connection,
    path: str,
    *,
    lock: bool,
) -> dict[str, Any] | None:
    suffix = " FOR UPDATE OF p" if lock else ""
    row = conn.execute(
        "SELECT p.page_id, p.path, p.status, p.superseded_by_page_id, "
        "v.version_id, v.version, v.title, v.page_type, v.content, "
        "v.content_text, v.tags, v.source_ids, v.parent_id, v.chunk_index, "
        "v.content_hash FROM pages p JOIN page_versions v "
        "ON v.version_id = p.current_version_id WHERE p.path = %s" + suffix,
        (path,),
    ).fetchone()
    if not row:
        return None
    return {
        "page_id": str(row[0]),
        "path": row[1],
        "status": row[2],
        "superseded_by_page_id": str(row[3]) if row[3] else None,
        "version_id": str(row[4]),
        "version": row[5],
        "title": row[6],
        "page_type": row[7],
        "content": row[8],
        "content_text": row[9],
        "tags": list(row[10] or []),
        "source_ids": list(row[11]) if row[11] is not None else None,
        "parent_id": str(row[12]) if row[12] else None,
        "chunk_index": row[13],
        "content_hash": row[14],
    }


def target_page(
    conn: Connection,
    target_path: str,
    *,
    lock: bool,
) -> dict[str, Any] | None:
    state = page_state(conn, target_path, lock=lock)
    return state if state and state["status"] == "active" else None


def receipt_count(
    conn: Connection,
    patches: list[dict[str, Any]],
) -> int:
    ids = [UUID(patch["patch_id"]) for patch in patches]
    row = conn.execute(
        "SELECT count(*) FROM curation_receipts WHERE patch_id = ANY(%s)",
        (ids,),
    ).fetchone()
    return int(row[0])


def insert_receipt(
    conn: Connection,
    run_id: UUID,
    patch: dict[str, Any],
    *,
    status: str,
    result_version_id: UUID | None,
) -> None:
    local = page_state(conn, patch["path"], lock=False)
    local_hash = local["content_hash"] if local else None
    payload = {
        "run_id": str(run_id),
        "patch_id": patch["patch_id"],
        "status": status,
        "result_version_id": (
            str(result_version_id) if result_version_id else None
        ),
        "local_content_hash": local_hash,
    }
    conn.execute(
        "INSERT INTO curation_receipts "
        "(patch_id, run_id, status, result_version_id, local_content_hash, "
        "details) VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (patch_id) DO NOTHING",
        (
            UUID(patch["patch_id"]),
            run_id,
            status,
            result_version_id,
            local_hash,
            Jsonb({}),
        ),
    )
    conn.execute(
        "INSERT INTO sync_outbox "
        "(event_id, entity_kind, entity_id, version_id, payload_hash) "
        "VALUES (%s, 'curation_receipt', %s, %s, %s) "
        "ON CONFLICT (event_id) DO NOTHING",
        (
            uuid5(
                NAMESPACE_URL,
                f"wikibricks:curation-receipt:{patch['patch_id']}",
            ),
            run_id,
            UUID(patch["patch_id"]),
            content_hash(payload),
        ),
    )


def mark_run_applied(conn: Connection, run_id: UUID) -> None:
    conn.execute(
        "UPDATE curation_runs SET applied_at = now() WHERE run_id = %s "
        "AND NOT EXISTS (SELECT 1 FROM curation_patches p "
        "LEFT JOIN curation_receipts r USING (patch_id) "
        "WHERE p.run_id = %s AND r.patch_id IS NULL)",
        (run_id, run_id),
    )


def list_conflicts(store: PostgresStore) -> list[dict[str, Any]]:
    store.migrate()
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT conflict_id, run_id, group_id, details, created_at "
            "FROM curation_conflicts WHERE status = 'pending' "
            "ORDER BY created_at, conflict_id"
        ).fetchall()
    return [
        {
            "conflict_id": str(row[0]),
            "run_id": str(row[1]),
            "group_id": str(row[2]),
            "details": list(row[3]),
            "created_at": row[4],
        }
        for row in rows
    ]
