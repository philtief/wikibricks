"""Operation log, sources, and page links."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb

if TYPE_CHECKING:
    from wikibricks.storage.store import PostgresStore


class GraphRepository:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    def log(
        self,
        op_type: str,
        *,
        path: str | None = None,
        query: str | None = None,
        details: Any = None,
    ) -> None:
        with self.store.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO operations "
                "(operation_id, op_type, path, query, details) "
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
        source_id = uuid5(
            NAMESPACE_URL,
            f"wikibricks:source:{source_type}:{uri}",
        )
        metadata = {"title": title, "content_text": content_text}
        with self.store.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO sources (source_id, source_type, uri, metadata) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (source_id) DO UPDATE "
                "SET metadata = excluded.metadata",
                (source_id, source_type, uri, Jsonb(metadata)),
            )
        return str(source_id)

    def commit_edges(self, edges: list[dict[str, Any]]) -> int:
        written = 0
        with self.store.connection() as conn, conn.transaction():
            for edge in edges:
                source_path = edge.get("source_path")
                target_path = edge.get("target_path")
                source_id = edge.get("source_page_id")
                target_id = edge.get("target_page_id")
                if source_path:
                    row = conn.execute(
                        "SELECT page_id FROM pages WHERE path = %s",
                        (source_path,),
                    ).fetchone()
                    source_id = row[0] if row else None
                if target_path:
                    row = conn.execute(
                        "SELECT page_id FROM pages WHERE path = %s",
                        (target_path,),
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
                    "(link_id, source_page_id, target_page_id, link_type, "
                    "origin, metadata) VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (source_page_id, target_page_id, link_type) "
                    "DO UPDATE SET origin = excluded.origin, "
                    "metadata = excluded.metadata",
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

    def neighbors(
        self,
        path: str,
        *,
        depth: int = 1,
        link_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if depth != 1:
            raise ValueError("local graph traversal currently supports depth=1")
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT target.path, target_version.title, l.link_type, "
                "l.origin, l.metadata FROM pages source "
                "JOIN links l ON l.source_page_id = source.page_id "
                "JOIN pages target ON target.page_id = l.target_page_id "
                "JOIN page_versions target_version "
                "ON target_version.version_id = target.current_version_id "
                "WHERE source.path = %s "
                "AND (%s::text[] IS NULL OR l.link_type = ANY(%s)) "
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
