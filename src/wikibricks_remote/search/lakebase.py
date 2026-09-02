"""Lakebase-only persistence for remote curation search documents."""

from __future__ import annotations

from importlib.resources import files
from typing import Any
from uuid import UUID

from wikibricks.postgres_store import PostgresStore
from wikibricks_remote.search.documents import SearchDocument, project_event
from wikibricks_remote.search.embeddings import (
    Embedder,
    EmbeddingUpdate,
    build_embedding_updates,
)
from wikibricks_remote.search.ranking import (
    CandidateSelection,
    reciprocal_rank_fusion,
)

_MIGRATION_LOCK = "wikibricks:lakebase-search-migration"
_EXTENSIONS = ("lakebase_text", "lakebase_vector")
_BM25_INDEX = "remote_search_documents_content_bm25"


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

    def _refresh_keyword_index(self) -> None:
        with self.store.connection() as conn:
            exists = conn.execute(
                "SELECT to_regclass(%s)",
                (_BM25_INDEX,),
            ).fetchone()[0]
        if exists is None:
            with self.store.connection() as conn, conn.transaction():
                conn.execute(
                    f"CREATE INDEX {_BM25_INDEX} ON remote_search_documents "
                    "USING lakebase_bm25 (content_tsv)"
                )
            return
        with self.store.connection() as conn:
            previous = conn.autocommit
            conn.autocommit = True
            try:
                conn.execute("VACUUM (ANALYZE) remote_search_documents")
            finally:
                conn.autocommit = previous

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
        inserted = self._insert(list(documents.values()))
        self._refresh_keyword_index()
        return inserted

    def _missing_documents(self, maximum: int) -> list[SearchDocument]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT document_id, replica_id, archive_event_id, local_sequence, "
                "entity_kind, entity_id, version_id, page_path, title, "
                "document_kind, chunk_index, content_text, content_hash "
                "FROM remote_search_documents "
                "WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s "
                "ORDER BY local_sequence, document_id LIMIT %s",
                (self.embedding_model, maximum),
            ).fetchall()
        return [SearchDocument(*row) for row in rows]

    def _cached_vectors(
        self,
        content_hashes: list[str],
    ) -> dict[str, tuple[float, ...]]:
        if not content_hashes:
            return {}
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (content_hash) content_hash, "
                "to_json(embedding::real[]) FROM remote_search_documents "
                "WHERE embedding_model = %s AND embedding IS NOT NULL "
                "AND content_hash = ANY(%s) ORDER BY content_hash, document_id",
                (self.embedding_model, content_hashes),
            ).fetchall()
        vectors = {
            str(content_hash): tuple(float(value) for value in raw_vector)
            for content_hash, raw_vector in rows
        }
        for vector in vectors.values():
            if len(vector) != self.embedding_dimension:
                raise ValueError("cached embedding has the wrong dimension")
        return vectors

    def _write_embeddings(
        self,
        updates: list[EmbeddingUpdate],
        batch_size: int,
    ) -> int:
        written = 0
        for offset in range(0, len(updates), batch_size):
            with self.store.connection() as conn, conn.transaction():
                for update in updates[offset : offset + batch_size]:
                    row = conn.execute(
                        "UPDATE remote_search_documents SET embedding_model = %s, "
                        "embedding = %s WHERE document_id = %s AND content_hash = %s "
                        "RETURNING document_id",
                        (
                            update.model,
                            list(update.embedding),
                            update.document_id,
                            update.content_hash,
                        ),
                    ).fetchone()
                    written += row is not None
        return written

    def embed_missing(
        self,
        embedder: Embedder,
        *,
        maximum: int,
        batch_size: int,
    ) -> int:
        if maximum < 1 or batch_size < 1:
            raise ValueError("embedding bounds must be positive")
        if not self.migrate():
            return 0
        documents = self._missing_documents(maximum)
        if not documents:
            return 0
        cached = self._cached_vectors(
            list({document.content_hash for document in documents})
        )
        generated = build_embedding_updates(
            [document for document in documents if document.content_hash not in cached],
            embedder,
            model=self.embedding_model,
            dimension=self.embedding_dimension,
            batch_size=batch_size,
        )
        generated_by_id = {update.document_id: update for update in generated}
        updates = []
        for document in documents:
            if document.content_hash in cached:
                updates.append(
                    EmbeddingUpdate(
                        document_id=document.document_id,
                        content_hash=document.content_hash,
                        model=self.embedding_model,
                        embedding=cached[document.content_hash],
                    )
                )
            else:
                updates.append(generated_by_id[document.document_id])
        return self._write_embeddings(updates, batch_size)

    @staticmethod
    def _evidence_event_ids(evidence: list[dict[str, Any]]) -> list[UUID]:
        result = []
        for item in evidence:
            raw = item.get("event_id")
            if raw is None and str(item.get("evidence_id", "")).startswith(
                "archive-event:"
            ):
                raw = str(item["evidence_id"]).split(":", 1)[1]
            if raw is not None:
                result.append(UUID(str(raw)))
        return result

    def _query_documents(
        self,
        replica_id: UUID,
        evidence: list[dict[str, Any]],
        maximum: int,
    ) -> list[dict[str, Any]]:
        event_ids = self._evidence_event_ids(evidence)
        if not event_ids:
            return []
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT document_id, archive_event_id, page_path, content_text, "
                "to_json(embedding::real[]) FROM remote_search_documents "
                "WHERE replica_id = %s AND archive_event_id = ANY(%s) "
                "AND embedding_model = %s AND embedding IS NOT NULL "
                "ORDER BY local_sequence, chunk_index LIMIT %s",
                (replica_id, event_ids, self.embedding_model, maximum),
            ).fetchall()
        return [
            {
                "document_id": row[0],
                "evidence_id": f"archive-event:{row[1]}",
                "page_path": row[2],
                "content_text": row[3],
                "embedding": row[4],
            }
            for row in rows
        ]

    @staticmethod
    def _current_page_where() -> str:
        return (
            "d.replica_id = %s AND d.document_kind = 'page' "
            "AND d.local_sequence <= %s AND d.embedding_model = %s "
            "AND d.local_sequence = (SELECT max(newer.local_sequence) "
            "FROM remote_search_documents newer WHERE newer.replica_id = d.replica_id "
            "AND newer.document_kind = 'page' AND newer.page_path = d.page_path "
            "AND newer.local_sequence <= %s) "
            "AND NOT EXISTS (SELECT 1 FROM archive_events receipt "
            "JOIN curation_patches patch ON patch.patch_id = "
            "(receipt.payload->>'patch_id')::uuid "
            "WHERE receipt.replica_id = d.replica_id "
            "AND receipt.entity_kind = 'curation_receipt' "
            "AND receipt.local_sequence <= %s "
            "AND receipt.payload->>'status' IN ('applied', 'merged') "
            "AND patch.operation = 'supersede_page' AND patch.path = d.page_path) "
        )

    def _vector_paths(
        self,
        replica_id: UUID,
        watermark: int,
        query: dict[str, Any],
        maximum: int,
    ) -> list[str]:
        where = self._current_page_where()
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT d.page_path FROM remote_search_documents d WHERE "
                + where
                + "AND d.embedding IS NOT NULL "
                "AND d.page_path IS DISTINCT FROM %s "
                "ORDER BY d.embedding <=> (%s::real[])::vector, "
                "d.page_path, d.chunk_index LIMIT %s",
                (
                    replica_id,
                    watermark,
                    self.embedding_model,
                    watermark,
                    watermark,
                    query["page_path"],
                    query["embedding"],
                    maximum,
                ),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def _keyword_paths(
        self,
        replica_id: UUID,
        watermark: int,
        query: dict[str, Any],
        maximum: int,
    ) -> list[str]:
        where = self._current_page_where()
        score = (
            "d.content_tsv <@> to_bm25query("
            f"to_tsvector('english', %s), '{_BM25_INDEX}')"
        )
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT d.page_path, "
                + score
                + " AS score FROM remote_search_documents d WHERE "
                + where
                + "AND d.page_path IS DISTINCT FROM %s "
                "ORDER BY score, d.page_path, d.chunk_index LIMIT %s",
                (
                    query["content_text"],
                    replica_id,
                    watermark,
                    self.embedding_model,
                    watermark,
                    watermark,
                    query["page_path"],
                    maximum,
                ),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def _current_pages(
        self,
        replica_id: UUID,
        watermark: int,
        paths: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not paths:
            return {}
        with self.store.connection() as conn:
            rows = conn.execute(
                "WITH ranked AS (SELECT event_id, version_id, payload_hash, payload, "
                "row_number() OVER (PARTITION BY payload->>'path' "
                "ORDER BY local_sequence DESC) AS rank FROM archive_events "
                "WHERE replica_id = %s AND entity_kind = 'page_version' "
                "AND local_sequence <= %s AND payload->>'path' = ANY(%s)) "
                "SELECT event_id, version_id, payload_hash, payload "
                "FROM ranked WHERE rank = 1",
                (replica_id, watermark, paths),
            ).fetchall()
        pages = {}
        for event_id, version_id, content_hash, raw_payload in rows:
            payload = dict(raw_payload)
            pages[payload["path"]] = {
                "evidence_id": f"archive-event:{event_id}",
                "path": payload["path"],
                "title": payload["title"],
                "page_type": payload["page_type"],
                "content": payload["content"],
                "tags": list(payload.get("tags") or []),
                "source_ids": list(payload.get("source_ids") or []),
                "base_version_id": str(version_id),
                "base_content_hash": content_hash,
            }
        return pages

    def candidates(
        self,
        replica_id: UUID,
        watermark: int,
        evidence: list[dict[str, Any]],
        *,
        maximum_queries: int,
        pages_per_query: int,
    ) -> CandidateSelection:
        if maximum_queries < 1 or pages_per_query < 1:
            raise ValueError("hybrid search bounds must be positive")
        if not self.migrate():
            return CandidateSelection("unavailable", (), (), 0, 0, 0)
        queries = self._query_documents(replica_id, evidence, maximum_queries)
        groups = []
        page_order: list[str] = []
        vector_matches = 0
        keyword_matches = 0
        candidate_limit = pages_per_query * 4
        for query in queries:
            vector_paths = self._vector_paths(
                replica_id,
                watermark,
                query,
                candidate_limit,
            )
            keyword_paths = self._keyword_paths(
                replica_id,
                watermark,
                query,
                candidate_limit,
            )
            vector_matches += len(vector_paths)
            keyword_matches += len(keyword_paths)
            ranked = reciprocal_rank_fusion(
                vector_paths,
                keyword_paths,
                maximum=pages_per_query,
            )
            groups.append((query, ranked))
            for item in ranked:
                if item.path not in page_order:
                    page_order.append(item.path)
        current = self._current_pages(replica_id, watermark, page_order)
        pages = tuple(current[path] for path in page_order if path in current)
        similarity = []
        for query, ranked in groups:
            candidates = [
                {
                    "path": item.path,
                    "evidence_id": current[item.path]["evidence_id"],
                    "vector_rank": item.vector_rank,
                    "keyword_rank": item.keyword_rank,
                    "rrf_score": item.rrf_score,
                }
                for item in ranked
                if item.path in current
            ]
            similarity.append(
                {
                    "query_evidence_id": query["evidence_id"],
                    "query_document_id": str(query["document_id"]),
                    "candidates": candidates,
                }
            )
        return CandidateSelection(
            "available",
            pages,
            tuple(similarity),
            len(queries),
            vector_matches,
            keyword_matches,
        )


__all__ = ["LakebaseHybridSearch"]
