"""Archive outbox and source import cursors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from psycopg.types.json import Jsonb

if TYPE_CHECKING:
    from wikibricks.storage.store import PostgresStore


class OutboxRepository:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    def count(self) -> int:
        with self.store.connection() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM sync_outbox "
                    "WHERE acknowledged_at IS NULL"
                ).fetchone()[0]
            )

    def get_cursor(self, target: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT cursor FROM sync_state WHERE target = %s",
                (target,),
            ).fetchone()
        return dict(row[0]) if row else {}

    def set_cursor(self, target: str, cursor: dict[str, Any]) -> None:
        with self.store.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO sync_state (target, cursor) VALUES (%s, %s) "
                "ON CONFLICT (target) DO UPDATE "
                "SET cursor = excluded.cursor, updated_at = now()",
                (target, Jsonb(cursor)),
            )

    def pending(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            first = conn.execute(
                "SELECT batch_id FROM sync_outbox "
                "WHERE acknowledged_at IS NULL ORDER BY sequence LIMIT 1"
            ).fetchone()
            if not first:
                return []
            if first[0]:
                rows = conn.execute(
                    "SELECT sequence, event_id, entity_kind, entity_id, "
                    "version_id, payload_hash, batch_id FROM sync_outbox "
                    "WHERE acknowledged_at IS NULL AND batch_id = %s "
                    "ORDER BY sequence",
                    (first[0],),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT sequence, event_id, entity_kind, entity_id, "
                    "version_id, payload_hash, batch_id FROM sync_outbox "
                    "WHERE acknowledged_at IS NULL AND batch_id IS NULL "
                    "ORDER BY sequence LIMIT %s",
                    (limit,),
                ).fetchall()
        keys = (
            "sequence",
            "event_id",
            "entity_kind",
            "entity_id",
            "version_id",
            "payload_hash",
            "batch_id",
        )
        return [dict(zip(keys, row)) for row in rows]

    def assign_batch(self, sequences: list[int], batch_id: UUID) -> None:
        with self.store.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE sync_outbox SET batch_id = %s "
                "WHERE sequence = ANY(%s) AND acknowledged_at IS NULL "
                "AND (batch_id IS NULL OR batch_id = %s)",
                (batch_id, sequences, batch_id),
            )

    def acknowledge_batch(self, batch_id: UUID) -> int:
        with self.store.connection() as conn, conn.transaction():
            result = conn.execute(
                "UPDATE sync_outbox SET acknowledged_at = now() "
                "WHERE batch_id = %s AND acknowledged_at IS NULL",
                (batch_id,),
            )
        return result.rowcount
