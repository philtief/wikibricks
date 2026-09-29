from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from wikibricks.config import load_config


def _normalize(value: str) -> str:
    return value.strip().lower().replace(" ", "-").replace("_", "-")


def _normalized_name(value: str) -> str:
    return _normalize(Path(value).name)


def _unrouted_workspace(workspace: str | None, *, generic: set[str], home: Path) -> bool:
    if workspace is None or not workspace or Path(workspace) == home:
        return True
    return _normalized_name(workspace) in generic


def _timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def curation_backlog(
    sessions: Iterable[tuple[str, str | None, str | datetime]],
    pages: Iterable[tuple[str, str, str | datetime]],
    *,
    now: datetime,
    days: int = 7,
    limit: int = 5,
    targets: dict[str, str | None],
) -> list[dict[str, Any]]:
    """Return recent sessions that are newer than their covering pages."""
    now = _timestamp(now)
    cutoff = now - timedelta(days=days)
    page_updates = {
        path: _timestamp(updated_at) for path, _, updated_at in pages if not path.startswith("_meta/")
    }
    page_titles = {path: title for path, title, _ in pages}
    projects: dict[str, dict[str, Any]] = {}

    for session_id, workspace, updated_at in sessions:
        target = targets.get(session_id)
        if target is None:
            continue
        timestamp = _timestamp(updated_at)
        if timestamp < cutoff or timestamp > now:
            continue
        project = Path(target).name
        item = projects.setdefault(
            target,
            {
                "project": project,
                "living_page": target,
                "workspace": workspace,
                "workspaces": set(),
                "session_timestamps": [],
            },
        )
        if workspace is not None and workspace:
            item["workspaces"].add(workspace)
            if (
                item["workspace"] is None
                or not item["workspace"]
                or workspace < item["workspace"]
            ):
                item["workspace"] = workspace
        item["session_timestamps"].append(timestamp)

    result: list[dict[str, Any]] = []
    for target, item in projects.items():
        project = item["project"]
        living_page = item["living_page"]
        last_page_update = page_updates.get(living_page)
        project_pages: list[str] = []
        if last_page_update is not None:
            project_pages.append(living_page)
        project_pages.extend(
            path
            for path in sorted(page_updates)
            if path != living_page
            and path.startswith("topics/")
            and (
                project in _normalize(path)
                or project in _normalize(page_titles.get(path, ""))
            )
        )
        last_session_at = max(item["session_timestamps"])
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
                "workspaces": sorted(item["workspaces"]),
                "workspace": item["workspace"],
                "new_sessions": new_sessions,
                "pages": project_pages[:3],
                "last_page_update": (
                    last_page_update.isoformat() if last_page_update else None
                ),
                "last_session_at": last_session_at.isoformat(),
            }
        )

    return sorted(
        result, key=lambda item: (-item["new_sessions"], item["project"])
    )[:limit]


def session_targets(
    conn: Any,
    *,
    generic: Iterable[str] | None = None,
    home: Path | None = None,
) -> dict[str, str | None]:
    """Route every session to the durable page that covers its work."""
    home = home or Path.home()
    normalized_generic = {
        _normalize(value) for value in (
            load_config().curation_generic_workspaces
            if generic is None
            else generic
        )
    }
    session_rows = conn.execute(
        "SELECT s.session_id, s.workspace, t.session_id, t.page_path FROM sessions s "
        "LEFT JOIN session_topics t ON t.session_id = s.session_id"
    ).fetchall()
    page_rows = conn.execute(
        "SELECT p.path, v.title FROM pages p "
        "JOIN page_versions v ON v.version_id = p.current_version_id "
        "WHERE p.status = 'active' AND p.path LIKE 'topics/%' ORDER BY p.path"
    ).fetchall()
    pages = [(row[0], row[1]) for row in page_rows]
    targets: dict[str, str | None] = {}
    for session_id, workspace, topic_session_id, routed_page in session_rows:
        if topic_session_id is not None:
            targets[session_id] = routed_page
            continue
        if _unrouted_workspace(workspace, generic=normalized_generic, home=home):
            targets[session_id] = None
            continue
        key = _normalized_name(workspace)
        target = next(
            (
                path
                for path, title in pages
                if key in _normalize(path) or key in _normalize(title)
            ),
            f"topics/{key}",
        )
        targets[session_id] = target
    return targets


def unrouted_sessions(
    conn: Any,
    *,
    since: str | datetime,
    generic: Iterable[str] | None = None,
    home: Path | None = None,
) -> list[str]:
    """Return container-folder sessions still waiting for topic routing."""
    home = home or Path.home()
    normalized_generic = {
        _normalize(value) for value in (
            load_config().curation_generic_workspaces
            if generic is None
            else generic
        )
    }
    cutoff = _timestamp(since)
    rows = conn.execute(
        "SELECT s.session_id, s.workspace, "
        "COALESCE(s.source_updated_at, s.updated_at) FROM sessions s "
        "LEFT JOIN session_topics t ON t.session_id = s.session_id "
        "WHERE t.session_id IS NULL ORDER BY s.session_id"
    ).fetchall()
    return [
        session_id
        for session_id, workspace, updated_at in rows
        if _timestamp(updated_at) >= cutoff
        and _unrouted_workspace(workspace, generic=normalized_generic, home=home)
    ]


def load_curation_backlog(conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Load sessions and active pages, then calculate the curation backlog."""
    # Count sessions by when the work happened, not when a backfill imported them.
    sessions = conn.execute(
        "SELECT s.session_id, s.workspace, COALESCE(s.source_updated_at, s.updated_at), "
        "t.created_at "
        "FROM sessions s LEFT JOIN session_topics t ON t.session_id = s.session_id"
    ).fetchall()
    sessions = [
        (
            row[0],
            row[1],
            _timestamp(row[2]) if row[3] is None else max(_timestamp(row[2]), _timestamp(row[3])),
        )
        for row in sessions
    ]
    pages = conn.execute(
        "SELECT p.path, v.title, p.updated_at FROM pages p "
        "JOIN page_versions v ON v.version_id = p.current_version_id "
        "WHERE p.status = 'active'"
    ).fetchall()
    home = kwargs.pop("home", None)
    generic = kwargs.pop("generic", None)
    if generic is None:
        generic = load_config().curation_generic_workspaces
    targets = session_targets(conn, generic=generic, home=home)
    kwargs.setdefault("now", datetime.now(timezone.utc))
    kwargs["targets"] = targets
    return curation_backlog(sessions, pages, **kwargs)
