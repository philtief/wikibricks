"""Local PostgreSQL implementation of the public WikiClient contract."""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from pathlib import Path
from typing import Any

from wikibricks.models import SessionRecord
from wikibricks.postgres_store import PostgresStore


class LocalWikiClient:
    def __init__(self, database_url: str | None = None, *, migrate: bool = True) -> None:
        self.store = PostgresStore(database_url)
        self.database_url = self.store.database_url
        if migrate:
            self.store.migrate()

    def write_page(self, *args, **kwargs) -> str:
        return self.store.write_page(*args, **kwargs)

    def write_pages(self, pages: list[dict[str, Any]]) -> int:
        written = 0
        for page in pages:
            content = page.get("content", page.get("content_json"))
            self.write_page(
                page["path"],
                page["title"],
                content,
                page_type=page.get("page_type", "concept"),
                created_by=page.get("created_by", "agent"),
                tags=page.get("tags"),
                source_ids=page.get("source_ids"),
                parent_id=page.get("parent_id"),
                chunk_index=page.get("chunk_index"),
                content_text_override=page.get("content_text_override"),
            )
            written += 1
        return written

    def read_page(self, path: str) -> dict[str, Any] | None:
        page = self.store.read_page(path)
        if page:
            self._log("read", path=path)
        return page

    def list_pages(
        self,
        path_prefix: str | None = None,
        *,
        include_ephemeral: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self.store.list_pages(path_prefix)
        if include_ephemeral:
            return rows
        return [row for row in rows if "ephemeral:stub" not in row.get("tags", [])]

    def history(self, path: str) -> list[dict[str, Any]]:
        return self.store.history(path)

    def search(
        self,
        query: str,
        mode: str = "HYBRID",
        num_results: int = 5,
        rerank_by_citations: bool | None = None,
        rerank_with_pagerank: bool | None = None,
        include_ephemeral: bool = False,
    ) -> list[dict[str, Any]]:
        del mode, rerank_by_citations, rerank_with_pagerank, include_ephemeral
        hits = self.store.search(query, num_results=num_results)
        self._log("search", query=query)
        return hits

    def ingest_session(self, record: SessionRecord):
        return self.store.ingest_session(record)

    def ingest_source(
        self,
        uri: str,
        title: str | None = None,
        content_text: str | None = None,
        source_type: str = "manual",
    ) -> str:
        self.store.ingest_source(
            uri,
            title=title,
            content_text=content_text,
            source_type=source_type,
        )
        self._log("ingest", details=uri)
        return f"Ingested source: {uri}"

    def promote_answer(
        self,
        query: str,
        answer: str,
        source_pages: list[dict[str, Any]],
        created_by: str = "chat",
    ) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:50]
        suffix = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
        path = f"synthesis/{slug}-{suffix}"
        self.write_page(
            path,
            query[:120],
            {"summary": query, "body": answer},
            page_type="synthesis",
            created_by=created_by,
            tags=["promoted"],
        )
        promoted = self.read_page(path)
        self.commit_edges(
            [
                {
                    "source_page_id": promoted["page_id"],
                    "target_page_id": source["page_id"],
                    "link_type": "cites",
                    "origin": "promote",
                }
                for source in source_pages
                if source and source.get("page_id")
            ]
        )
        self._log("promote", path=path, query=query)
        return path

    def bulk_write_pages(
        self,
        jsonl_path: str,
        source_tag: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        pages = [json.loads(line) for line in Path(jsonl_path).read_text().splitlines() if line.strip()]
        if dry_run:
            return {"written": 0, "would_write": len(pages), "source_tag": source_tag}
        written = self.write_pages(pages)
        return {"written": written, "would_write": len(pages), "source_tag": source_tag}

    def commit_edges(self, edges: list[dict[str, Any]]) -> int:
        return self.store.commit_edges(edges)

    def graph_neighbors(
        self,
        path: str,
        depth: int = 1,
        link_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.store.graph_neighbors(path, depth=depth, link_types=link_types)

    def materialize_index(self) -> str:
        pages = [
            page
            for page in self.list_pages()
            if page["path"] != "_meta/index"
            and page["page_type"] not in {"session", "archive"}
        ]
        body = "\n".join(f"- [{page['title']}]({page['path']})" for page in pages)
        self.write_page(
            "_meta/index",
            "Wiki Index",
            {"summary": f"Wiki index: {len(pages)} pages", "body": body or "No pages yet."},
            page_type="synthesis",
            created_by="maintenance",
            tags=["meta", "index"],
        )
        return f"Materialized index with {len(pages)} pages"

    def sync_index(self) -> None:
        warnings.warn(
            "sync_index is unnecessary for local PostgreSQL search",
            DeprecationWarning,
            stacklevel=2,
        )

    def index_row_count(self) -> int:
        return len(self.list_pages(include_ephemeral=True))

    def reconcile_vs_source(self) -> int:
        return 0

    def list_recent_by_cwd_tag(self, cwd_basename: str, limit: int = 3) -> list[dict[str, Any]]:
        if not cwd_basename:
            return []
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT page_path, title, updated_at FROM sessions "
                "WHERE workspace LIKE %s ORDER BY updated_at DESC LIMIT %s",
                (f"%/{cwd_basename}", limit),
            ).fetchall()
        return [
            {"path": row[0], "title": row[1], "summary": row[1], "updated_at": row[2]}
            for row in rows
        ]

    def _log(self, op_type, path=None, query=None, details=None) -> None:
        try:
            self.store.log(op_type, path=path, query=query, details=details)
        except Exception:
            pass
