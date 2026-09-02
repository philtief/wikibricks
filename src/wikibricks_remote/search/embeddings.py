"""Bounded embedding generation with per-content-hash reuse."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from wikibricks_remote.search.documents import SearchDocument

Embedder = Callable[[list[str]], Sequence[Sequence[float]]]


@dataclass(frozen=True, slots=True)
class EmbeddingUpdate:
    document_id: UUID
    content_hash: str
    model: str
    embedding: tuple[float, ...]


def build_embedding_updates(
    documents: Sequence[SearchDocument],
    embedder: Embedder,
    *,
    model: str,
    dimension: int,
    batch_size: int,
) -> tuple[EmbeddingUpdate, ...]:
    if not model.strip() or dimension < 1 or batch_size < 1:
        raise ValueError("embedding model and bounds must be positive")
    texts: dict[str, str] = {}
    for document in documents:
        previous = texts.setdefault(document.content_hash, document.content_text)
        if previous != document.content_text:
            raise ValueError("search content hash collision")
    vectors: dict[str, tuple[float, ...]] = {}
    hashes = list(texts)
    for offset in range(0, len(hashes), batch_size):
        batch_hashes = hashes[offset : offset + batch_size]
        response = embedder([texts[value] for value in batch_hashes])
        if len(response) != len(batch_hashes):
            raise ValueError("embedding response count does not match its request")
        for content_hash, raw_vector in zip(batch_hashes, response):
            if len(raw_vector) != dimension:
                raise ValueError(
                    f"embedding dimension must be {dimension}, got {len(raw_vector)}"
                )
            try:
                vector = tuple(float(value) for value in raw_vector)
            except (TypeError, ValueError) as error:
                raise ValueError("embedding contains a non-numeric value") from error
            vectors[content_hash] = vector
    return tuple(
        EmbeddingUpdate(
            document_id=document.document_id,
            content_hash=document.content_hash,
            model=model,
            embedding=vectors[document.content_hash],
        )
        for document in documents
    )


__all__ = ["Embedder", "EmbeddingUpdate", "build_embedding_updates"]
