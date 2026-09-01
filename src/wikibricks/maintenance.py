"""Local PostgreSQL initialization, validation, backup, and maintenance."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikibricks.client import WikiClient
from wikibricks.postgres_store import PostgresStore
from wikibricks.storage.content import insert_search_chunks

_FINGERPRINT_TABLES = (
    "pages",
    "page_versions",
    "page_aliases",
    "links",
    "sources",
    "operations",
    "sessions",
    "session_events",
    "session_event_versions",
    "sync_outbox",
    "sync_state",
    "sync_replicas",
    "curation_runs",
    "curation_patches",
    "curation_receipts",
    "curation_conflicts",
    "remote_maintenance_runs",
    "archive_pages",
    "archive_batches",
    "archive_events",
    "archive_batch_events",
    "curated_pages",
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


def curate_database(
    database_url: str,
    *,
    prune_archived_sessions_after_days: int | None = None,
) -> dict[str, Any]:
    """Repair local indexes, report wiki hygiene, and apply explicit retention."""
    if (
        prune_archived_sessions_after_days is not None
        and prune_archived_sessions_after_days < 1
    ):
        raise ValueError("session retention must be at least one day")

    store = PostgresStore(database_url)
    store.migrate()
    repaired_pages = repaired_sessions = 0
    with store.connection() as conn, conn.transaction():
        page_rows = conn.execute(
            "SELECT v.version_id, v.content_text FROM pages p "
            "JOIN page_versions v ON v.version_id = p.current_version_id "
            "WHERE p.status = 'active' AND NOT EXISTS (SELECT 1 FROM page_search_chunks c "
            "WHERE c.version_id = v.version_id)"
        ).fetchall()
        for version_id, content_text in page_rows:
            insert_search_chunks(
                conn, "page_search_chunks", version_id, content_text
            )
            repaired_pages += 1

        session_rows = conn.execute(
            "SELECT v.version_id, v.content FROM sessions s "
            "JOIN session_events e ON e.session_id = s.session_id AND e.active "
            "JOIN session_event_versions v ON v.version_id = e.current_version_id "
            "WHERE NOT EXISTS (SELECT 1 FROM session_search_chunks c "
            "WHERE c.version_id = v.version_id)"
        ).fetchall()
        for version_id, content in session_rows:
            insert_search_chunks(
                conn, "session_search_chunks", version_id, content
            )
            repaired_sessions += 1

    index_result = WikiClient(database_url, migrate=False).materialize_index()

    with store.connection() as conn:
        duplicate_rows = conn.execute(
            "SELECT array_agg(p.path ORDER BY p.path), count(*) "
            "FROM pages p JOIN page_versions v ON v.version_id = p.current_version_id "
            "WHERE p.status = 'active' AND p.path <> '_meta/index' GROUP BY v.content_hash "
            "HAVING count(*) > 1 ORDER BY min(p.path)"
        ).fetchall()
        orphan_rows = conn.execute(
            "SELECT p.path FROM pages p WHERE p.status = 'active' "
            "AND p.path NOT LIKE '_meta/%' "
            "AND NOT EXISTS (SELECT 1 FROM links l WHERE l.source_page_id = p.page_id "
            "OR l.target_page_id = p.page_id) ORDER BY p.path"
        ).fetchall()

    pruned_sessions = 0
    if prune_archived_sessions_after_days is not None:
        with store.connection() as conn, conn.transaction():
            candidates = conn.execute(
                "SELECT s.session_id FROM sessions s "
                "WHERE s.updated_at < now() - (%s * interval '1 day') "
                "AND EXISTS (SELECT 1 FROM session_events e "
                "WHERE e.session_id = s.session_id) "
                "AND NOT EXISTS ("
                "SELECT 1 FROM session_event_versions v "
                "JOIN session_events e ON e.event_id = v.event_id "
                "WHERE e.session_id = s.session_id AND NOT EXISTS ("
                "SELECT 1 FROM sync_outbox o "
                "WHERE o.entity_kind = 'session_event_version' "
                "AND o.version_id = v.version_id "
                "AND o.acknowledged_at IS NOT NULL)) "
                "ORDER BY s.session_id FOR UPDATE",
                (prune_archived_sessions_after_days,),
            ).fetchall()
            session_ids = [row[0] for row in candidates]
            if session_ids:
                conn.execute(
                    "DELETE FROM sync_outbox o USING session_event_versions v, "
                    "session_events e WHERE o.version_id = v.version_id "
                    "AND v.event_id = e.event_id AND e.session_id = ANY(%s) "
                    "AND o.acknowledged_at IS NOT NULL",
                    (session_ids,),
                )
                deleted = conn.execute(
                    "DELETE FROM sessions WHERE session_id = ANY(%s)",
                    (session_ids,),
                )
                pruned_sessions = deleted.rowcount

    result = {
        "ok": True,
        "index": index_result,
        "repaired_page_search_versions": repaired_pages,
        "repaired_session_search_versions": repaired_sessions,
        "duplicate_page_groups": [
            {"paths": list(paths), "count": int(count)}
            for paths, count in duplicate_rows
        ],
        "orphan_pages": [row[0] for row in orphan_rows],
        "pruned_sessions": pruned_sessions,
        "pending_outbox": store.outbox_count(),
    }
    store.log("curate_local", details=result)
    return result


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
