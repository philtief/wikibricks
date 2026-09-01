"""One-time migration from the former PostgreSQL local store to SQLite."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from wikibricks.storage.sqlite_store import SQLiteStore

_TABLES = (
    "pages",
    "page_versions",
    "sessions",
    "session_events",
    "session_event_versions",
    "links",
    "sources",
    "operations",
    "sync_outbox",
    "sync_state",
    "sync_replicas",
    "curation_runs",
    "curation_patches",
    "page_aliases",
    "curation_receipts",
    "curation_conflicts",
    "archive_pages",
)


@dataclass(frozen=True, slots=True)
class MigrationReport:
    counts: dict[str, int]
    verified: bool


def _sqlite_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    raise TypeError(f"unsupported PostgreSQL migration value: {type(value).__name__}")


def _postgres_columns(conn: Any, table: str) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        ).fetchall()
    ]


def _sqlite_columns(conn: Any, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def migrate_postgres(source_url: str, destination: Path) -> MigrationReport:
    """Copy one PostgreSQL WikiBricks database into a new SQLite database."""
    from psycopg import sql

    from wikibricks.postgres_store import PostgresStore

    destination = Path(destination).expanduser()
    if destination.exists():
        raise FileExistsError(f"migration destination already exists: {destination}")

    source = PostgresStore(source_url)
    source.migrate()
    target = SQLiteStore(destination)
    target.migrate()
    counts: dict[str, int] = {}
    with source.connection() as source_conn, target.connection(write=True) as target_conn:
        for table in _TABLES:
            source_columns = _postgres_columns(source_conn, table)
            target_columns = _sqlite_columns(target_conn, table)
            columns = [column for column in source_columns if column in target_columns]
            if not columns:
                continue
            rows = source_conn.execute(
                sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    sql.Identifier(table),
                )
            ).fetchall()
            if rows:
                quoted = ", ".join(f'"{column}"' for column in columns)
                placeholders = ", ".join("?" for _ in columns)
                target_conn.executemany(
                    f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
                    [tuple(_sqlite_value(value) for value in row) for row in rows],
                )
            counts[table] = len(rows)

    target.repair_search_indexes()
    with source.connection() as source_conn, target.connection() as target_conn:
        verified = all(
            int(
                source_conn.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                ).fetchone()[0]
            )
            == int(target_conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in counts
        )
    if not verified:
        raise RuntimeError("PostgreSQL migration row-count verification failed")
    return MigrationReport(counts=counts, verified=True)


__all__ = ["MigrationReport", "migrate_postgres"]
