from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wikibricks.config import load_config
from wikibricks.curation.backlog import curation_backlog, session_targets, unrouted_sessions
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.storage.sqlite_store import SQLiteStore
from wikibricks_curator.evidence import build_request

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
HOME = Path("/Users/u")


def _timestamp(offset_hours: int) -> str:
    return (NOW - timedelta(hours=offset_hours)).isoformat()


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "wikibricks.db")
    store.migrate()
    return store


def _ingest(
    store: SQLiteStore,
    external_id: str,
    workspace: str,
    updated_at: str = _timestamp(1),
) -> str:
    store.ingest_session(
        SessionRecord(
            harness="test-harness",
            external_id=external_id,
            user_id="user",
            workspace=workspace,
            updated_at=updated_at,
            events=[SessionEvent("0", "user", f"{external_id} content")],
        )
    )
    with store.connection(write=True) as conn:
        return conn.execute(
            "SELECT session_id FROM sessions "
            "WHERE harness = 'test-harness' AND external_id = ?",
            (external_id,),
        ).fetchone()[0]


def test_session_targets_route_by_rows_and_pages(tmp_path: Path):
    store = _store(tmp_path)
    store.write_page(
        "topics/agent-atlas",
        "Agent Atlas (agent-compliance-cockpit)",
        {"summary": "atlas", "body": "page"},
    )
    routed = _ingest(store, "routed", "/Users/u/work/covered")
    routed_none = _ingest(store, "routed-none", "/Users/u/work/covered")
    generic = _ingest(store, "generic", str(HOME / "code"))
    home = _ingest(store, "home", str(HOME))
    covered = _ingest(store, "covered", str(HOME / "agent-compliance-cockpit"))
    uncovered = _ingest(store, "uncovered", str(HOME / "uncovered-project"))

    with store.connection(write=True) as conn:
        conn.executemany(
            "INSERT INTO session_topics(session_id, page_path, origin, created_at) "
            "VALUES (?, ?, 'manual', ?)",
            [
                (routed, "topics/agent-atlas", _timestamp(2)),
                (routed_none, None, _timestamp(2)),
            ],
        )
        targets = session_targets(conn, home=HOME)

    assert targets[routed] == "topics/agent-atlas"
    assert targets[routed_none] is None
    assert targets[generic] is None
    assert targets[home] is None
    assert targets[covered] == "topics/agent-atlas"
    assert targets[uncovered] == "topics/uncovered-project"


def test_session_targets_honours_a_generic_override(tmp_path: Path):
    store = _store(tmp_path)
    overridden = _ingest(store, "overridden", str(HOME / "custom"))
    project = _ingest(store, "project", str(HOME / "custom" / "project"))

    with store.connection(write=True) as conn:
        targets = session_targets(conn, generic=("custom",), home=HOME)

    assert targets[overridden] is None
    assert targets[project] == "topics/project"


def test_unrouted_sessions_returns_only_recent_container_folder_sessions(tmp_path: Path):
    store = _store(tmp_path)
    generic = _ingest(store, "generic", str(HOME / "code"), _timestamp(1))
    home = _ingest(store, "home", str(HOME), _timestamp(2))
    old = _ingest(store, "old", str(HOME / "code"), _timestamp(10))
    routed = _ingest(store, "routed", str(HOME / "code"), _timestamp(3))
    project = _ingest(store, "project", str(HOME / "project"), _timestamp(1))
    with store.connection(write=True) as conn:
        conn.execute(
            "INSERT INTO session_topics(session_id, page_path, origin, created_at) "
            "VALUES (?, 'topics/agent-atlas', 'manual', ?)",
            (routed, _timestamp(4)),
        )

    with store.connection() as conn:
        result = unrouted_sessions(conn, since=_timestamp(5), home=HOME)

    assert set(result) == {generic, home}
    assert old not in result
    assert routed not in result
    assert project not in result


