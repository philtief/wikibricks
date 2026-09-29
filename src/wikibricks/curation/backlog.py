from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _normalize(value: str) -> str:
    return value.strip().lower().replace(" ", "-").replace("_", "-")


def _timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def curation_backlog(
    sessions: Iterable[tuple[str | None, str | datetime]],
    pages: Iterable[tuple[str, str, str | datetime]],
    *,
    now: datetime,
    days: int = 7,
    limit: int = 5,
    home: Path | None = None,
) -> list[dict[str, Any]]:
    """Return recent sessions that are newer than their covering pages."""
    now = _timestamp(now)
    home = home or Path.home()
    cutoff = now - timedelta(days=days)
    projects: dict[str, dict[str, Any]] = {}

    for workspace, updated_at in sessions:
        if workspace is None or not workspace or Path(workspace) == home:
            continue
        timestamp = _timestamp(updated_at)
        if timestamp < cutoff or timestamp > now:
            continue
        project = _normalize(Path(workspace).name)
        item = projects.setdefault(
            project,
            {
                "project": project,
                "workspace": workspace,
                "session_timestamps": [],
            },
        )
        item["session_timestamps"].append(timestamp)

    covering: dict[str, list[tuple[str, datetime]]] = {}
    for path, title, updated_at in pages:
        if path.startswith("_meta/"):
            continue
        normalized_path = _normalize(path)
        normalized_title = _normalize(title)
        timestamp = _timestamp(updated_at)
        for project, item in projects.items():
            if project in normalized_path or project in normalized_title:
                covering.setdefault(project, []).append((path, timestamp))

    result: list[dict[str, Any]] = []
    for project, item in projects.items():
        project_pages = sorted(covering.get(project, []))
        last_session_at = max(item["session_timestamps"])
        if project_pages:
            last_page_update = max(timestamp for _, timestamp in project_pages)
        else:
            last_page_update = None
        new_sessions = sum(
            last_page_update is None or timestamp > last_page_update
            for timestamp in item["session_timestamps"]
        )
        if new_sessions == 0:
            continue
        del item["session_timestamps"]
        result.append(
            {
                **item,
                "new_sessions": new_sessions,
                "pages": [path for path, _ in project_pages[:3]],
                "last_page_update": (
                    last_page_update.isoformat() if last_page_update else None
                ),
                "last_session_at": last_session_at.isoformat(),
            }
        )

    return sorted(
        result, key=lambda item: (-item["new_sessions"], item["project"])
    )[:limit]


def load_curation_backlog(conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Load sessions and active pages, then calculate the curation backlog."""
    # Count sessions by when the work happened, not when a backfill imported them.
    sessions = conn.execute(
        "SELECT workspace, COALESCE(source_updated_at, updated_at) FROM sessions"
    ).fetchall()
    pages = conn.execute(
        "SELECT p.path, v.title, p.updated_at FROM pages p "
        "JOIN page_versions v ON v.version_id = p.current_version_id "
        "WHERE p.status = 'active'"
    ).fetchall()
    kwargs.setdefault("now", datetime.now(timezone.utc))
    return curation_backlog(sessions, pages, **kwargs)
