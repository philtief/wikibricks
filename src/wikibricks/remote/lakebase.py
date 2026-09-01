"""Explicit, resumable PostgreSQL-to-Lakebase archive synchronization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from wikibricks.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from wikibricks.postgres_store import PostgresStore

SYNC_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class ArchiveEvent:
    replica_id: UUID
    sequence: int
    event_id: UUID
    entity_kind: str
    entity_id: UUID
    version_id: UUID
    payload_hash: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SyncBatch:
    manifest: dict[str, Any]
    events: tuple[ArchiveEvent, ...]


@dataclass(frozen=True, slots=True)
class LakebaseTarget:
    project: str
    branch: str
    endpoint: str
    database: str
    profile: str | None

    def fresh_database_url(self) -> str:
        if not self.profile:
            raise ValueError("Databricks profile is required for Lakebase sync")
        from databricks.sdk import WorkspaceClient
        from psycopg.conninfo import make_conninfo

        client = WorkspaceClient(profile=self.profile)
        endpoint_name = (
            f"projects/{self.project}/branches/{self.branch}/endpoints/{self.endpoint}"
        )
        endpoint = client.postgres.get_endpoint(endpoint_name)
        credential = client.postgres.generate_database_credential(endpoint_name)
        user = client.current_user.me().user_name
        host = endpoint.status.hosts.host
        return make_conninfo(
            host=host,
            port=5432,
            dbname=self.database,
            user=user,
            password=credential.token,
            sslmode="require",
        )


def _jsonable(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"unsupported archive payload value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        default=_jsonable,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _resolve_event(
    store: PostgresStore | SQLiteStore,
    row: dict[str, Any],
    *,
    replica_id: UUID,
) -> ArchiveEvent:
    placeholder = "?" if isinstance(store, SQLiteStore) else "%s"
    with store.connection() as conn:
        if row["entity_kind"] == "page_version":
            payload_row = conn.execute(
                "SELECT p.path, v.version, v.title, v.page_type, v.content, v.content_text, "
                "v.tags, v.source_ids, v.parent_id, v.chunk_index, v.created_by, v.created_at, "
                "v.curation_patch_id "
                "FROM page_versions v JOIN pages p ON p.page_id = v.page_id "
                f"WHERE v.version_id = {placeholder}",
                (row["version_id"],),
            ).fetchone()
            if not payload_row:
                raise RuntimeError(f"missing immutable page version {row['version_id']}")
            payload = {
                "path": payload_row[0],
                "version": payload_row[1],
                "title": payload_row[2],
                "page_type": payload_row[3],
                "content": payload_row[4],
                "content_text": payload_row[5],
                "tags": payload_row[6],
                "source_ids": payload_row[7],
                "parent_id": payload_row[8],
                "chunk_index": payload_row[9],
                "created_by": payload_row[10],
                "created_at": payload_row[11],
                "curation_patch_id": payload_row[12],
            }
        elif row["entity_kind"] == "session_event_version":
            payload_row = conn.execute(
                "SELECT s.harness, s.external_id, s.user_id, s.agent, s.workspace, "
                "s.page_path, e.external_id, e.position, v.version, v.kind, v.content, "
                "v.metadata, v.source_created_at, v.created_at "
                "FROM session_event_versions v "
                "JOIN session_events e ON e.event_id = v.event_id "
                "JOIN sessions s ON s.session_id = e.session_id "
                f"WHERE v.version_id = {placeholder}",
                (row["version_id"],),
            ).fetchone()
            if not payload_row:
                raise RuntimeError(f"missing immutable session event version {row['version_id']}")
            payload = {
                "harness": payload_row[0],
                "session_external_id": payload_row[1],
                "user_id": payload_row[2],
                "agent": payload_row[3],
                "workspace": payload_row[4],
                "page_path": payload_row[5],
                "event_external_id": payload_row[6],
                "position": payload_row[7],
                "version": payload_row[8],
                "kind": payload_row[9],
                "content": payload_row[10],
                "metadata": payload_row[11],
                "source_created_at": payload_row[12],
                "created_at": payload_row[13],
            }
        elif row["entity_kind"] == "curation_receipt":
            payload_row = conn.execute(
                "SELECT run_id, patch_id, status, result_version_id, local_content_hash, "
                "details, applied_at FROM curation_receipts "
                f"WHERE patch_id = {placeholder}",
                (row["version_id"],),
            ).fetchone()
            if not payload_row:
                raise RuntimeError(f"missing immutable curation receipt {row['version_id']}")
            payload = {
                "run_id": payload_row[0],
                "patch_id": payload_row[1],
                "status": payload_row[2],
                "result_version_id": payload_row[3],
                "local_content_hash": payload_row[4],
                "details": payload_row[5],
                "applied_at": payload_row[6],
            }
        else:
            raise ValueError(f"unsupported outbox entity kind: {row['entity_kind']}")
    json_fields = {
        "page_version": ("content", "tags", "source_ids"),
        "session_event_version": ("metadata",),
        "curation_receipt": ("details",),
    }[row["entity_kind"]]
    for key in json_fields:
        if key in payload and isinstance(payload[key], str):
            payload[key] = json.loads(payload[key])
    return ArchiveEvent(
        replica_id=replica_id,
        sequence=int(row["sequence"]),
        event_id=UUID(str(row["event_id"])),
        entity_kind=row["entity_kind"],
        entity_id=UUID(str(row["entity_id"])),
        version_id=UUID(str(row["version_id"])),
        payload_hash=row["payload_hash"],
        payload=json.loads(_canonical_json(payload)),
    )


def build_batch(
    store: PostgresStore | SQLiteStore,
    *,
    limit: int = 1000,
) -> SyncBatch | None:
    if limit < 1:
        raise ValueError("archive batch limit must be positive")
    from wikibricks.curation import get_or_create_replica_id

    pending = store.pending_outbox(limit)
    if not pending:
        return None
    replica_id = get_or_create_replica_id(store)
    events = tuple(
        _resolve_event(store, row, replica_id=replica_id) for row in pending
    )
    digest_input = [
        {
            "replica_id": str(event.replica_id),
            "sequence": event.sequence,
            "event_id": str(event.event_id),
            "entity_kind": event.entity_kind,
            "entity_id": str(event.entity_id),
            "version_id": str(event.version_id),
            "payload_hash": event.payload_hash,
            "payload": event.payload,
        }
        for event in events
    ]
    digest = hashlib.sha256(_canonical_json(digest_input).encode("utf-8")).hexdigest()
    batch_id = uuid5(
        NAMESPACE_URL,
        f"wikibricks:archive-batch:{replica_id}:{digest}",
    )
    existing_batch_ids = {
        UUID(str(row["batch_id"])) for row in pending if row["batch_id"]
    }
    if existing_batch_ids and existing_batch_ids != {batch_id}:
        raise RuntimeError("pending outbox batch identity does not match its content digest")
    store.assign_outbox_batch([event.sequence for event in events], batch_id)
    manifest = {
        "batch_id": str(batch_id),
        "replica_id": str(replica_id),
        "schema_version": SYNC_SCHEMA_VERSION,
        "event_count": len(events),
        "first_sequence": events[0].sequence,
        "last_sequence": events[-1].sequence,
        "digest": digest,
    }
    return SyncBatch(manifest=manifest, events=events)


def _push_batch(batch: SyncBatch, remote_url: str) -> dict[str, Any]:
    from psycopg.types.json import Jsonb

    from wikibricks.postgres_store import PostgresStore

    remote = PostgresStore(remote_url)
    remote.migrate()
    batch_id = UUID(batch.manifest["batch_id"])
    with remote.connection() as conn, conn.transaction():
        existing = conn.execute(
            "SELECT replica_id, schema_version, event_count, first_sequence, "
            "last_sequence, digest "
            "FROM archive_batches WHERE batch_id = %s",
            (batch_id,),
        ).fetchone()
        if existing:
            stored = {
                "batch_id": str(batch_id),
                "replica_id": str(existing[0]),
                "schema_version": existing[1],
                "event_count": existing[2],
                "first_sequence": existing[3],
                "last_sequence": existing[4],
                "digest": existing[5],
            }
            if stored != batch.manifest:
                raise RuntimeError("remote batch manifest hash conflict")
            return stored
        conn.execute(
            "CREATE TEMP TABLE wb_archive_stage "
            "(replica_id uuid, event_id uuid, local_sequence bigint, entity_kind text, "
            "entity_id uuid, version_id uuid, payload_hash text, payload jsonb) "
            "ON COMMIT DROP"
        )
        with conn.cursor().copy(
            "COPY wb_archive_stage "
            "(replica_id, event_id, local_sequence, entity_kind, entity_id, version_id, "
            "payload_hash, payload) FROM STDIN"
        ) as copy:
            for event in batch.events:
                copy.write_row(
                    (
                        event.replica_id,
                        event.event_id,
                        event.sequence,
                        event.entity_kind,
                        event.entity_id,
                        event.version_id,
                        event.payload_hash,
                        Jsonb(event.payload),
                    )
                )
        conflict = conn.execute(
            "SELECT s.event_id FROM wb_archive_stage s JOIN archive_events a USING (event_id) "
            "WHERE s.payload_hash <> a.payload_hash OR s.replica_id <> a.replica_id LIMIT 1"
        ).fetchone()
        if conflict:
            raise RuntimeError(f"remote archive event hash conflict: {conflict[0]}")
        conn.execute(
            "INSERT INTO archive_events "
            "(replica_id, event_id, local_sequence, entity_kind, entity_id, version_id, "
            "payload_hash, payload) SELECT replica_id, event_id, local_sequence, entity_kind, "
            "entity_id, version_id, payload_hash, payload FROM wb_archive_stage "
            "ON CONFLICT (event_id) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO archive_batches "
            "(batch_id, replica_id, schema_version, event_count, first_sequence, "
            "last_sequence, digest) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                batch_id,
                UUID(batch.manifest["replica_id"]),
                batch.manifest["schema_version"],
                batch.manifest["event_count"],
                batch.manifest["first_sequence"],
                batch.manifest["last_sequence"],
                batch.manifest["digest"],
            ),
        )
        conn.execute(
            "INSERT INTO archive_batch_events (batch_id, event_id) "
            "SELECT %s, event_id FROM wb_archive_stage",
            (batch_id,),
        )
    return dict(batch.manifest)


def sync_to_archive(
    local: PostgresStore | SQLiteStore,
    remote_url: str,
    *,
    limit: int = 1000,
    drain: bool = False,
    max_batches: int = 100,
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if drain:
        if max_batches < 1:
            raise ValueError("maximum archive batches must be positive")
        batches = 0
        acknowledged = 0
        for _ in range(max_batches):
            result = sync_to_archive(
                local,
                remote_url,
                limit=limit,
                failpoint=failpoint,
            )
            if result["status"] == "idle":
                break
            batches += 1
            acknowledged += int(result["acknowledged"])
        remaining = local.outbox_count()
        status = "partial" if remaining else ("drained" if batches else "idle")
        return {
            "status": status,
            "batches": batches,
            "acknowledged": acknowledged,
            "remaining": remaining,
        }
    batch = build_batch(local, limit=limit)
    if batch is None:
        return {"status": "idle", "acknowledged": 0}
    trigger = failpoint or (lambda _stage: None)
    trigger("before_remote_copy")
    acknowledgement = _push_batch(batch, remote_url)
    trigger("after_remote_commit")
    if acknowledgement != batch.manifest:
        raise RuntimeError("remote acknowledgement does not match the local batch manifest")
    acknowledged = local.acknowledge_outbox_batch(UUID(batch.manifest["batch_id"]))
    return {
        "status": "synced",
        "batch_id": batch.manifest["batch_id"],
        "acknowledged": acknowledged,
    }


def pull_curated_snapshot(local: PostgresStore, remote_url: str) -> int:
    from psycopg.types.json import Jsonb

    from wikibricks.postgres_store import PostgresStore

    remote = PostgresStore(remote_url)
    remote.migrate()
    with remote.connection() as conn:
        latest = conn.execute("SELECT max(snapshot_version) FROM curated_pages").fetchone()[0]
        if latest is None:
            return 0
        rows = conn.execute(
            "SELECT path, title, content, content_text, tags, snapshot_version, content_hash "
            "FROM curated_pages WHERE snapshot_version = %s ORDER BY path",
            (latest,),
        ).fetchall()
    with local.connection() as conn, conn.transaction():
        current = conn.execute("SELECT max(snapshot_version) FROM archive_pages").fetchone()[0]
        if current is not None and int(latest) <= int(current):
            return 0
        imported = 0
        for row in rows:
            conflict = conn.execute(
                "SELECT 1 FROM pages WHERE path = %s UNION ALL "
                "SELECT 1 FROM sessions WHERE page_path = %s LIMIT 1",
                (row[0], row[0]),
            ).fetchone()
            if conflict:
                continue
            conn.execute(
                "INSERT INTO archive_pages "
                "(path, title, content, content_text, tags, snapshot_version, content_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (path) DO UPDATE SET "
                "title = excluded.title, content = excluded.content, "
                "content_text = excluded.content_text, tags = excluded.tags, "
                "snapshot_version = excluded.snapshot_version, content_hash = excluded.content_hash, "
                "imported_at = now() "
                "WHERE archive_pages.snapshot_version < excluded.snapshot_version",
                (row[0], row[1], Jsonb(row[2]), row[3], row[4], row[5], row[6]),
            )
            imported += 1
    return imported


def pull_curation_patches(local: PostgresStore, remote_url: str) -> dict[str, int]:
    """Copy immutable patch manifests into the local inbox without applying them."""
    from wikibricks.curation import get_or_create_replica_id, pull_manifests
    from wikibricks.postgres_store import PostgresStore

    remote = PostgresStore(remote_url)
    replica_id = get_or_create_replica_id(local)
    return pull_manifests(local, remote, replica_id=replica_id)
