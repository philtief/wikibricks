"""Compatibility exports for PostgreSQL storage."""

from wikibricks.storage import IngestResult, PostgresStore
from wikibricks.storage.content import (
    MAX_SEARCH_CHUNK_BYTES,
    iter_search_chunks,
    page_content_hash,
)

__all__ = [
    "IngestResult",
    "MAX_SEARCH_CHUNK_BYTES",
    "PostgresStore",
    "iter_search_chunks",
    "page_content_hash",
]
