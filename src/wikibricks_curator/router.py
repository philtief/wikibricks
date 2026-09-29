"""Route container-folder sessions to topic pages with one model call."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from importlib.resources import files
from typing import Any

from wikibricks.config import load_config
from wikibricks.curation.backlog import unrouted_sessions
from wikibricks.storage.sqlite_store import SQLiteStore

Chat = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
_NEW_TOPIC = re.compile(r"^topics/[a-z0-9]+(-[a-z0-9]+)*$")
_ROUTER_PROMPT = files("wikibricks_curator").joinpath("resources", "router.md")
_ROUTER_SCHEMA = files("wikibricks_curator").joinpath("resources", "router.schema.json")


def _normalize(value: str) -> str:
    return value.strip().lower().replace(" ", "-").replace("_", "-")


def _sessions(conn: Any, ids: list[str], max_chars: int) -> list[dict[str, str]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT s.session_id, s.workspace, s.title FROM sessions s "
        f"WHERE s.session_id IN ({placeholders})",
        ids,
    ).fetchall()
    event_rows = conn.execute(
        "SELECT e.session_id, v.content FROM session_events e "
        "JOIN session_event_versions v ON v.version_id = e.current_version_id "
        "WHERE e.active = 1 AND v.kind = 'user' "
        f"AND e.session_id IN ({placeholders}) ORDER BY e.session_id, e.position",
        ids,
    ).fetchall()
    texts: dict[str, list[str]] = {}
    for session_id, content in event_rows:
        texts.setdefault(session_id, []).append(content)
    return [
        {
            "session_id": row["session_id"],
            "title": row["title"],
            "workspace": row["workspace"],
            "text": "\n".join(texts.get(row["session_id"], []))[:max_chars],
        }
        for row in sorted(rows, key=lambda row: ids.index(row["session_id"]))
    ]


def _candidates(conn: Any, generic: set[str]) -> list[dict[str, str]]:
    # Pages named after a container folder (e.g. an old `topics/emails`) are not topics.
    rows = conn.execute(
        "SELECT p.path, v.title, v.content FROM pages p "
        "JOIN page_versions v ON v.version_id = p.current_version_id "
        "WHERE p.status = 'active' "
        "AND (p.path LIKE 'topics/%' OR p.path LIKE 'projects/%') ORDER BY p.path"
    ).fetchall()
    return [
        {
            "path": row["path"],
            "title": row["title"],
            "summary": str(json.loads(row["content"]).get("summary", ""))[:200],
        }
        for row in rows
        if _normalize(row["path"].rsplit("/", 1)[-1]) not in generic | {"home"}
    ]


def _new_path(path: str, *, candidates: set[str], generic: set[str]) -> bool:
    if not isinstance(path, str) or path in candidates:
        return False
    last_segment = path.rsplit("/", 1)[-1]
    return _NEW_TOPIC.fullmatch(path) is not None and _normalize(last_segment) not in generic | {"home"}


def _valid_path(path: Any, *, candidates: set[str], generic: set[str]) -> bool:
    if not isinstance(path, str):
        return False
    return path in candidates or _new_path(path, candidates=candidates, generic=generic)


def _schema() -> dict[str, Any]:
    return json.loads(_ROUTER_SCHEMA.read_text(encoding="utf-8"))


def route_sessions(
    store: SQLiteStore,
    chat: Chat,
    *,
    since: str | datetime,
    limit: int = 20,
    max_chars: int = 600,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ask the model for topic routes and store the accepted answers."""
    generic = {_normalize(value) for value in load_config().curation_generic_workspaces}
    with store.connection() as conn:
        ids = unrouted_sessions(conn, since=since)[:limit]
        if not ids:
            return {
                "sessions": 0,
                "routed": 0,
                "to_none": 0,
                "new_topics": [],
                "invalid": 0,
                "error": None,
            }
        sessions = _sessions(conn, ids, max_chars)
        candidates = _candidates(conn, generic)
    candidate_paths = {candidate["path"] for candidate in candidates}
    prompt = _ROUTER_PROMPT.read_text(encoding="utf-8")
    schema = _schema()
    empty = {session["session_id"] for session in sessions if not session["text"]}
    model_ids = [session_id for session_id in ids if session_id not in empty]
    model_id_set = set(model_ids)
    request = {
        "sessions": [session for session in sessions if session["session_id"] in model_id_set],
        "candidates": candidates,
    }
    raw: Any = None
    error: str | None = None
    for _attempt in (1, 2):
        try:
            raw = chat(prompt, request, schema) if model_ids else {"routes": []}
            error = None
            break
        except Exception as exc:
            error = str(exc)[:300]
    if error is not None:
        return {
            "sessions": len(ids),
            "routed": 0,
            "to_none": 0,
            "new_topics": [],
            "invalid": 0,
            "error": error,
        }

    routes = raw.get("routes") if isinstance(raw, dict) else None
    invalid_routes = not isinstance(routes, list)
    routes = routes if isinstance(routes, list) else []
    accepted: dict[str, str | None] = {}
    invalid = 0
    for route in routes:
        session_id = route.get("session_id") if isinstance(route, dict) else None
        page_path = route.get("page_path") if isinstance(route, dict) else None
        if (
            not isinstance(session_id, str)
            or session_id not in model_id_set
            or session_id in accepted
            or not (
                page_path is None
                or _valid_path(page_path, candidates=candidate_paths, generic=generic)
            )
        ):
            invalid += 1
            continue
        accepted[session_id] = page_path
    for session_id in empty:
        accepted[session_id] = None
    if invalid_routes:
        invalid += len(model_ids)
    new_topics = sorted(
        {path for path in accepted.values() if path is not None and path not in candidate_paths}
    )
    if not dry_run:
        timestamp = datetime.now(timezone.utc).isoformat()
        with store.connection(write=True) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO session_topics"
                "(session_id, page_path, origin, created_at) VALUES (?, ?, 'local-curator-router', ?)",
                [
                    (session_id, page_path, timestamp)
                    for session_id, page_path in accepted.items()
                ],
            )
    return {
        "sessions": len(ids),
        "routed": len(accepted),
        "to_none": sum(path is None for path in accepted.values()),
        "new_topics": new_topics,
        "invalid": invalid,
        "error": None,
    }
