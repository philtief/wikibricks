"""Build bounded model requests from local curation evidence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wikibricks.storage.sqlite_store import SQLiteStore


def _normalize(value: str) -> str:
    return Path(value).name.strip().lower().replace(" ", "-").replace("_", "-")


def _database_path(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = str(row[2]) if row else ""
    return Path(path) if path else None


def _active_pages(
    conn: sqlite3.Connection,
    *,
    paths: set[str],
    project: str,
    related: int,
) -> list[dict[str, Any]]:
    candidates: list[str] = []
    if paths:
        placeholders = ",".join("?" for _ in paths)
        rows = conn.execute(
            "SELECT p.path FROM pages p WHERE p.status = 'active' "
            f"AND p.path IN ({placeholders})",
            tuple(paths),
        ).fetchall()
        candidates.extend(row[0] for row in rows)

    if related > 0 and len(candidates) < related + len(paths):
        database_path = _database_path(conn)
        if database_path is not None:
            hits = SQLiteStore(database_path).search(
                project.replace("-", " "),
                num_results=related + len(candidates) + len(paths),
            )
            related_limit = len(candidates) + related
            for hit in hits:
                path = hit["path"]
                if (
                    len(candidates) >= related_limit
                    or path.startswith("_meta/")
                    or path in candidates
                ):
                    continue
                candidates.append(path)

    if not candidates:
        return []
    placeholders = ",".join("?" for _ in candidates)
    rows = conn.execute(
        "SELECT v.version_id, p.path, v.title, v.page_type, v.content, v.tags, "
        "v.source_ids, v.content_hash FROM pages p "
        "JOIN page_versions v ON v.version_id = p.current_version_id "
        "WHERE p.status = 'active' "
        f"AND p.path IN ({placeholders}) ORDER BY p.path",
        candidates,
    ).fetchall()
    return [
        {
            "evidence_id": f"page-version:{row['version_id']}",
            "path": row["path"],
            "title": row["title"],
            "page_type": row["page_type"],
            "content": json.loads(row["content"]),
            "tags": json.loads(row["tags"]),
            "source_ids": json.loads(row["source_ids"]) if row["source_ids"] else [],
            "base_version_id": row["version_id"],
            "base_content_hash": row["content_hash"],
        }
        for row in rows
    ]


def _moment(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _evidence(
    conn: sqlite3.Connection,
    *,
    project: str,
    cursor: str | None,
    last_page_update: str | None,
    max_events: int,
    max_chars: int,
    max_event_chars: int,
) -> tuple[list[dict[str, Any]], str]:
    # Compare parsed datetimes: stored ISO strings carry different UTC offsets.
    after = [_moment(value) for value in (cursor, last_page_update) if value]
    sessions = [
        (_moment(row[2]), row[0])
        for row in conn.execute(
            "SELECT session_id, workspace, COALESCE(source_updated_at, updated_at) "
            "FROM sessions WHERE workspace IS NOT NULL"
        ).fetchall()
        if _normalize(row[1]) == project and all(_moment(row[2]) > bound for bound in after)
    ]
    if not sessions:
        return [], ""
    sessions.sort()
    order = {session_id: index for index, (_, session_id) in enumerate(sessions)}
    moments = {session_id: moment for moment, session_id in sessions}
    placeholders = ",".join("?" for _ in sessions)
    rows = conn.execute(
        "SELECT s.session_id, s.page_path, e.position, "
        "v.version_id, v.kind, v.content, v.source_created_at "
        "FROM sessions s JOIN session_events e ON e.session_id = s.session_id "
        "JOIN session_event_versions v ON v.version_id = e.current_version_id "
        "WHERE e.active = 1 AND v.kind IN ('user', 'assistant') "
        f"AND s.session_id IN ({placeholders})",
        list(order),
    ).fetchall()
    candidates = sorted(rows, key=lambda row: (order[row["session_id"]], row["position"]))

    selected: list[sqlite3.Row] = []
    total_chars = 0
    for row in candidates:
        length = min(len(row["content"]), max_event_chars)
        if len(selected) == max_events:
            total_chars -= min(len(selected[0]["content"]), max_event_chars)
            selected.pop(0)
        while selected and total_chars + length > max_chars:
            total_chars -= min(len(selected[0]["content"]), max_event_chars)
            selected.pop(0)
        if total_chars + length > max_chars:
            continue
        total_chars += length
        selected.append(row)
    evidence = [
        {
            "evidence_id": f"session-event:{row['version_id']}",
            "session": row["page_path"],
            "kind": row["kind"],
            "created_at": row["source_created_at"],
            "text": row["content"][:max_event_chars],
        }
        for row in selected
    ]
    newest = max(moments[row["session_id"]] for row in selected)
    return evidence, newest.isoformat()


def build_request(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    *,
    cursor: str | None = None,
    max_events: int = 40,
    max_chars: int = 30000,
    max_event_chars: int = 2000,
    related: int = 5,
) -> dict[str, Any] | None:
    """Build one bounded curator request for a backlog item."""
    project = item["project"]
    requested_paths = list(dict.fromkeys(item["pages"]))
    living_page = next(
        (path for path in requested_paths if path.startswith("topics/")),
        f"topics/{project}",
    )
    paths = set(requested_paths) | {living_page}
    pages = _active_pages(
        conn,
        paths=paths,
        project=project,
        related=related,
    )
    evidence, newest_evidence_at = _evidence(
        conn,
        project=project,
        cursor=cursor,
        last_page_update=item.get("last_page_update"),
        max_events=max_events,
        max_chars=max_chars,
        max_event_chars=max_event_chars,
    )
    if not evidence:
        return None
    evidence_ids = {entry["evidence_id"] for entry in evidence}
    page_ids = {page["evidence_id"] for page in pages}
    return {
        "request": {
            "project": project,
            "living_page": living_page,
            "current_pages": pages,
            "evidence": evidence,
            "similarity_candidates": [],
        },
        "pages": pages,
        "evidence_ids": evidence_ids | page_ids,
        "newest_evidence_at": newest_evidence_at,
    }
