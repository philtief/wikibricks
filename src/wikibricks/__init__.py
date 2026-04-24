"""WikiBricks: Delta + Vector Search wiki store for AI agents on Databricks."""

from wikibricks.agent_tools import make_agent_tools
from wikibricks.client import WikiClient
from wikibricks.ops import (
    CATALOG,
    EMBEDDING_MODEL,
    HISTORY_TABLE,
    LINKS_TABLE,
    LOG_TABLE,
    PAGES_TABLE,
    PROMOTE_CHECKPOINT_TABLE,
    SCHEMA,
    SOURCES_TABLE,
    SOURCES_VOLUME,
    VS_ENDPOINT,
    VS_INDEX,
)

__all__ = [
    "WikiClient",
    "make_agent_tools",
    "CATALOG",
    "SCHEMA",
    "PAGES_TABLE",
    "HISTORY_TABLE",
    "LINKS_TABLE",
    "SOURCES_TABLE",
    "LOG_TABLE",
    "PROMOTE_CHECKPOINT_TABLE",
    "SOURCES_VOLUME",
    "VS_INDEX",
    "VS_ENDPOINT",
    "EMBEDDING_MODEL",
]
