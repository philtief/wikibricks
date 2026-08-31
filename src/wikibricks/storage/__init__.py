"""Focused PostgreSQL repositories and their public facade."""

from wikibricks.storage.sessions import IngestResult
from wikibricks.storage.store import PostgresStore

__all__ = ["IngestResult", "PostgresStore"]
