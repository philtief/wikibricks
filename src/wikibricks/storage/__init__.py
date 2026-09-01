"""Local storage facades."""

from wikibricks.storage.sessions import IngestResult
from wikibricks.storage.sqlite_store import SQLiteStore
from wikibricks.storage.store import PostgresStore

__all__ = ["IngestResult", "PostgresStore", "SQLiteStore"]
