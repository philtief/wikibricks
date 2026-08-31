"""Transactional PostgreSQL storage for local-first WikiBricks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.session_ingest import (
    session_content_hash,
    session_identity,
    session_page_path,
    session_tags,
)

MAX_SEARCH_CHUNK_BYTES = 64 * 1024
_MIGRATION_LOCK_KEY = "wikibricks:schema-migrations"
_NO_PRECONDITION = object()


@dataclass(frozen=True, slots=True)
class IngestResult:
    created_events: int
    updated_events: int
    unchanged_events: int


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def page_content_hash(
    *,
    title: str,
    page_type: str,
    content: dict[str, Any],
    content_text: str,
    tags: list[str] | None,
    source_ids: list[str] | None,
    parent_id: str | None,
    chunk_index: int | None,
) -> str:
    return _canonical_hash(
        {
            "title": title,
            "page_type": page_type,
            "content": content,
            "content_text": content_text,
            "tags": tags or [],
            "source_ids": source_ids,
            "parent_id": parent_id,
            "chunk_index": chunk_index,
        }
    )


def iter_search_chunks(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield character offsets and text under the UTF-8 byte ceiling."""
    start = 0
    length = len(text)
    while start < length:
        end = min(start + MAX_SEARCH_CHUNK_BYTES, length)
        candidate = text[start:end]
        if len(candidate.encode("utf-8")) > MAX_SEARCH_CHUNK_BYTES:
            low, high = start + 1, end
            while low < high:
                middle = (low + high + 1) // 2
                if len(text[start:middle].encode("utf-8")) <= MAX_SEARCH_CHUNK_BYTES:
                    low = middle
                else:
                    high = middle - 1
            end = low
        if end < length:
            paragraph = text.rfind("\n\n", start, end)
            if paragraph > start:
                end = paragraph + 2
        if end <= start:
            end = start + 1
        yield start, end, text[start:end]
        start = end
    if not text:
        yield 0, 0, ""


