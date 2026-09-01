"""Local SQLite storage for WikiBricks."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from wikibricks.models import IngestResult, SessionEvent, SessionRecord
from wikibricks.session_ingest import (
    session_content_hash,
    session_identity,
    session_page_path,
)
from wikibricks.storage.content import (
    canonical_hash,
    iter_search_chunks,
    page_content_hash,
)

DEFAULT_DATABASE_PATH = Path.home() / ".wikibricks" / "wikibricks.db"
_UNSET = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _insert_chunks(
    conn: sqlite3.Connection,
    *,
    table: str,
    fts_table: str,
    version_id: str,
    text: str,
) -> None:
    if (table, fts_table) not in {
        ("page_search_chunks", "page_search_fts"),
        ("session_search_chunks", "session_search_fts"),
    }:
        raise ValueError("unsupported search chunk table")
    for index, (start, end, chunk) in enumerate(iter_search_chunks(text)):
        conn.execute(
            f"INSERT INTO {table}(version_id, chunk_index, start_offset, end_offset, chunk_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (version_id, index, start, end, chunk),
        )
        conn.execute(
            f"INSERT INTO {fts_table}(version_id, chunk_text) VALUES (?, ?)",
            (version_id, chunk),
        )


class SQLiteStore:
    """Own SQLite connections and expose the local storage contract."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        configured = database_path or os.environ.get("WIKIBRICKS_DATABASE_PATH")
        self.database_path = Path(configured or DEFAULT_DATABASE_PATH).expanduser()
        self._failpoint = failpoint or (lambda _stage: None)

    def _open(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self.database_path.parent.chmod(0o700)
        conn = sqlite3.connect(self.database_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self._open()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except Exception:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        migration_dir = Path(__file__).parents[1] / "sql" / "sqlite"
        migrations = sorted(migration_dir.glob("*.sql"))
        if not migrations:
            raise RuntimeError("WikiBricks SQLite migrations are missing")
        with self.connection(write=True) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in conn.execute("SELECT name FROM schema_migrations")
            }
            for migration in migrations:
                if migration.name in applied:
                    continue
                for statement in migration.read_text().split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                    (migration.name, _now()),
                )

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
        with self.connection(write=True) as conn:
            existing = conn.execute(
                "SELECT p.page_id, p.status, v.version, v.content_hash "
                "FROM pages p LEFT JOIN page_versions v "
                "ON v.version_id = p.current_version_id WHERE p.path = ?",
                (path,),
            ).fetchone()
            if existing and existing["status"] != "active":
                raise ValueError(f"wiki page is superseded: {path}")
            if existing and existing["content_hash"] == page_hash:
                return f"Wiki page unchanged: {path}"
            timestamp = _now()
            if existing:
                page_id = existing["page_id"]
                version = int(existing["version"]) + 1
            else:
                page_id = str(uuid4())
                version = 1
                conn.execute(
                    "INSERT INTO pages(page_id, path, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (page_id, path, timestamp, timestamp),
                )
            version_id = str(uuid4())
            conn.execute(
                "INSERT INTO page_versions("
                "version_id, page_id, version, title, page_type, content, "
                "content_text, tags, source_ids, parent_id, chunk_index, created_by, "
                "content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    page_id,
                    version,
                    title,
                    page_type,
                    _json(content),
                    content_text,
                    _json(tags or []),
                    _json(source_ids) if source_ids is not None else None,
                    parent_id,
                    chunk_index,
                    created_by,
                    page_hash,
                    timestamp,
                ),
            )
            conn.execute(
                "UPDATE pages SET current_version_id = ?, updated_at = ? WHERE page_id = ?",
                (version_id, timestamp, page_id),
            )
            _insert_chunks(
                conn,
                table="page_search_chunks",
                fts_table="page_search_fts",
                version_id=version_id,
                text=content_text,
            )
            conn.execute(
                "INSERT INTO sync_outbox("
                "event_id, entity_kind, entity_id, version_id, payload_hash, created_at"
                ") VALUES (?, 'page_version', ?, ?, ?, ?)",
                (str(uuid4()), page_id, version_id, page_hash, timestamp),
            )
            self._failpoint("before_commit")
        return f"Wrote wiki page: {path}"

    def write_page_in_connection(
        self,
        conn: sqlite3.Connection,
        path: str,
        title: str,
        content: dict[str, Any],
        *,
        page_type: str,
        created_by: str,
        tags: list[str],
        source_ids: list[str] | None,
        parent_id: str | None,
        chunk_index: int | None,
        content_text: str,
        curation_patch_id: str,
        expected_base_content_hash: str | None | object = _UNSET,
    ) -> tuple[str, str]:
        """Write one curated page version inside an existing transaction."""
        existing = conn.execute(
            "SELECT p.page_id, p.status, v.version, v.content_hash "
            "FROM pages p LEFT JOIN page_versions v "
            "ON v.version_id = p.current_version_id WHERE p.path = ?",
            (path,),
        ).fetchone()
        if expected_base_content_hash is not _UNSET:
            if expected_base_content_hash is None and existing:
                raise RuntimeError(f"curation create path already exists: {path}")
            if expected_base_content_hash is not None and (
                not existing or existing["content_hash"] != expected_base_content_hash
            ):
                raise RuntimeError(f"curation base changed: {path}")
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
        timestamp = _now()
        if existing:
            if existing["status"] != "active":
                raise RuntimeError(f"curation target is not active: {path}")
            if existing["content_hash"] == page_hash:
                current = conn.execute(
                    "SELECT current_version_id FROM pages WHERE page_id = ?",
                    (existing["page_id"],),
                ).fetchone()[0]
                return f"Wiki page unchanged: {path}", str(current)
            page_id = existing["page_id"]
            version = int(existing["version"]) + 1
        else:
            page_id = str(uuid4())
            version = 1
            conn.execute(
                "INSERT INTO pages(page_id, path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (page_id, path, timestamp, timestamp),
            )
        version_id = str(uuid4())
        conn.execute(
            "INSERT INTO page_versions("
            "version_id, page_id, version, title, page_type, content, content_text, "
            "tags, source_ids, parent_id, chunk_index, created_by, content_hash, "
            "curation_patch_id, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                page_id,
                version,
                title,
                page_type,
                _json(content),
                content_text,
                _json(tags),
                _json(source_ids) if source_ids is not None else None,
                parent_id,
                chunk_index,
                created_by,
                page_hash,
                curation_patch_id,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE pages SET current_version_id = ?, updated_at = ? WHERE page_id = ?",
            (version_id, timestamp, page_id),
        )
        _insert_chunks(
            conn,
            table="page_search_chunks",
            fts_table="page_search_fts",
            version_id=version_id,
            text=content_text,
        )
        conn.execute(
            "INSERT INTO sync_outbox("
            "event_id, entity_kind, entity_id, version_id, payload_hash, created_at"
            ") VALUES (?, 'page_version', ?, ?, ?, ?)",
            (str(uuid4()), page_id, version_id, page_hash, timestamp),
        )
        return f"Wrote wiki page: {path}", version_id

    def read_page(self, path: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT p.page_id, p.path, v.title, v.page_type, v.content, "
                "v.content_text, v.tags, v.created_by, p.created_at, p.updated_at, "
                "v.version FROM pages p JOIN page_versions v "
                "ON v.version_id = p.current_version_id "
                "WHERE p.page_id = COALESCE("
                "(SELECT page_id FROM pages WHERE path = ? AND status = 'active'), "
                "(SELECT target_page_id FROM page_aliases WHERE alias_path = ?)) "
                "AND p.status = 'active'",
                (path, path),
            ).fetchone()
            if row:
                return {
                    "page_id": row["page_id"],
                    "path": row["path"],
                    "title": row["title"],
                    "page_type": row["page_type"],
                    "content": json.loads(row["content"]),
                    "content_text": row["content_text"],
                    "tags": json.loads(row["tags"]),
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "version": row["version"],
                }
            session = conn.execute(
                "SELECT session_id, page_path, title, created_at, updated_at "
                "FROM sessions WHERE page_path = ?",
                (path,),
            ).fetchone()
            if not session:
                return None
            events = self._read_event_rows(conn, session["session_id"])
        body = "\n\n".join(f"[{event.kind}]\n{event.content}" for event in events)
        return {
            "page_id": session["session_id"],
            "path": session["page_path"],
            "title": session["title"],
            "page_type": "session",
            "content": {"summary": session["title"], "body": body},
            "content_text": body,
            "tags": ["session"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "version": 1,
            "events": [event.to_dict() for event in events],
        }

    def list_pages(self, path_prefix: str | None = None) -> list[dict[str, Any]]:
        like = f"{path_prefix}%" if path_prefix else "%"
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT p.path, v.title, v.page_type, v.version "
                "FROM pages p JOIN page_versions v ON v.version_id = p.current_version_id "
                "WHERE p.path LIKE ? AND p.status = 'active' "
                "UNION ALL SELECT page_path, title, 'session', 1 "
                "FROM sessions WHERE page_path LIKE ? ORDER BY 1",
                (like, like),
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
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT v.version_id, v.version, v.title, v.page_type, v.content, "
                "v.content_text, v.tags, v.created_by, v.content_hash, v.created_at "
                "FROM page_versions v JOIN pages p ON p.page_id = v.page_id "
                "WHERE p.path = ? ORDER BY v.version DESC",
                (path,),
            ).fetchall()
        return [
            {
                "version_id": row["version_id"],
                "version": row["version"],
                "title": row["title"],
                "page_type": row["page_type"],
                "content": json.loads(row["content"]),
                "content_text": row["content_text"],
                "tags": json.loads(row["tags"]),
                "created_by": row["created_by"],
                "content_hash": row["content_hash"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def current_page_state(self, path: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT p.page_id, p.path, p.status, p.superseded_by_page_id, "
                "v.version_id, v.version, v.title, v.page_type, v.content, "
                "v.content_text, v.tags, v.source_ids, v.parent_id, v.chunk_index, "
                "v.content_hash FROM pages p JOIN page_versions v "
                "ON v.version_id = p.current_version_id WHERE p.path = ?",
                (path,),
            ).fetchone()
        if not row:
            return None
        return {
            "page_id": row["page_id"],
            "path": row["path"],
            "status": row["status"],
            "superseded_by_page_id": row["superseded_by_page_id"],
            "version_id": row["version_id"],
            "version": row["version"],
            "title": row["title"],
            "page_type": row["page_type"],
            "content": json.loads(row["content"]),
            "content_text": row["content_text"],
            "tags": json.loads(row["tags"]),
            "source_ids": (
                json.loads(row["source_ids"])
                if row["source_ids"] is not None
                else None
            ),
            "parent_id": row["parent_id"],
            "chunk_index": row["chunk_index"],
            "content_hash": row["content_hash"],
        }

    def outbox_count(self) -> int:
        with self.connection() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM sync_outbox WHERE acknowledged_at IS NULL"
                ).fetchone()[0]
            )

    def pending_outbox(self, limit: int = 1000) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("outbox limit must be positive")
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT sequence, event_id, entity_kind, entity_id, version_id, "
                "payload_hash, batch_id, created_at FROM sync_outbox "
                "WHERE acknowledged_at IS NULL ORDER BY sequence LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def assign_outbox_batch(self, sequences: list[int], batch_id: UUID) -> None:
        if not sequences:
            return
        placeholders = ",".join("?" for _ in sequences)
        with self.connection(write=True) as conn:
            conn.execute(
                "UPDATE sync_outbox SET batch_id = ? "
                f"WHERE sequence IN ({placeholders}) AND acknowledged_at IS NULL "
                "AND (batch_id IS NULL OR batch_id = ?)",
                (str(batch_id), *sequences, str(batch_id)),
            )

    def acknowledge_outbox_batch(self, batch_id: UUID) -> int:
        with self.connection(write=True) as conn:
            before = conn.total_changes
            conn.execute(
                "UPDATE sync_outbox SET acknowledged_at = ? "
                "WHERE batch_id = ? AND acknowledged_at IS NULL",
                (_now(), str(batch_id)),
            )
            return conn.total_changes - before

    def get_sync_cursor(self, target: str) -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT cursor FROM sync_state WHERE target = ?",
                (target,),
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def set_sync_cursor(self, target: str, cursor: dict[str, Any]) -> None:
        with self.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO sync_state(target, cursor, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(target) DO UPDATE SET cursor = excluded.cursor, "
                "updated_at = excluded.updated_at",
                (target, _json(cursor), _now()),
            )

    def log(
        self,
        op_type: str,
        *,
        path: str | None = None,
        query: str | None = None,
        details: Any = None,
    ) -> None:
        with self.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO operations(operation_id, op_type, path, query, details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    op_type,
                    path,
                    query,
                    _json(details) if details is not None else None,
                    _now(),
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
        source_id = str(uuid5(NAMESPACE_URL, f"wikibricks:source:{source_type}:{uri}"))
        with self.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO sources(source_id, source_type, uri, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
                "metadata = excluded.metadata",
                (
                    source_id,
                    source_type,
                    uri,
                    _json({"title": title, "content_text": content_text}),
                    _now(),
                ),
            )
        return source_id

    def commit_edges(self, edges: list[dict[str, Any]]) -> int:
        written = 0
        with self.connection(write=True) as conn:
            for edge in edges:
                source_id = edge.get("source_page_id")
                target_id = edge.get("target_page_id")
                if edge.get("source_path"):
                    row = conn.execute(
                        "SELECT page_id FROM pages WHERE path = ?",
                        (edge["source_path"],),
                    ).fetchone()
                    source_id = row[0] if row else None
                if edge.get("target_path"):
                    row = conn.execute(
                        "SELECT page_id FROM pages WHERE path = ?",
                        (edge["target_path"],),
                    ).fetchone()
                    target_id = row[0] if row else None
                if not source_id or not target_id:
                    continue
                before = conn.total_changes
                conn.execute(
                    "INSERT INTO links(link_id, source_page_id, target_page_id, link_type, "
                    "origin, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_page_id, target_page_id, link_type) DO UPDATE SET "
                    "origin = excluded.origin, metadata = excluded.metadata",
                    (
                        str(uuid4()),
                        str(source_id),
                        str(target_id),
                        str(edge.get("link_type") or "related"),
                        str(edge.get("origin") or "manual"),
                        _json(
                            {
                                key: edge[key]
                                for key in ("confidence", "evidence")
                                if key in edge
                            }
                        ),
                        _now(),
                    ),
                )
                written += int(conn.total_changes > before)
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
        params: list[Any] = [path]
        filter_sql = ""
        if link_types:
            filter_sql = f" AND l.link_type IN ({','.join('?' for _ in link_types)})"
            params.extend(link_types)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT target.path, v.title, l.link_type, l.origin, l.metadata "
                "FROM pages source JOIN links l ON l.source_page_id = source.page_id "
                "JOIN pages target ON target.page_id = l.target_page_id "
                "JOIN page_versions v ON v.version_id = target.current_version_id "
                "WHERE source.path = ?" + filter_sql + " ORDER BY target.path",
                params,
            ).fetchall()
        return [
            {
                "path": row[0],
                "title": row[1],
                "link_type": row[2],
                "origin": row[3],
                "metadata": json.loads(row[4]),
            }
            for row in rows
        ]

    def acquire_lease(
        self,
        name: str,
        owner: str,
        ttl_seconds: int,
        *,
        now: float | None = None,
    ) -> bool:
        if ttl_seconds < 1:
            raise ValueError("lease ttl must be positive")
        current = datetime.now(timezone.utc).timestamp() if now is None else now
        expires = current + ttl_seconds
        with self.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO background_leases(name, owner, expires_at, renewed_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "owner = excluded.owner, expires_at = excluded.expires_at, "
                "renewed_at = excluded.renewed_at WHERE background_leases.owner = excluded.owner "
                "OR background_leases.expires_at <= excluded.renewed_at",
                (name, owner, expires, current),
            )
            lease = conn.execute(
                "SELECT owner, expires_at FROM background_leases WHERE name = ?",
                (name,),
            ).fetchone()
        return bool(lease and lease["owner"] == owner and lease["expires_at"] == expires)

    def repair_search_indexes(self) -> tuple[int, int]:
        with self.connection(write=True) as conn:
            page_rows = conn.execute(
                "SELECT v.version_id, v.content_text FROM pages p "
                "JOIN page_versions v ON v.version_id = p.current_version_id "
                "WHERE p.status = 'active' AND NOT EXISTS ("
                "SELECT 1 FROM page_search_chunks c WHERE c.version_id = v.version_id)"
            ).fetchall()
            for row in page_rows:
                _insert_chunks(
                    conn,
                    table="page_search_chunks",
                    fts_table="page_search_fts",
                    version_id=row["version_id"],
                    text=row["content_text"],
                )
            session_rows = conn.execute(
                "SELECT v.version_id, v.content FROM session_events e "
                "JOIN session_event_versions v ON v.version_id = e.current_version_id "
                "WHERE e.active = 1 AND NOT EXISTS ("
                "SELECT 1 FROM session_search_chunks c WHERE c.version_id = v.version_id)"
            ).fetchall()
            for row in session_rows:
                _insert_chunks(
                    conn,
                    table="session_search_chunks",
                    fts_table="session_search_fts",
                    version_id=row["version_id"],
                    text=row["content"],
                )
            conn.execute("DELETE FROM page_search_fts")
            conn.execute(
                "INSERT INTO page_search_fts(version_id, chunk_text) "
                "SELECT version_id, chunk_text FROM page_search_chunks ORDER BY chunk_id"
            )
            conn.execute("DELETE FROM session_search_fts")
            conn.execute(
                "INSERT INTO session_search_fts(version_id, chunk_text) "
                "SELECT version_id, chunk_text FROM session_search_chunks ORDER BY chunk_id"
            )
        return len(page_rows), len(session_rows)

    @staticmethod
    def _session_title(record: SessionRecord) -> str:
        configured = record.metadata.get("title")
        if configured:
            return str(configured)[:120]
        for event in record.events:
            if event.kind == "user" and event.content.strip():
                return event.content.strip().splitlines()[0][:120]
        return f"Session {record.external_id[:8]}"

    def ingest_session(self, record: SessionRecord) -> IngestResult:
        stable_id = str(session_identity(record))
        record_hash = session_content_hash(record)
        created = updated = unchanged = 0
        timestamp = _now()
        with self.connection(write=True) as conn:
            session = conn.execute(
                "SELECT session_id, current_hash FROM sessions "
                "WHERE harness = ? AND external_id = ?",
                (record.harness, record.external_id),
            ).fetchone()
            if session and session["current_hash"] == record_hash:
                return IngestResult(0, 0, len(record.events))
            if session:
                stable_id = session["session_id"]
                conn.execute(
                    "UPDATE sessions SET user_id = ?, agent = ?, workspace = ?, "
                    "started_at = ?, source_updated_at = ?, page_path = ?, title = ?, "
                    "metadata = ?, current_hash = ?, updated_at = ? WHERE session_id = ?",
                    (
                        record.user_id,
                        record.agent,
                        record.workspace,
                        record.started_at,
                        record.updated_at,
                        session_page_path(record),
                        self._session_title(record),
                        _json(record.metadata),
                        record_hash,
                        timestamp,
                        stable_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO sessions("
                    "session_id, harness, external_id, user_id, agent, workspace, "
                    "started_at, source_updated_at, page_path, title, metadata, current_hash, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        _json(record.metadata),
                        record_hash,
                        timestamp,
                        timestamp,
                    ),
                )
            active_ids: list[str] = []
            for position, event in enumerate(record.events):
                active_ids.append(event.external_id)
                event_id = str(uuid5(session_identity(record), event.external_id))
                event_hash = canonical_hash(event.to_dict())
                existing = conn.execute(
                    "SELECT e.event_id, v.version, v.content_hash "
                    "FROM session_events e LEFT JOIN session_event_versions v "
                    "ON v.version_id = e.current_version_id "
                    "WHERE e.session_id = ? AND e.external_id = ?",
                    (stable_id, event.external_id),
                ).fetchone()
                if existing and existing["content_hash"] == event_hash:
                    conn.execute(
                        "UPDATE session_events SET position = ?, active = 1 WHERE event_id = ?",
                        (position, existing["event_id"]),
                    )
                    unchanged += 1
                    continue
                if existing:
                    event_id = existing["event_id"]
                    version = int(existing["version"]) + 1
                    updated += 1
                else:
                    version = 1
                    created += 1
                    conn.execute(
                        "INSERT INTO session_events(event_id, session_id, external_id, position) "
                        "VALUES (?, ?, ?, ?)",
                        (event_id, stable_id, event.external_id, position),
                    )
                version_id = str(uuid4())
                conn.execute(
                    "INSERT INTO session_event_versions("
                    "version_id, event_id, version, kind, content, metadata, "
                    "source_created_at, content_hash, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        version_id,
                        event_id,
                        version,
                        event.kind,
                        event.content,
                        _json(event.metadata),
                        event.created_at,
                        event_hash,
                        timestamp,
                    ),
                )
                conn.execute(
                    "UPDATE session_events SET position = ?, current_version_id = ?, active = 1 "
                    "WHERE event_id = ?",
                    (position, version_id, event_id),
                )
                _insert_chunks(
                    conn,
                    table="session_search_chunks",
                    fts_table="session_search_fts",
                    version_id=version_id,
                    text=event.content,
                )
                conn.execute(
                    "INSERT INTO sync_outbox("
                    "event_id, entity_kind, entity_id, version_id, payload_hash, created_at"
                    ") VALUES (?, 'session_event_version', ?, ?, ?, ?)",
                    (str(uuid4()), event_id, version_id, event_hash, timestamp),
                )
            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                conn.execute(
                    "UPDATE session_events SET active = 0 WHERE session_id = ? "
                    f"AND external_id NOT IN ({placeholders})",
                    (stable_id, *active_ids),
                )
            else:
                conn.execute(
                    "UPDATE session_events SET active = 0 WHERE session_id = ?",
                    (stable_id,),
                )
            self._failpoint("before_commit")
        return IngestResult(created, updated, unchanged)

    @staticmethod
    def _read_event_rows(
        conn: sqlite3.Connection,
        session_id: str,
    ) -> list[SessionEvent]:
        rows = conn.execute(
            "SELECT e.external_id, v.kind, v.content, v.source_created_at, v.metadata "
            "FROM session_events e JOIN session_event_versions v "
            "ON v.version_id = e.current_version_id "
            "WHERE e.session_id = ? AND e.active = 1 ORDER BY e.position",
            (session_id,),
        ).fetchall()
        return [
            SessionEvent(
                external_id=row["external_id"],
                kind=row["kind"],
                content=row["content"],
                created_at=row["source_created_at"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def read_session_events(self, harness: str, external_id: str) -> list[SessionEvent]:
        with self.connection() as conn:
            session = conn.execute(
                "SELECT session_id FROM sessions WHERE harness = ? AND external_id = ?",
                (harness, external_id),
            ).fetchone()
            return [] if not session else self._read_event_rows(conn, session["session_id"])

    def session_event_versions(
        self,
        harness: str,
        session_external_id: str,
        event_external_id: str,
    ) -> int:
        with self.connection() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM session_event_versions v "
                    "JOIN session_events e ON e.event_id = v.event_id "
                    "JOIN sessions s ON s.session_id = e.session_id "
                    "WHERE s.harness = ? AND s.external_id = ? AND e.external_id = ?",
                    (harness, session_external_id, event_external_id),
                ).fetchone()[0]
            )

    def search(self, query: str, *, num_results: int = 5) -> list[dict[str, Any]]:
        tokens = re.findall(r"[\w-]+", query, flags=re.UNICODE)
        if not tokens:
            return []
        match = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        pattern = f"%{query}%"
        with self.connection() as conn:
            rows = conn.execute(
                "WITH page_hits AS ("
                "SELECT p.page_id, p.path, v.title, v.page_type, v.content_text, "
                "v.tags, v.version, CASE WHEN p.path = ? THEN 10.0 WHEN p.path LIKE ? "
                "THEN 6.0 WHEN v.title LIKE ? THEN 5.0 ELSE 3.0 END AS score "
                "FROM page_search_fts f JOIN page_versions v ON v.version_id = f.version_id "
                "JOIN pages p ON p.current_version_id = v.version_id "
                "WHERE page_search_fts MATCH ? AND p.status = 'active' "
                "GROUP BY v.version_id), session_hits AS ("
                "SELECT s.session_id, s.page_path, s.title, 'session', v.content, "
                "json_array('session', 'harness:' || s.harness), 1, 1.0 "
                "FROM session_search_fts f JOIN session_event_versions v "
                "ON v.version_id = f.version_id JOIN session_events e "
                "ON e.current_version_id = v.version_id JOIN sessions s "
                "ON s.session_id = e.session_id WHERE session_search_fts MATCH ? "
                "AND e.active = 1 GROUP BY v.version_id) "
                "SELECT * FROM (SELECT * FROM page_hits UNION ALL SELECT * FROM session_hits) "
                "ORDER BY score DESC, 2 LIMIT ?",
                (query, pattern, pattern, match, match, num_results),
            ).fetchall()
        return [
            {
                "page_id": row[0],
                "path": row[1],
                "title": row[2],
                "page_type": row[3],
                "content_text": row[4],
                "tags": json.loads(row[5]),
                "version": row[6],
                "score": float(row[7]),
            }
            for row in rows
        ]
