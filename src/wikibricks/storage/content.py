"""Canonical hashes and bounded PostgreSQL search chunks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

if TYPE_CHECKING:
    from psycopg import Connection

MAX_SEARCH_CHUNK_BYTES = 64 * 1024


def canonical_hash(value: Any) -> str:
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
    return canonical_hash(
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


def insert_search_chunks(
    conn: Connection,
    table: Literal["page_search_chunks", "session_search_chunks"],
    version_id: UUID,
    text: str,
) -> None:
    if table not in {"page_search_chunks", "session_search_chunks"}:
        raise ValueError(f"unsupported search chunk table: {table}")
    statement = (
        f"INSERT INTO {table} "
        "(version_id, chunk_index, start_offset, end_offset, search_vector) "
        "VALUES (%s, %s, %s, %s, to_tsvector('simple', %s))"
    )
    for index, (start, end, chunk) in enumerate(iter_search_chunks(text)):
        conn.execute(statement, (version_id, index, start, end, chunk))
