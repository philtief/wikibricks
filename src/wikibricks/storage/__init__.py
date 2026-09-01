"""Local storage facades."""

from wikibricks.models import IngestResult
from wikibricks.storage.sqlite_store import SQLiteStore

__all__ = ["IngestResult", "SQLiteStore"]
