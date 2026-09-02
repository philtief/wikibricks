"""Lakebase-only persistence for remote curation search documents."""

from __future__ import annotations

from importlib.resources import files
from typing import Any
from uuid import UUID

from wikibricks.postgres_store import PostgresStore
from wikibricks_remote.search.documents import SearchDocument, project_event

_MIGRATION_LOCK = "wikibricks:lakebase-search-migration"
_EXTENSIONS = ("lakebase_text", "lakebase_vector")


class LakebaseHybridSearch:
    def __init__(
        self,
        store: PostgresStore,
        *,
        embedding_model: str,
        embedding_dimension: int = 1024,
        max_chunk_chars: int = 12000,
    ) -> None:
        if not embedding_model.strip():
            raise ValueError("embedding model must be non-empty")
        if embedding_dimension < 1:
            raise ValueError("embedding dimension must be positive")
        if max_chunk_chars < 1:
            raise ValueError("search chunk size must be positive")
        self.store = store
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension
        self.max_chunk_chars = max_chunk_chars

    def available(self) -> bool:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT name FROM pg_available_extensions WHERE name = ANY(%s)",
                (list(_EXTENSIONS),),
            ).fetchall()
        return {str(row[0]) for row in rows} == set(_EXTENSIONS)

    def migrate(self) -> bool:
        if not self.available():
            return False
        sql = (
            files("wikibricks_remote")
            .joinpath("sql", "0001_lakebase_search.sql")
            .read_text(encoding="utf-8")
            .replace("__EMBEDDING_DIMENSION__", str(self.embedding_dimension))
        )
        with self.store.connection() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (_MIGRATION_LOCK,),
            )
            conn.execute(sql)
        return True

    def _latest_page_events(
        self,
        replica_id: UUID,
        watermark: int,
        maximum: int,
    ) -> list[dict[str, Any]]:
        if maximum == 0:
            return []
        with self.store.connection() as conn:
            rows = conn.execute(
                "WITH ranked AS (SELECT replica_id, event_id, local_sequence, "
                "entity_kind, entity_id, version_id, payload, "
                "row_number() OVER (PARTITION BY payload->>'path' "
                "ORDER BY local_sequence DESC) AS rank "
                "FROM archive_events WHERE replica_id = %s "
                "AND entity_kind = 'page_version' AND local_sequence <= %s) "
                "SELECT replica_id, event_id, local_sequence, entity_kind, "
                "entity_id, version_id, payload FROM ranked WHERE rank = 1 "
                "ORDER BY payload->>'path' LIMIT %s",
                (replica_id, watermark, maximum),
            ).fetchall()
        keys = (
            "replica_id",
            "event_id",
            "local_sequence",
            "entity_kind",
            "entity_id",
            "version_id",
            "payload",
        )
        return [dict(zip(keys, row)) for row in rows]

    @staticmethod
    def _normalize_event(raw: dict[str, Any], replica_id: UUID) -> dict[str, Any]:
        event = dict(raw)
        event["replica_id"] = replica_id
        if "event_id" not in event and str(event.get("evidence_id", "")).startswith(
            "archive-event:"
        ):
            event["event_id"] = str(event["evidence_id"]).split(":", 1)[1]
        return event

    def _insert(self, documents: list[SearchDocument]) -> int:
        inserted = 0
        with self.store.connection() as conn, conn.transaction():
            for item in documents:
                row = conn.execute(
                    "INSERT INTO remote_search_documents "
                    "(document_id, replica_id, archive_event_id, local_sequence, "
                    "entity_kind, entity_id, version_id, page_path, title, "
                    "document_kind, chunk_index, content_text, content_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (archive_event_id, chunk_index) DO NOTHING "
                    "RETURNING document_id",
                    (
                        item.document_id,
                        item.replica_id,
                        item.archive_event_id,
                        item.local_sequence,
                        item.entity_kind,
                        item.entity_id,
                        item.version_id,
                        item.page_path,
                        item.title,
                        item.document_kind,
                        item.chunk_index,
                        item.content_text,
                        item.content_hash,
                    ),
                ).fetchone()
                inserted += row is not None
        return inserted

    def project(
        self,
        replica_id: UUID,
        watermark: int,
        evidence: list[dict[str, Any]],
        *,
        max_pages: int,
    ) -> int:
        if watermark < 0 or max_pages < 0:
            raise ValueError("search projection bounds cannot be negative")
        if not self.migrate():
            return 0
        events = [
            self._normalize_event(item, replica_id)
            for item in evidence
        ]
        events.extend(self._latest_page_events(replica_id, watermark, max_pages))
        documents = {
            document.document_id: document
            for event in events
            for document in project_event(event, max_chars=self.max_chunk_chars)
        }
        return self._insert(list(documents.values()))


__all__ = ["LakebaseHybridSearch"]
