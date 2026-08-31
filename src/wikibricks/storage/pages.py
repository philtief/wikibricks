"""Versioned wiki page persistence."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from wikibricks.models import SessionRecord
from wikibricks.session_ingest import session_tags
from wikibricks.storage.content import insert_search_chunks, page_content_hash

if TYPE_CHECKING:
    from wikibricks.storage.store import PostgresStore

NO_PRECONDITION = object()


class PageRepository:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    def write(
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
        with self.store.connection() as conn, conn.transaction():
            message, _version_id = self.write_in_connection(
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
            self.store._failpoint("before_commit")
        return message

    def write_in_connection(
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
        expected_base_content_hash: str | None | object = NO_PRECONDITION,
        curation_patch_id: UUID | None = None,
        preserve_llm_tags: bool = True,
    ) -> tuple[str, UUID | None]:
        if not path or "/" not in path:
            raise ValueError("wiki page path must contain a slash")
        content = (
            json.loads(content_json)
            if isinstance(content_json, str)
            else content_json
        )
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
            "FROM pages p LEFT JOIN page_versions v "
            "ON v.version_id = p.current_version_id "
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
                "SELECT current_version_id FROM pages WHERE page_id = %s",
                (existing[0],),
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
            "(version_id, page_id, version, title, page_type, content, "
            "content_text, tags, source_ids, parent_id, chunk_index, created_by, "
            "content_hash, curation_patch_id) "
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
            "UPDATE pages SET current_version_id = %s, updated_at = now() "
            "WHERE page_id = %s",
            (version_id, page_id),
        )
        insert_search_chunks(
            conn,
            "page_search_chunks",
            version_id,
            content_text,
        )
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

    def current_state(self, path: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT p.page_id, p.path, p.status, p.superseded_by_page_id, "
                "v.version_id, v.version, v.title, v.page_type, v.content, "
                "v.content_text, v.tags, v.source_ids, v.parent_id, v.chunk_index, "
                "v.content_hash FROM pages p JOIN page_versions v "
                "ON v.version_id = p.current_version_id WHERE p.path = %s",
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

    def read(self, path: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT p.page_id, p.path, v.title, v.page_type, v.content, "
                "v.content_text, v.tags, v.created_by, p.created_at, p.updated_at, "
                "v.version FROM pages p JOIN page_versions v "
                "ON v.version_id = p.current_version_id "
                "WHERE p.path = %s AND p.status = 'active'",
                (path,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT p.page_id, p.path, v.title, v.page_type, v.content, "
                    "v.content_text, v.tags, v.created_by, p.created_at, "
                    "p.updated_at, v.version FROM page_aliases a "
                    "JOIN pages p ON p.page_id = a.target_page_id "
                    "JOIN page_versions v ON v.version_id = p.current_version_id "
                    "WHERE a.alias_path = %s AND p.status = 'active'",
                    (path,),
                ).fetchone()
            if row:
                return self._page_row(row)
            session = conn.execute(
                "SELECT session_id, page_path, title, user_id, agent, "
                "started_at, updated_at, metadata FROM sessions WHERE page_path = %s",
                (path,),
            ).fetchone()
            if session:
                event_rows = self.store.sessions.read_event_rows(conn, session[0])
            else:
                archive = conn.execute(
                    "SELECT path, title, content, content_text, tags, "
                    "snapshot_version, imported_at FROM archive_pages WHERE path = %s",
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
        events = [
            self.store.sessions.event_from_row(event_row)
            for event_row in event_rows
        ]
        body = "\n\n".join(
            f"[{event.kind}]\n{event.content}" for event in events
        )
        return {
            "page_id": str(session[0]),
            "path": session[1],
            "title": session[2],
            "page_type": "session",
            "content": {"summary": session[2], "body": body},
            "content_text": body,
            "tags": session_tags(
                SessionRecord(
                    harness=(
                        path.split("-sessions/", 1)[0]
                        if "-sessions/" in path
                        else "claude-code"
                    ),
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

    def list(self, path_prefix: str | None = None) -> list[dict[str, Any]]:
        like = f"{path_prefix}%" if path_prefix else "%"
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT p.path, v.title, v.page_type, v.version "
                "FROM pages p JOIN page_versions v "
                "ON v.version_id = p.current_version_id "
                "WHERE p.path LIKE %s AND p.status = 'active' "
                "UNION ALL SELECT page_path, title, 'session', 1 "
                "FROM sessions WHERE page_path LIKE %s "
                "UNION ALL SELECT path, title, 'archive', snapshot_version "
                "FROM archive_pages WHERE path LIKE %s ORDER BY 1",
                (like, like, like),
            ).fetchall()
        return [
            {
                "path": row[0],
                "title": row[1],
                "page_type": row[2],
                "version": row[3],
            }
            for row in rows
        ]

    def history(self, path: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT p.page_id, p.path, v.title, v.page_type, v.content, "
                "v.content_text, v.tags, v.created_by, p.created_at, p.updated_at, "
                "v.version FROM pages p JOIN page_versions v "
                "ON v.page_id = p.page_id WHERE p.path = %s "
                "ORDER BY v.version DESC",
                (path,),
            ).fetchall()
        return [self._page_row(row) for row in rows]