def test_curation_backlog_groups_by_routed_pages(tmp_path: Path):
    store = _store(tmp_path)
    store.write_page(
        "topics/agent-atlas",
        "Agent Atlas (agent-compliance-cockpit)",
        {"summary": "atlas", "body": "page"},
    )
    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE pages SET updated_at = ? WHERE path = 'topics/agent-atlas'",
            (_timestamp(3),),
        )
    project_one = _ingest(store, "one", str(HOME / "agent-compliance-cockpit"))
    project_two = _ingest(store, "two", str(HOME / "agent-compliance-cockpit"))
    routed = _ingest(store, "routed", str(HOME / "code"))
    unrouted = _ingest(store, "unrouted", str(HOME / "code"))
    routed_none = _ingest(store, "none", str(HOME / "agent-compliance-cockpit"))
    with store.connection(write=True) as conn:
        conn.executemany(
            "INSERT INTO session_topics(session_id, page_path, origin, created_at) "
            "VALUES (?, ?, 'manual', ?)",
            [
                (routed, "topics/agent-atlas", _timestamp(2)),
                (routed_none, None, _timestamp(2)),
            ],
        )
        targets = session_targets(conn, home=HOME)

    sessions = [
        (project_one, str(HOME / "agent-compliance-cockpit"), _timestamp(1)),
        (project_two, str(HOME / "agent-compliance-cockpit"), _timestamp(1)),
        (routed, str(HOME / "code"), _timestamp(1)),
        (unrouted, str(HOME / "code"), _timestamp(1)),
        (routed_none, str(HOME / "agent-compliance-cockpit"), _timestamp(1)),
    ]
    pages = [
        (
            "topics/agent-atlas",
            "Agent Atlas (agent-compliance-cockpit)",
            _timestamp(3),
        )
    ]
    result = curation_backlog(sessions, pages, now=NOW, targets=targets)

    assert targets[unrouted] is None
    assert len(result) == 1
    item = result[0]
    assert item["project"] == "agent-atlas"
    assert item["living_page"] == "topics/agent-atlas"
    assert item["workspaces"] == [
        str(HOME / "agent-compliance-cockpit"),
        str(HOME / "code"),
    ]
    assert item["workspace"] == str(HOME / "agent-compliance-cockpit")
    assert item["new_sessions"] == 3
    assert item["pages"] == ["topics/agent-atlas"]


def test_build_request_selects_routed_sessions(tmp_path: Path):
    store = _store(tmp_path)
    store.write_page("topics/agent-atlas", "Agent Atlas", {"summary": "a", "body": "b"})
    routed = _ingest(store, "routed", str(HOME / "code"))
    unrouted = _ingest(store, "unrouted", str(HOME / "code"))
    with store.connection(write=True) as conn:
        conn.execute(
            "INSERT INTO session_topics(session_id, page_path, origin, created_at) "
            "VALUES (?, 'topics/agent-atlas', 'manual', ?)",
            (routed, _timestamp(2)),
        )
    item = {
        "project": "agent-atlas",
        "living_page": "topics/agent-atlas",
        "new_sessions": 1,
        "last_session_at": _timestamp(1),
        "pages": ["topics/agent-atlas"],
        "last_page_update": None,
    }

    with store.connection(write=True) as conn:
        result = build_request(conn, item, related=0)

    texts = [entry["text"] for entry in result["request"]["evidence"]]
    assert texts == ["routed content"]
    assert result["request"]["living_page"] == "topics/agent-atlas"
    with store.connection() as conn:
        assert session_targets(conn, home=HOME)[unrouted] is None


def test_config_defaults_environment_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    defaults = load_config(home=tmp_path / "home", environ={})
    assert defaults.curation_generic_workspaces == (
        "code",
        "emails",
        "work",
        "projects",
        "repos",
        "src",
        "documents",
        "desktop",
        "downloads",
        "tmp",
    )

    monkeypatch.setenv("WIKIBRICKS_CURATION_GENERIC_WORKSPACES", "code, Custom Workspace")
    overridden = load_config(home=tmp_path / "home")
    assert overridden.curation_generic_workspaces == ("code", "Custom Workspace")

    with pytest.raises(ValueError, match="WIKIBRICKS_CURATION_GENERIC_WORKSPACES"):
        load_config(
            home=tmp_path / "home",
            environ={"WIKIBRICKS_CURATION_GENERIC_WORKSPACES": "code,"},
        )


def test_migrations_apply_and_delete_session_topics_with_sessions(tmp_path: Path):
    store = _store(tmp_path)
    session_id = _ingest(store, "routed", str(HOME / "code"))
    with store.connection(write=True) as conn:
        conn.execute(
            "INSERT INTO session_topics(session_id, page_path, origin, created_at) "
            "VALUES (?, 'topics/agent-atlas', 'manual', ?)",
            (session_id, _timestamp(2)),
        )
        count = conn.execute("SELECT count(*) FROM session_topics").fetchone()[0]
    store.migrate()
    with store.connection(write=True) as conn:
        conn.execute("DROP TABLE session_topics")
        conn.execute(
            "DELETE FROM schema_migrations WHERE name = '0004_session_topics.sql'"
        )
    store.migrate()
    with store.connection() as conn:
        assert count == 1
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        remaining = conn.execute("SELECT count(*) FROM session_topics").fetchone()[0]

    assert remaining == 0
