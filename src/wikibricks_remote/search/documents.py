"""Pure projection of immutable archive events into bounded search documents."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5


@dataclass(frozen=True, slots=True)
class SearchDocument:
    document_id: UUID
    replica_id: UUID
    archive_event_id: UUID
    local_sequence: int
    entity_kind: str
    entity_id: UUID
    version_id: UUID
    page_path: str | None
    title: str | None
    document_kind: str
    chunk_index: int
    content_text: str
    content_hash: str


def _chunks(text: str, maximum: int) -> tuple[str, ...]:
    if maximum < 1:
        raise ValueError("search document chunk size must be positive")
    paragraphs = text.strip().split("\n\n")
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        combined = f"{current}\n\n{paragraph}" if current else paragraph
        if len(combined) <= maximum:
            current = combined
            continue
        if current:
            available = maximum - len(current) - 2
            if available > 0:
                current = f"{current}\n\n{paragraph[:available]}"
                paragraph = paragraph[available:]
            chunks.append(current)
            current = ""
        while len(paragraph) > maximum:
            chunks.append(paragraph[:maximum])
            paragraph = paragraph[maximum:]
        current = paragraph
    if current:
        chunks.append(current)
    return tuple(chunks)


def _page_text(payload: dict[str, Any]) -> str:
    title = str(payload.get("title") or "").strip()
    text = str(payload.get("content_text") or "").strip()
    if not text:
        content = payload.get("content")
        if isinstance(content, dict):
            text = "\n\n".join(
                str(content.get(field) or "").strip()
                for field in ("summary", "body")
                if str(content.get(field) or "").strip()
            )
    return f"{title}\n\n{text}".strip()


def project_event(
    event: dict[str, Any],
    *,
    max_chars: int = 12000,
) -> tuple[SearchDocument, ...]:
    """Project searchable text without changing or deleting archived evidence."""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("archive event payload must be an object")
    entity_kind = str(event["entity_kind"])
    if entity_kind == "page_version":
        text = _page_text(payload)
        page_path = str(payload["path"])
        title = str(payload["title"])
        document_kind = "page"
    elif entity_kind == "session_event_version" and payload.get("kind") in {
        "user",
        "assistant",
    }:
        text = str(payload.get("content") or "").strip()
        page_path = str(payload["page_path"]) if payload.get("page_path") else None
        title = None
        document_kind = str(payload["kind"])
    else:
        return ()
    if not text:
        return ()
    replica_id = UUID(str(event["replica_id"]))
    event_id = UUID(str(event["event_id"]))
    entity_id = UUID(str(event["entity_id"]))
    version_id = UUID(str(event["version_id"]))
    sequence = int(event.get("local_sequence", event.get("sequence")))
    result = []
    for index, chunk in enumerate(_chunks(text, max_chars)):
        result.append(
            SearchDocument(
                document_id=uuid5(
                    NAMESPACE_URL,
                    f"https://wikibricks.dev/search/{replica_id}/{event_id}/{index}",
                ),
                replica_id=replica_id,
                archive_event_id=event_id,
                local_sequence=sequence,
                entity_kind=entity_kind,
                entity_id=entity_id,
                version_id=version_id,
                page_path=page_path,
                title=title,
                document_kind=document_kind,
                chunk_index=index,
                content_text=chunk,
                content_hash=hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(result)


__all__ = ["SearchDocument", "project_event"]
