"""Compatibility exports for PostgreSQL storage."""

from wikibricks.models import IngestResult
from wikibricks.storage.content import (
    MAX_SEARCH_CHUNK_BYTES,
    iter_search_chunks,
    page_content_hash,
)
from wikibricks.storage.store import PostgresStore

__all__ = [
    "IngestResult",
    "MAX_SEARCH_CHUNK_BYTES",
    "PostgresStore",
    "iter_search_chunks",
    "page_content_hash",
]
