"""Persistence for curation manifests and receipts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from wikibricks.curation.protocol import content_hash, validate_manifest
from wikibricks.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from psycopg import Connection

    from wikibricks.postgres_store import PostgresStore


def _placeholder(conn: Any) -> str:
    return "?" if isinstance(conn, sqlite3.Connection) else "%s"


def _uuid(conn: Any, value: str | UUID | None) -> str | UUID | None:
    if value is None:
        return None
    return str(value) if isinstance(conn, sqlite3.Connection) else UUID(str(value))


def _json(conn: Any, value: Any) -> Any:
    if isinstance(conn, sqlite3.Connection):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _array(conn: Any, value: list[str]) -> Any:
    return _json(conn, value) if isinstance(conn, sqlite3.Connection) else value


def _timestamp(conn: Any) -> str | datetime:
    value = datetime.now(timezone.utc)
    return value.isoformat() if isinstance(conn, sqlite3.Connection) else value


def get_or_create_replica_id(store: PostgresStore | SQLiteStore) -> UUID:
    store.migrate()
    candidate = uuid4()
    if isinstance(store, SQLiteStore):
        with store.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO sync_replicas (singleton, replica_id) "
                "VALUES (1, ?) ON CONFLICT (singleton) DO NOTHING",
                (str(candidate),),
            )
            row = conn.execute(
                "SELECT replica_id FROM sync_replicas WHERE singleton = 1"
            ).fetchone()
            return UUID(str(row[0]))
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
    placeholder = _placeholder(conn)
    existing = conn.execute(
        f"SELECT manifest_hash FROM curation_runs WHERE run_id = {placeholder}",
        (_uuid(conn, manifest["run_id"]),),
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
        f"manifest, received_at) VALUES ({', '.join([placeholder] * 7)})",
        (
            _uuid(conn, manifest["run_id"]),
            _uuid(conn, manifest["replica_id"]),
            manifest["input_watermark"],
            manifest["schema_version"],
            manifest["manifest_hash"],
            _json(conn, manifest),
            _timestamp(conn) if received else None,
        ),
    )
    for patch in manifest["patches"]:
        conn.execute(
            "INSERT INTO curation_patches "
            "(patch_id, run_id, group_id, position, operation, path, "
            "base_version_id, base_content_hash, proposed_hash, proposal, "
            f"evidence_ids, reason, risk_class) VALUES ({', '.join([placeholder] * 13)})",
            (
                _uuid(conn, patch["patch_id"]),
                _uuid(conn, manifest["run_id"]),
                _uuid(conn, patch["group_id"]),
                patch["position"],
                patch["operation"],
                patch["path"],
                _uuid(conn, patch["base_version_id"]),
                patch["base_content_hash"],
                patch["proposed_hash"],
                _json(conn, patch["proposal"]),
                _array(conn, patch["evidence_ids"]),
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
    local: PostgresStore | SQLiteStore,
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
        if isinstance(local, SQLiteStore):
            with local.connection(write=True) as conn:
                inserted = _insert_manifest(conn, manifest, received=True)
        else:
            with local.connection() as conn, conn.transaction():
                inserted = _insert_manifest(conn, manifest, received=True)
        if inserted:
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
    sqlite = isinstance(conn, sqlite3.Connection)
    placeholder = "?" if sqlite else "%s"
    rows = conn.execute(
        "SELECT patch_id, group_id, position, operation, path, base_version_id, "
        "base_content_hash, proposed_hash, proposal, evidence_ids, reason, "
        f"risk_class FROM curation_patches WHERE run_id = {placeholder} "
        "ORDER BY group_id, position",
        (str(run_id) if sqlite else run_id,),
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
        patch["proposal"] = (
            json.loads(patch["proposal"])
            if isinstance(patch["proposal"], str)
            else dict(patch["proposal"])
        )
        patch["evidence_ids"] = (
            json.loads(patch["evidence_ids"])
            if isinstance(patch["evidence_ids"], str)
            else list(patch["evidence_ids"])
        )
        patches.append(patch)
    return patches


def page_state(
    conn: Connection,
    path: str,
    *,
    lock: bool,
) -> dict[str, Any] | None:
    sqlite = isinstance(conn, sqlite3.Connection)
    suffix = " FOR UPDATE OF p" if lock and not sqlite else ""
    placeholder = "?" if sqlite else "%s"
    row = conn.execute(
        "SELECT p.page_id, p.path, p.status, p.superseded_by_page_id, "
        "v.version_id, v.version, v.title, v.page_type, v.content, "
        "v.content_text, v.tags, v.source_ids, v.parent_id, v.chunk_index, "
        "v.content_hash FROM pages p JOIN page_versions v "
        f"ON v.version_id = p.current_version_id WHERE p.path = {placeholder}" + suffix,
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
        "content": json.loads(row[8]) if sqlite else row[8],
        "content_text": row[9],
        "tags": json.loads(row[10]) if sqlite else list(row[10] or []),
        "source_ids": (
            json.loads(row[11])
            if sqlite and row[11] is not None
            else (list(row[11]) if row[11] is not None else None)
        ),
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
    if isinstance(conn, sqlite3.Connection):
        ids = [patch["patch_id"] for patch in patches]
        placeholders = ",".join("?" for _ in ids)
        row = conn.execute(
            f"SELECT count(*) FROM curation_receipts WHERE patch_id IN ({placeholders})",
            ids,
        ).fetchone()
    else:
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
    if isinstance(conn, sqlite3.Connection):
        timestamp = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO curation_receipts "
            "(patch_id, run_id, status, result_version_id, local_content_hash, details, applied_at) "
            "VALUES (?, ?, ?, ?, ?, '{}', ?) ON CONFLICT (patch_id) DO NOTHING",
            (
                patch["patch_id"],
                str(run_id),
                status,
                str(result_version_id) if result_version_id else None,
                local_hash,
                timestamp,
            ),
        )
        conn.execute(
            "INSERT INTO sync_outbox "
            "(event_id, entity_kind, entity_id, version_id, payload_hash, created_at) "
            "VALUES (?, 'curation_receipt', ?, ?, ?, ?) "
            "ON CONFLICT (event_id) DO NOTHING",
            (
                str(uuid5(NAMESPACE_URL, f"wikibricks:curation-receipt:{patch['patch_id']}")),
                str(run_id),
                patch["patch_id"],
                content_hash(payload),
                timestamp,
            ),
        )
        return

    from psycopg.types.json import Jsonb

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
    if isinstance(conn, sqlite3.Connection):
        conn.execute(
            "UPDATE curation_runs SET applied_at = ? WHERE run_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM curation_patches p "
            "LEFT JOIN curation_receipts r USING (patch_id) "
            "WHERE p.run_id = ? AND r.patch_id IS NULL)",
            (datetime.now(timezone.utc).isoformat(), str(run_id), str(run_id)),
        )
        return
    conn.execute(
        "UPDATE curation_runs SET applied_at = now() WHERE run_id = %s "
        "AND NOT EXISTS (SELECT 1 FROM curation_patches p "
        "LEFT JOIN curation_receipts r USING (patch_id) "
        "WHERE p.run_id = %s AND r.patch_id IS NULL)",
        (run_id, run_id),
    )


def list_conflicts(store: PostgresStore | SQLiteStore) -> list[dict[str, Any]]:
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
            "details": (
                json.loads(row[3])
                if isinstance(row[3], str)
                else list(row[3])
            ),
            "created_at": row[4],
        }
        for row in rows
    ]
