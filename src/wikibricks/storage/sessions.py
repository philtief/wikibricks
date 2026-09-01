"""Normalized, versioned session persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from wikibricks.models import IngestResult, SessionEvent, SessionRecord
from wikibricks.session_ingest import (
    session_content_hash,
    session_identity,
    session_page_path,
)
from wikibricks.storage.content import canonical_hash, insert_search_chunks

if TYPE_CHECKING:
    from wikibricks.storage.store import PostgresStore


class SessionRepository:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    @staticmethod
    def title(record: SessionRecord) -> str:
        configured = record.metadata.get("title")
        if configured:
            return str(configured)[:120]
        for event in record.events:
            if event.kind == "user" and event.content.strip():
                return event.content.strip().splitlines()[0][:120]
        return f"Session {record.external_id[:8]}"

    @staticmethod
    def event_hash(event: SessionEvent) -> str:
        return canonical_hash(event.to_dict())

    def ingest(self, record: SessionRecord) -> IngestResult:
        stable_id = session_identity(record)
        record_hash = session_content_hash(record)
        created = updated = unchanged = 0
        with self.store.connection() as conn, conn.transaction():
            existing_session = conn.execute(
                "SELECT session_id, current_hash FROM sessions "
                "WHERE harness = %s AND external_id = %s FOR UPDATE",
                (record.harness, record.external_id),
            ).fetchone()
            if existing_session and existing_session[1] == record_hash:
                return IngestResult(0, 0, len(record.events))
            if existing_session:
                stable_id = existing_session[0]
                conn.execute(
                    "UPDATE sessions SET user_id = %s, agent = %s, workspace = %s, "
                    "started_at = %s, source_updated_at = %s, page_path = %s, "
                    "title = %s, metadata = %s, current_hash = %s, updated_at = now() "
                    "WHERE session_id = %s",
                    (
                        record.user_id,
                        record.agent,
                        record.workspace,
                        record.started_at,
                        record.updated_at,
                        session_page_path(record),
                        self.title(record),
                        Jsonb(record.metadata),
                        record_hash,
                        stable_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO sessions "
                    "(session_id, harness, external_id, user_id, agent, workspace, "
                    "started_at, source_updated_at, page_path, title, metadata, "
                    "current_hash) VALUES "
                    "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        stable_id,
                        record.harness,
                        record.external_id,
                        record.user_id,
                        record.agent,
                        record.workspace,
                        record.started_at,
                        record.updated_at,
                        session_page_path(record),
                        self.title(record),
                        Jsonb(record.metadata),
                        record_hash,
                    ),
                )
            active_ids: list[str] = []
            for position, event in enumerate(record.events):
                active_ids.append(event.external_id)
                event_id = uuid5(stable_id, event.external_id)
                event_hash = self.event_hash(event)
                existing_event = conn.execute(
                    "SELECT e.event_id, v.version, v.content_hash "
                    "FROM session_events e LEFT JOIN session_event_versions v "
                    "ON v.version_id = e.current_version_id "
                    "WHERE e.session_id = %s AND e.external_id = %s "
                    "FOR UPDATE OF e",
                    (stable_id, event.external_id),
                ).fetchone()
                if existing_event and existing_event[2] == event_hash:
                    conn.execute(
                        "UPDATE session_events SET position = %s, active = true "
                        "WHERE event_id = %s",
                        (position, existing_event[0]),
                    )
                    unchanged += 1
                    continue
                if existing_event:
                    event_id = existing_event[0]
                    version = int(existing_event[1]) + 1
                    updated += 1
                else:
                    version = 1
                    created += 1
                    conn.execute(
                        "INSERT INTO session_events "
                        "(event_id, session_id, external_id, position) "
                        "VALUES (%s, %s, %s, %s)",
                        (event_id, stable_id, event.external_id, position),
                    )
                version_id = uuid4()
                conn.execute(
                    "INSERT INTO session_event_versions "
                    "(version_id, event_id, version, kind, content, metadata, "
                    "source_created_at, content_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        version_id,
                        event_id,
                        version,
                        event.kind,
                        event.content,
                        Jsonb(event.metadata),
                        event.created_at,
                        event_hash,
                    ),
                )
                conn.execute(
                    "UPDATE session_events SET position = %s, "
                    "current_version_id = %s, active = true WHERE event_id = %s",
                    (position, version_id, event_id),
                )
                insert_search_chunks(
                    conn,
                    "session_search_chunks",
                    version_id,
                    event.content,
                )
                conn.execute(
                    "INSERT INTO sync_outbox "
                    "(event_id, entity_kind, entity_id, version_id, payload_hash) "
                    "VALUES (%s, 'session_event_version', %s, %s, %s)",
                    (uuid4(), event_id, version_id, event_hash),
                )
            if active_ids:
                conn.execute(
                    "UPDATE session_events SET active = false "
                    "WHERE session_id = %s AND NOT (external_id = ANY(%s))",
                    (stable_id, active_ids),
                )
            else:
                conn.execute(
                    "UPDATE session_events SET active = false WHERE session_id = %s",
                    (stable_id,),
                )
            self.store._failpoint("before_commit")
        return IngestResult(created, updated, unchanged)

    @staticmethod
    def read_event_rows(
        conn: Connection,
        session_id: UUID,
    ) -> list[tuple[Any, ...]]:
        return conn.execute(
            "SELECT e.external_id, v.kind, v.content, v.source_created_at, "
            "v.metadata FROM session_events e JOIN session_event_versions v "
            "ON v.version_id = e.current_version_id "
            "WHERE e.session_id = %s AND e.active ORDER BY e.position",
            (session_id,),
        ).fetchall()

    @staticmethod
    def event_from_row(row: tuple[Any, ...]) -> SessionEvent:
        return SessionEvent(
            external_id=row[0],
            kind=row[1],
            content=row[2],
            created_at=row[3].isoformat() if row[3] else None,
            metadata=row[4],
        )

    def read_events(self, harness: str, external_id: str) -> list[SessionEvent]:
        with self.store.connection() as conn:
            session = conn.execute(
                "SELECT session_id FROM sessions "
                "WHERE harness = %s AND external_id = %s",
                (harness, external_id),
            ).fetchone()
            if not session:
                return []
            rows = self.read_event_rows(conn, session[0])
        return [self.event_from_row(row) for row in rows]

    def event_versions(
        self,
        harness: str,
        session_external_id: str,
        event_external_id: str,
    ) -> int:
        with self.store.connection() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM session_event_versions v "
                    "JOIN session_events e ON e.event_id = v.event_id "
                    "JOIN sessions s ON s.session_id = e.session_id "
                    "WHERE s.harness = %s AND s.external_id = %s "
                    "AND e.external_id = %s",
                    (harness, session_external_id, event_external_id),
                ).fetchone()[0]
            )
