"""Local PostgreSQL initialization, validation, backup, and maintenance."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks.postgres_store import PostgresStore

_FINGERPRINT_TABLES = (
    "pages",
    "page_versions",
    "links",
    "sources",
    "operations",
    "sessions",
    "session_events",
    "session_event_versions",
    "sync_outbox",
    "sync_state",
    "archive_pages",
)


def _database_params(database_url: str) -> tuple[dict[str, str], str]:
    params = conninfo_to_dict(database_url)
    database = params.get("dbname") or "wikibricks"
    return params, database


def initialize_database(database_url: str, *, migrate: bool = True) -> bool:
    """Create the target database when absent, then apply migrations."""
    params, database = _database_params(database_url)
    admin_params = dict(params)
    admin_params["dbname"] = "postgres"
    with psycopg.connect(make_conninfo(**admin_params), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    if migrate:
        PostgresStore(database_url).migrate()
    return not bool(exists)


def check_database(database_url: str) -> dict[str, Any]:
    store = PostgresStore(database_url)
    store.migrate()
    with store.connection() as conn:
        pg_trgm = bool(
            conn.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
            ).fetchone()
        )
        broken_pages = int(
            conn.execute(
                "SELECT count(*) FROM pages p LEFT JOIN page_versions v "
                "ON v.version_id = p.current_version_id "
                "WHERE p.current_version_id IS NULL OR v.version_id IS NULL"
            ).fetchone()[0]
        )
        broken_sessions = int(
            conn.execute(
                "SELECT count(*) FROM session_events e LEFT JOIN session_event_versions v "
                "ON v.version_id = e.current_version_id "
                "WHERE e.active AND (e.current_version_id IS NULL OR v.version_id IS NULL)"
            ).fetchone()[0]
        )
        pending_outbox = int(
            conn.execute(
                "SELECT count(*) FROM sync_outbox WHERE acknowledged_at IS NULL"
            ).fetchone()[0]
        )
    return {
        "ok": pg_trgm and broken_pages == 0 and broken_sessions == 0,
        "pg_trgm": pg_trgm,
        "broken_page_pointers": broken_pages,
        "broken_session_pointers": broken_sessions,
        "pending_outbox": pending_outbox,
    }


def backup_database(database_url: str, output: Path) -> Path:
    executable = shutil.which("pg_dump")
    if executable is None:
        raise RuntimeError("pg_dump is required for WikiBricks backups")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            executable,
            "--dbname",
            database_url,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output


def restore_database(backup: Path, database_url: str) -> None:
    executable = shutil.which("pg_restore")
    if executable is None:
        raise RuntimeError("pg_restore is required for WikiBricks restores")
    created = initialize_database(database_url, migrate=False)
    if not created:
        raise RuntimeError("restore target database already exists")
    subprocess.run(
        [
            executable,
            "--dbname",
            database_url,
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            str(backup),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def database_fingerprint(database_url: str) -> dict[str, dict[str, Any]]:
    store = PostgresStore(database_url)
    result: dict[str, dict[str, Any]] = {}
    with store.connection() as conn:
        for table in _FINGERPRINT_TABLES:
            count, digest = conn.execute(
                sql.SQL(
                    "SELECT count(*), md5(COALESCE(string_agg(to_jsonb(t)::text, '' "
                    "ORDER BY to_jsonb(t)::text), '')) FROM {} t"
                ).format(sql.Identifier(table))
            ).fetchone()
            result[table] = {"count": int(count), "sha": digest}
    return result


def vacuum_database(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("VACUUM (ANALYZE)")
