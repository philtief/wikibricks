"""PostgreSQL connection owner and compatibility facade."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection

from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.storage.graph import GraphRepository
from wikibricks.storage.outbox import OutboxRepository
from wikibricks.storage.pages import PageRepository
from wikibricks.storage.search import SearchRepository
from wikibricks.storage.sessions import IngestResult, SessionRepository

_MIGRATION_LOCK_KEY = "wikibricks:schema-migrations"


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
        self.pages = PageRepository(self)
        self.sessions = SessionRepository(self)
        self.search_index = SearchRepository(self)
        self.outbox = OutboxRepository(self)
        self.graph = GraphRepository(self)

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with psycopg.connect(self.database_url) as conn:
            yield conn

    def migrate(self) -> None:
        migration_dir = Path(__file__).parents[1] / "sql" / "migrations"
        migrations = sorted(migration_dir.glob("*.sql"))
        if not migrations:
            raise RuntimeError("WikiBricks PostgreSQL migrations are missing")
        with self.connection() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (_MIGRATION_LOCK_KEY,),
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            applied = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM schema_migrations"
                ).fetchall()
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
            "remote_maintenance_runs, curation_conflicts, curation_receipts, "
            "page_aliases, curation_patches, "
            "curation_runs, sync_replicas, archive_batch_events, archive_events, "
            "archive_batches, curated_pages, archive_pages, sync_state, sync_outbox, "
            "session_search_chunks, session_event_versions, session_events, sessions, "
            "operations, sources, links, page_search_chunks, page_versions, pages"
        )
        with self.connection() as conn, conn.transaction():
            conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")

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
        return self.pages.write(
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

    def current_page_state(self, path: str) -> dict[str, Any] | None:
        return self.pages.current_state(path)

    def read_page(self, path: str) -> dict[str, Any] | None:
        return self.pages.read(path)

    def list_pages(self, path_prefix: str | None = None) -> list[dict[str, Any]]:
        return self.pages.list(path_prefix)

    def history(self, path: str) -> list[dict[str, Any]]:
        return self.pages.history(path)

    def search(
        self,
        query: str,
        *,
        num_results: int = 5,
    ) -> list[dict[str, Any]]:
        return self.search_index.query(query, num_results=num_results)

    def ingest_session(self, record: SessionRecord) -> IngestResult:
        return self.sessions.ingest(record)

    def read_session_events(
        self,
        harness: str,
        external_id: str,
    ) -> list[SessionEvent]:
        return self.sessions.read_events(harness, external_id)

    def session_event_versions(
        self,
        harness: str,
        session_external_id: str,
        event_external_id: str,
    ) -> int:
        return self.sessions.event_versions(
            harness,
            session_external_id,
            event_external_id,
        )

    def outbox_count(self) -> int:
        return self.outbox.count()

    def get_sync_cursor(self, target: str) -> dict[str, Any]:
        return self.outbox.get_cursor(target)

    def set_sync_cursor(self, target: str, cursor: dict[str, Any]) -> None:
        self.outbox.set_cursor(target, cursor)

    def pending_outbox(self, limit: int = 1000) -> list[dict[str, Any]]:
        return self.outbox.pending(limit)

    def assign_outbox_batch(self, sequences: list[int], batch_id: UUID) -> None:
        self.outbox.assign_batch(sequences, batch_id)

    def acknowledge_outbox_batch(self, batch_id: UUID) -> int:
        return self.outbox.acknowledge_batch(batch_id)

    def log(
        self,
        op_type: str,
        *,
        path: str | None = None,
        query: str | None = None,
        details: Any = None,
    ) -> None:
        self.graph.log(op_type, path=path, query=query, details=details)

    def ingest_source(
        self,
        uri: str,
        *,
        title: str | None = None,
        content_text: str | None = None,
        source_type: str = "manual",
    ) -> str:
        return self.graph.ingest_source(
            uri,
            title=title,
            content_text=content_text,
            source_type=source_type,
        )

    def commit_edges(self, edges: list[dict[str, Any]]) -> int:
        return self.graph.commit_edges(edges)

    def graph_neighbors(
        self,
        path: str,
        *,
        depth: int = 1,
        link_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.graph.neighbors(
            path,
            depth=depth,
            link_types=link_types,
        )