class PostgresStore:
    def __init__(
        self,
        database_url: str | None = None,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if database_url is None:
            from wikibricks.config import load_config

            database_url = load_config().database_url
        self.database_url = database_url
        self._failpoint = failpoint or (lambda _stage: None)

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with psycopg.connect(self.database_url) as conn:
            yield conn

    def migrate(self) -> None:
        migration_dir = Path(__file__).with_name("sql") / "migrations"
        migrations = sorted(migration_dir.glob("*.sql"))
        if not migrations:
            raise RuntimeError("WikiBricks PostgreSQL migrations are missing")
        with self.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_MIGRATION_LOCK_KEY,))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            applied = {
                row[0]
                for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
            }
            for migration in migrations:
                if migration.name in applied:
                    continue
                conn.execute(migration.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations (name) VALUES (%s)",
                    (migration.name,),
                )

    def clear_all(self) -> None:
        tables = (
            "curation_conflicts, curation_receipts, page_aliases, curation_patches, "
            "curation_runs, sync_replicas, archive_batch_events, archive_events, "
            "archive_batches, curated_pages, archive_pages, sync_state, sync_outbox, "
            "session_search_chunks, "
            "session_event_versions, session_events, sessions, operations, sources, "
            "links, page_search_chunks, page_versions, pages"
        )
        with self.connection() as conn, conn.transaction():
            conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")

    def _insert_search_chunks(
        self,
        conn: Connection,
        table: str,
        version_id: UUID,
        text: str,
    ) -> None:
        statement = (
            f"INSERT INTO {table} "
            "(version_id, chunk_index, start_offset, end_offset, search_vector) "
            "VALUES (%s, %s, %s, %s, to_tsvector('simple', %s))"
        )
        for index, (start, end, chunk) in enumerate(iter_search_chunks(text)):
            conn.execute(statement, (version_id, index, start, end, chunk))

    def write_page(
        self,
        path: str,
        title: str,
        content_json: str | dict[str, Any],
        page_type: str = "concept",
        created_by: str = "agent",
        tags: list[str] | None = None,
        source_ids: list[str] | None = None,
        parent_id: str | None = None,
        chunk_index: int | None = None,
        content_text_override: str | None = None,
    ) -> str:
        with self.connection() as conn, conn.transaction():
            message, _version_id = self._write_page_in_connection(
                conn,
                path,
                title,
                content_json,
                page_type=page_type,
                created_by=created_by,
                tags=tags,
                source_ids=source_ids,
                parent_id=parent_id,
                chunk_index=chunk_index,
                content_text_override=content_text_override,
            )
            self._failpoint("before_commit")
        return message

    def _write_page_in_connection(
        self,
        conn: Connection,
        path: str,
        title: str,
        content_json: str | dict[str, Any],
        *,
        page_type: str = "concept",
        created_by: str = "agent",
        tags: list[str] | None = None,
        source_ids: list[str] | None = None,
        parent_id: str | None = None,
        chunk_index: int | None = None,
        content_text_override: str | None = None,
        expected_base_content_hash: str | None | object = _NO_PRECONDITION,
        curation_patch_id: UUID | None = None,
        preserve_llm_tags: bool = True,
    ) -> tuple[str, UUID | None]:
        if not path or "/" not in path:
            raise ValueError("wiki page path must contain a slash")
        content = json.loads(content_json) if isinstance(content_json, str) else content_json
        if not isinstance(content, dict):
            raise TypeError("page content must be a JSON object")
        content_text = content_text_override
        if content_text is None:
            content_text = " ".join(
                str(content.get(key) or "") for key in ("summary", "body")
            ).strip()
        page_hash = page_content_hash(
            title=title,
            page_type=page_type,
            content=content,
            content_text=content_text,
            tags=tags,
            source_ids=source_ids,
            parent_id=parent_id,
            chunk_index=chunk_index,
        )
        existing = conn.execute(
            "SELECT p.page_id, v.version, v.content_hash, v.tags, p.status "
            "FROM pages p LEFT JOIN page_versions v ON v.version_id = p.current_version_id "
            "WHERE p.path = %s FOR UPDATE OF p",
            (path,),
        ).fetchone()
        if existing and existing[4] != "active":
            raise ValueError(f"wiki page is superseded: {path}")
        if expected_base_content_hash is None and existing:
            raise RuntimeError(f"page already exists: {path}")
        if isinstance(expected_base_content_hash, str):
            if not existing or existing[2] != expected_base_content_hash:
                raise RuntimeError(f"page base content hash changed: {path}")
        if existing and existing[2] == page_hash:
            current_id = conn.execute(
                "SELECT current_version_id FROM pages WHERE page_id = %s", (existing[0],)
            ).fetchone()[0]
            return f"Wiki page unchanged: {path}", current_id
        if existing:
            page_id, current_version, _old_hash, old_tags, _status = existing
            version = int(current_version) + 1
            preserved = (
                [tag for tag in (old_tags or []) if tag.startswith("llm:")]
                if preserve_llm_tags
                else []
            )
            final_tags = list(dict.fromkeys([*(tags or []), *preserved]))
        else:
            page_id = uuid4()
            version = 1
            final_tags = tags or []
            conn.execute(
                "INSERT INTO pages (page_id, path) VALUES (%s, %s)",
                (page_id, path),
            )
        version_id = uuid4()
        conn.execute(
            "INSERT INTO page_versions "
            "(version_id, page_id, version, title, page_type, content, content_text, "
            "tags, source_ids, parent_id, chunk_index, created_by, content_hash, curation_patch_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                version_id,
                page_id,
                version,
                title,
                page_type,
                Jsonb(content),
                content_text,
                final_tags,
                source_ids,
                UUID(parent_id) if parent_id else None,
                chunk_index,
                created_by,
                page_hash,
                curation_patch_id,
            ),
        )
        conn.execute(
            "UPDATE pages SET current_version_id = %s, updated_at = now() WHERE page_id = %s",
            (version_id, page_id),
        )
        self._insert_search_chunks(conn, "page_search_chunks", version_id, content_text)
        conn.execute(
            "INSERT INTO operations (operation_id, op_type, path, details) "
            "VALUES (%s, 'write', %s, %s)",
            (
                uuid4(),
                path,
                Jsonb({"curation_patch_id": str(curation_patch_id)})
                if curation_patch_id
                else None,
            ),
        )
        conn.execute(
            "INSERT INTO sync_outbox "
            "(event_id, entity_kind, entity_id, version_id, payload_hash) "
            "VALUES (%s, 'page_version', %s, %s, %s)",
            (uuid4(), page_id, version_id, page_hash),
        )
        return f"Wrote wiki page: {path}", version_id

    @staticmethod
    def _page_row(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "page_id": str(row[0]),
            "path": row[1],
            "title": row[2],
            "page_type": row[3],
            "content": row[4],
            "content_text": row[5],
            "tags": list(row[6] or []),
            "created_by": row[7],
            "created_at": row[8],
            "updated_at": row[9],
            "version": row[10],
        }

    def current_page_state(self, path: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT p.page_id, p.path, p.status, p.superseded_by_page_id, "
                "v.version_id, v.version, v.title, v.page_type, v.content, v.content_text, "
                "v.tags, v.source_ids, v.parent_id, v.chunk_index, v.content_hash "
                "FROM pages p JOIN page_versions v ON v.version_id = p.current_version_id "
                "WHERE p.path = %s",
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

    def read_page(self, path: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT p.page_id, p.path, v.title, v.page_type, v.content, v.content_text, "
                "v.tags, v.created_by, p.created_at, p.updated_at, v.version "
                "FROM pages p JOIN page_versions v ON v.version_id = p.current_version_id "
                "WHERE p.path = %s AND p.status = 'active'",
                (path,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT p.page_id, p.path, v.title, v.page_type, v.content, v.content_text, "
                    "v.tags, v.created_by, p.created_at, p.updated_at, v.version "
                    "FROM page_aliases a JOIN pages p ON p.page_id = a.target_page_id "
                    "JOIN page_versions v ON v.version_id = p.current_version_id "
                    "WHERE a.alias_path = %s AND p.status = 'active'",
                    (path,),
                ).fetchone()
            if row:
                return self._page_row(row)
            session = conn.execute(
                "SELECT session_id, page_path, title, user_id, agent, started_at, updated_at, metadata "
                "FROM sessions WHERE page_path = %s",
                (path,),
            ).fetchone()
            if session:
                event_rows = self._read_session_event_rows(conn, session[0])
            else:
                archive = conn.execute(
                    "SELECT path, title, content, content_text, tags, snapshot_version, imported_at "
                    "FROM archive_pages WHERE path = %s",
                    (path,),
                ).fetchone()
                if not archive:
                    return None
                return {
                    "page_id": None,
                    "path": archive[0],
                    "title": archive[1],
                    "page_type": "archive",
                    "content": archive[2],
                    "content_text": archive[3],
                    "tags": list(archive[4] or []),
                    "created_at": archive[6],
                    "updated_at": archive[6],
                    "version": archive[5],
                    "read_only": True,
                }
        events = [self._event_from_row(row) for row in event_rows]
        body = "\n\n".join(f"[{event.kind}]\n{event.content}" for event in events)
        return {
            "page_id": str(session[0]),
            "path": session[1],
            "title": session[2],
            "page_type": "session",
            "content": {"summary": session[2], "body": body},
            "content_text": body,
            "tags": session_tags(
                SessionRecord(
                    harness=path.split("-sessions/", 1)[0] if "-sessions/" in path else "claude-code",
                    external_id=str(session[0]),
                    user_id=session[3],
                    agent=session[4],
                    events=[],
                    metadata=session[7],
                )
            ),
            "created_at": session[5],
            "updated_at": session[6],
            "version": 1,
            "events": [event.to_dict() for event in events],
        }

    def list_pages(self, path_prefix: str | None = None) -> list[dict[str, Any]]:
        like = f"{path_prefix}%" if path_prefix else "%"
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT p.path, v.title, v.page_type, v.version "
                "FROM pages p JOIN page_versions v ON v.version_id = p.current_version_id "
                "WHERE p.path LIKE %s AND p.status = 'active' "
                "UNION ALL "
                "SELECT page_path, title, 'session', 1 FROM sessions WHERE page_path LIKE %s "
                "UNION ALL "
                "SELECT path, title, 'archive', snapshot_version FROM archive_pages WHERE path LIKE %s "
                "ORDER BY 1",
                (like, like, like),
            ).fetchall()
        return [
            {"path": row[0], "title": row[1], "page_type": row[2], "version": row[3]}
            for row in rows
        ]

    def history(self, path: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT p.page_id, p.path, v.title, v.page_type, v.content, v.content_text, "
                "v.tags, v.created_by, p.created_at, p.updated_at, v.version "
                "FROM pages p JOIN page_versions v ON v.page_id = p.page_id "
                "WHERE p.path = %s ORDER BY v.version DESC",
                (path,),
            ).fetchall()
        return [self._page_row(row) for row in rows]

    def search(self, query: str, *, num_results: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        pattern = f"%{query}%"
        sql = """
            WITH q AS (SELECT websearch_to_tsquery('simple', %s) AS value),
            page_hits AS (
                SELECT p.page_id AS id, p.path, v.title, v.page_type, v.content_text, v.tags,
                       v.version,
                       GREATEST(
                           CASE WHEN p.path = %s THEN 10.0 ELSE 0.0 END,
                           similarity(p.path, %s) * 3.0,
                           similarity(v.title, %s) * 2.0,
                           COALESCE(max(ts_rank_cd(c.search_vector, q.value)), 0.0)
                       ) AS score
                FROM pages p
                JOIN page_versions v ON v.version_id = p.current_version_id
                LEFT JOIN page_search_chunks c ON c.version_id = v.version_id
                CROSS JOIN q
                WHERE p.status = 'active'
                  AND (p.path ILIKE %s OR v.title ILIKE %s OR c.search_vector @@ q.value)
                GROUP BY p.page_id, p.path, v.title, v.page_type, v.content_text, v.tags, v.version
            ),
            session_hits AS (
                SELECT s.session_id AS id, s.page_path AS path, s.title,
                       'session'::text AS page_type, ''::text AS content_text,
                       ARRAY['session', 'harness:' || s.harness]::text[] AS tags,
                       1 AS version,
                       GREATEST(
                           CASE WHEN s.page_path = %s THEN 10.0 ELSE 0.0 END,
                           similarity(s.page_path, %s) * 3.0,
                           similarity(s.title, %s) * 2.0,
                           COALESCE(max(ts_rank_cd(c.search_vector, q.value)), 0.0)
                       ) AS score
                FROM sessions s
                JOIN session_events e ON e.session_id = s.session_id AND e.active
                JOIN session_event_versions v ON v.version_id = e.current_version_id
                LEFT JOIN session_search_chunks c ON c.version_id = v.version_id
                CROSS JOIN q
                WHERE s.page_path ILIKE %s OR s.title ILIKE %s OR c.search_vector @@ q.value
                GROUP BY s.session_id, s.page_path, s.title, s.harness
            ),
            archive_hits AS (
                SELECT NULL::uuid AS id, a.path, a.title, 'archive'::text AS page_type,
                       a.content_text, a.tags, a.snapshot_version AS version,
                       GREATEST(
                           CASE WHEN a.path = %s THEN 9.0 ELSE 0.0 END,
                           similarity(a.path, %s) * 2.5,
                           similarity(a.title, %s) * 1.5,
                           CASE WHEN to_tsvector('simple', a.content_text) @@ q.value
                                THEN ts_rank_cd(to_tsvector('simple', a.content_text), q.value) - 0.25
                                ELSE 0.0 END
                       ) AS score
                FROM archive_pages a CROSS JOIN q
                WHERE a.path ILIKE %s OR a.title ILIKE %s
                   OR to_tsvector('simple', a.content_text) @@ q.value
            )
            SELECT * FROM (
                SELECT * FROM page_hits
                UNION ALL SELECT * FROM session_hits
                UNION ALL SELECT * FROM archive_hits
            ) hits
            ORDER BY score DESC, path
            LIMIT %s
        """
        params = (
            query,
            query,
            query,
            query,
            pattern,
            pattern,
            query,
            query,
            query,
            pattern,
            pattern,
            query,
            query,
            query,
            pattern,
            pattern,
            num_results,
        )
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "page_id": str(row[0]) if row[0] is not None else None,
                "path": row[1],
                "title": row[2],
                "page_type": row[3],
                "content_text": row[4],
                "tags": list(row[5] or []),
                "version": row[6],
                "score": float(row[7]),
            }
            for row in rows
        ]

    @staticmethod
    def _session_title(record: SessionRecord) -> str:
        configured = record.metadata.get("title")
        if configured:
            return str(configured)[:120]
        for event in record.events:
            if event.kind == "user" and event.content.strip():
                return event.content.strip().splitlines()[0][:120]
        return f"Session {record.external_id[:8]}"

    @staticmethod
    def _event_hash(event: SessionEvent) -> str:
        return _canonical_hash(event.to_dict())

    def ingest_session(self, record: SessionRecord) -> IngestResult:
        stable_id = session_identity(record)
        record_hash = session_content_hash(record)
        created = updated = unchanged = 0
        with self.connection() as conn, conn.transaction():
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
                    "started_at = %s, source_updated_at = %s, page_path = %s, title = %s, "
                    "metadata = %s, current_hash = %s, updated_at = now() WHERE session_id = %s",
                    (
                        record.user_id,
                        record.agent,
                        record.workspace,
                        record.started_at,
                        record.updated_at,
                        session_page_path(record),
                        self._session_title(record),
                        Jsonb(record.metadata),
                        record_hash,
                        stable_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO sessions "
                    "(session_id, harness, external_id, user_id, agent, workspace, started_at, "
                    "source_updated_at, page_path, title, metadata, current_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                        self._session_title(record),
                        Jsonb(record.metadata),
                        record_hash,
                    ),
                )
            active_ids: list[str] = []
            for position, event in enumerate(record.events):
                active_ids.append(event.external_id)
                event_id = uuid5(stable_id, event.external_id)
                event_hash = self._event_hash(event)
                existing_event = conn.execute(
                    "SELECT e.event_id, v.version, v.content_hash "
                    "FROM session_events e "
                    "LEFT JOIN session_event_versions v ON v.version_id = e.current_version_id "
                    "WHERE e.session_id = %s AND e.external_id = %s FOR UPDATE OF e",
                    (stable_id, event.external_id),
                ).fetchone()
                if existing_event and existing_event[2] == event_hash:
                    conn.execute(
                        "UPDATE session_events SET position = %s, active = true WHERE event_id = %s",
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
                        "INSERT INTO session_events (event_id, session_id, external_id, position) "
                        "VALUES (%s, %s, %s, %s)",
                        (event_id, stable_id, event.external_id, position),
                    )
                version_id = uuid4()
                conn.execute(
                    "INSERT INTO session_event_versions "
                    "(version_id, event_id, version, kind, content, metadata, source_created_at, content_hash) "
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
                    "UPDATE session_events SET position = %s, current_version_id = %s, active = true "
                    "WHERE event_id = %s",
                    (position, version_id, event_id),
                )
                self._insert_search_chunks(
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
            self._failpoint("before_commit")
        return IngestResult(created, updated, unchanged)

    @staticmethod
    def _read_session_event_rows(conn: Connection, session_id: UUID) -> list[tuple[Any, ...]]:
        return conn.execute(
            "SELECT e.external_id, v.kind, v.content, v.source_created_at, v.metadata "
            "FROM session_events e "
            "JOIN session_event_versions v ON v.version_id = e.current_version_id "
            "WHERE e.session_id = %s AND e.active ORDER BY e.position",
            (session_id,),
        ).fetchall()

    @staticmethod
    def _event_from_row(row: tuple[Any, ...]) -> SessionEvent:
        return SessionEvent(
            external_id=row[0],
            kind=row[1],
            content=row[2],
            created_at=row[3].isoformat() if row[3] else None,
            metadata=row[4],
        )

    def read_session_events(self, harness: str, external_id: str) -> list[SessionEvent]:
        with self.connection() as conn:
            session = conn.execute(
                "SELECT session_id FROM sessions WHERE harness = %s AND external_id = %s",
                (harness, external_id),
            ).fetchone()
            if not session:
                return []
            rows = self._read_session_event_rows(conn, session[0])
        return [self._event_from_row(row) for row in rows]

    def session_event_versions(self, harness: str, session_external_id: str, event_external_id: str) -> int:
        with self.connection() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM session_event_versions v "
                    "JOIN session_events e ON e.event_id = v.event_id "
                    "JOIN sessions s ON s.session_id = e.session_id "
                    "WHERE s.harness = %s AND s.external_id = %s AND e.external_id = %s",
                    (harness, session_external_id, event_external_id),
                ).fetchone()[0]
            )

    def outbox_count(self) -> int:
        with self.connection() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM sync_outbox WHERE acknowledged_at IS NULL"
                ).fetchone()[0]
            )

    def get_sync_cursor(self, target: str) -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT cursor FROM sync_state WHERE target = %s", (target,)
            ).fetchone()
        return dict(row[0]) if row else {}

    def set_sync_cursor(self, target: str, cursor: dict[str, Any]) -> None:
        with self.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO sync_state (target, cursor) VALUES (%s, %s) "
                "ON CONFLICT (target) DO UPDATE "
                "SET cursor = excluded.cursor, updated_at = now()",
                (target, Jsonb(cursor)),
            )

    def pending_outbox(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connection() as conn:
            first = conn.execute(
                "SELECT batch_id FROM sync_outbox WHERE acknowledged_at IS NULL "
                "ORDER BY sequence LIMIT 1"
            ).fetchone()
            if not first:
                return []
            if first[0]:
                rows = conn.execute(
                    "SELECT sequence, event_id, entity_kind, entity_id, version_id, "
                    "payload_hash, batch_id FROM sync_outbox "
                    "WHERE acknowledged_at IS NULL AND batch_id = %s ORDER BY sequence",
                    (first[0],),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT sequence, event_id, entity_kind, entity_id, version_id, "
                    "payload_hash, batch_id FROM sync_outbox "
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

    def assign_outbox_batch(self, sequences: list[int], batch_id: UUID) -> None:
        with self.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE sync_outbox SET batch_id = %s "
                "WHERE sequence = ANY(%s) AND acknowledged_at IS NULL "
                "AND (batch_id IS NULL OR batch_id = %s)",
                (batch_id, sequences, batch_id),
            )

    def acknowledge_outbox_batch(self, batch_id: UUID) -> int:
        with self.connection() as conn, conn.transaction():
            result = conn.execute(
                "UPDATE sync_outbox SET acknowledged_at = now() "
                "WHERE batch_id = %s AND acknowledged_at IS NULL",
                (batch_id,),
            )
        return result.rowcount

    def log(
        self,
        op_type: str,
        *,
        path: str | None = None,
        query: str | None = None,
        details: Any = None,
    ) -> None:
        with self.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO operations (operation_id, op_type, path, query, details) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    uuid4(),
                    op_type,
                    path,
                    query,
                    Jsonb(details) if details is not None else None,
                ),
            )

    def ingest_source(
        self,
        uri: str,
        *,
        title: str | None = None,
        content_text: str | None = None,
        source_type: str = "manual",
    ) -> str:
        source_id = uuid5(NAMESPACE_URL, f"wikibricks:source:{source_type}:{uri}")
        metadata = {"title": title, "content_text": content_text}
        with self.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO sources (source_id, source_type, uri, metadata) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (source_id) DO UPDATE SET metadata = excluded.metadata",
                (source_id, source_type, uri, Jsonb(metadata)),
            )
        return str(source_id)

    def commit_edges(self, edges: list[dict[str, Any]]) -> int:
        written = 0
        with self.connection() as conn, conn.transaction():
            for edge in edges:
                source_path = edge.get("source_path")
                target_path = edge.get("target_path")
                source_id = edge.get("source_page_id")
                target_id = edge.get("target_page_id")
                if source_path:
                    row = conn.execute(
                        "SELECT page_id FROM pages WHERE path = %s", (source_path,)
                    ).fetchone()
                    source_id = row[0] if row else None
                if target_path:
                    row = conn.execute(
                        "SELECT page_id FROM pages WHERE path = %s", (target_path,)
                    ).fetchone()
                    target_id = row[0] if row else None
                if not source_id or not target_id:
                    continue
                link_type = str(edge.get("link_type") or "related")
                origin = str(edge.get("origin") or "manual")
                metadata = {
                    key: edge[key]
                    for key in ("confidence", "evidence")
                    if key in edge
                }
                result = conn.execute(
                    "INSERT INTO links "
                    "(link_id, source_page_id, target_page_id, link_type, origin, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (source_page_id, target_page_id, link_type) "
                    "DO UPDATE SET origin = excluded.origin, metadata = excluded.metadata",
                    (
                        uuid4(),
                        UUID(str(source_id)),
                        UUID(str(target_id)),
                        link_type,
                        origin,
                        Jsonb(metadata),
                    ),
                )
                written += result.rowcount
        return written

    def graph_neighbors(
        self,
        path: str,
        *,
        depth: int = 1,
        link_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if depth != 1:
            raise ValueError("local graph traversal currently supports depth=1")
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT target.path, target_version.title, l.link_type, l.origin, l.metadata "
                "FROM pages source "
                "JOIN links l ON l.source_page_id = source.page_id "
                "JOIN pages target ON target.page_id = l.target_page_id "
                "JOIN page_versions target_version "
                "  ON target_version.version_id = target.current_version_id "
                "WHERE source.path = %s AND (%s::text[] IS NULL OR l.link_type = ANY(%s)) "
                "ORDER BY target.path",
                (path, link_types, link_types),
            ).fetchall()
        return [
            {
                "path": row[0],
                "title": row[1],
                "link_type": row[2],
                "origin": row[3],
                "metadata": row[4],
            }
            for row in rows
        ]
