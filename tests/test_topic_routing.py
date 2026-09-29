from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from wikibricks.config import load_config
from wikibricks.curation.backlog import (
    curation_backlog,
    load_curation_backlog,
    session_targets,
    unrouted_sessions,
)
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.storage.sqlite_store import SQLiteStore
from wikibricks_curator.curator import run_curator
from wikibricks_curator.evidence import build_request
from wikibricks_curator.router import route_sessions

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


def _topic_routes(store: SQLiteStore, external_ids: tuple[str, ...]) -> list[str]:
    return [_ingest(store, external_id, str(HOME / "code")) for external_id in external_ids]


def test_route_sessions_writes_model_routes_and_builds_backlog(tmp_path: Path):
    store = _store(tmp_path)
    store.write_page(
        "topics/agent-atlas",
        "Agent Atlas",
        {"summary": "Atlas summary", "body": "Atlas body"},
    )
    store.write_page("projects/unity", "Unity", {"summary": "Unity", "body": "project"})
    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE pages SET updated_at = ? WHERE path = 'topics/agent-atlas'",
            (_timestamp(4),),
        )
    atlas, unity, quick = _topic_routes(store, ("atlas", "unity", "quick"))
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def chat(prompt: str, request: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        calls.append((prompt, request, schema))
        return {
            "routes": [
                {"session_id": atlas, "page_path": "topics/agent-atlas", "reason": "atlas"},
                {"session_id": unity, "page_path": "topics/unity-gateway", "reason": "new"},
                {"session_id": quick, "page_path": None, "reason": "quick"},
            ]
        }

    result = route_sessions(store, chat, since=_timestamp(3))

    assert result == {
        "sessions": 3,
        "routed": 3,
        "to_none": 1,
        "new_topics": ["topics/unity-gateway"],
        "invalid": 0,
        "error": None,
    }
    assert calls[0][0].startswith("# Route sessions to wiki topics")
    assert [item["path"] for item in calls[0][1]["candidates"]] == [
        "projects/unity",
        "topics/agent-atlas",
    ]
    assert sorted(item["session_id"] for item in calls[0][1]["sessions"]) == sorted([atlas, unity, quick])
    assert calls[0][2]["properties"]["routes"]["items"]["properties"]["page_path"]["type"] == [
        "string",
        "null",
    ]
    with store.connection() as conn:
        rows = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT session_id, page_path, origin FROM session_topics"
            )
        }
        targets = session_targets(conn, home=HOME)
    assert rows == {
        atlas: ("topics/agent-atlas", "local-curator-router"),
        unity: ("topics/unity-gateway", "local-curator-router"),
        quick: (None, "local-curator-router"),
    }
    assert targets == {
        atlas: "topics/agent-atlas",
        unity: "topics/unity-gateway",
        quick: None,
    }
    with store.connection() as conn:
        backlog = load_curation_backlog(conn, home=HOME, now=NOW)
    assert {item["living_page"] for item in backlog} == {
        "topics/unity-gateway",
        "topics/agent-atlas",
    }
    assert all(item["new_sessions"] == 1 for item in backlog)


def test_route_sessions_skips_invalid_routes(tmp_path: Path):
    store = _store(tmp_path)
    store.write_page("topics/valid", "Valid", {"summary": "s", "body": "b"})
    (folder, email, home, project, bad_slug) = _topic_routes(
        store,
        ("folder", "email", "home", "project", "bad-slug"),
    )

    def chat(_prompt: str, _request: dict[str, Any], _schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "routes": [
                {"session_id": "missing", "page_path": "topics/valid", "reason": "unknown"},
                {"session_id": folder, "page_path": "topics/code", "reason": "folder"},
                {"session_id": email, "page_path": "topics/emails", "reason": "folder"},
                {"session_id": home, "page_path": "topics/home", "reason": "folder"},
                {"session_id": project, "page_path": "projects/x", "reason": "not candidate"},
                {"session_id": bad_slug, "page_path": "Topics/Bad Slug", "reason": "bad"},
            ]
        }

    with store.connection() as conn:
        before = set(unrouted_sessions(conn, since=_timestamp(2), home=HOME))
    result = route_sessions(store, chat, since=_timestamp(2))
    with store.connection() as conn:
        after = set(unrouted_sessions(conn, since=_timestamp(2), home=HOME))

    assert result["sessions"] == 5
    assert result["routed"] == 0
    assert result["to_none"] == 0
    assert result["new_topics"] == []
    assert result["invalid"] == 6
    assert result["error"] is None
    assert before == after


def test_route_sessions_routes_empty_session_without_chat(tmp_path: Path):
    store = _store(tmp_path)
    store.ingest_session(
        SessionRecord(
            harness="test-harness",
            external_id="empty",
            user_id="user",
            workspace=str(HOME / "code"),
            updated_at=_timestamp(1),
            events=[],
        )
    )
    with store.connection(write=True) as conn:
        session_id = conn.execute("SELECT session_id FROM sessions").fetchone()[0]

    def chat(*_args: Any) -> dict[str, Any]:
        raise AssertionError("chat should not be called")

    result = route_sessions(store, chat, since=_timestamp(2))

    assert result == {
        "sessions": 1,
        "routed": 1,
        "to_none": 1,
        "new_topics": [],
        "invalid": 0,
        "error": None,
    }
    with store.connection() as conn:
        row = conn.execute(
            "SELECT page_path, origin FROM session_topics WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    assert tuple(row) == (None, "local-curator-router")


def test_route_sessions_retries_once_and_reports_failure(tmp_path: Path):
    store = _store(tmp_path)
    _topic_routes(store, ("one", "two"))
    attempts = 0

    def succeeding(_prompt: str, _request: dict[str, Any], _schema: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        assert attempts == 2
        return {"routes": []}

    result = route_sessions(store, succeeding, since=_timestamp(2))
    assert result["error"] is None

    def failing(_prompt: str, _request: dict[str, Any], _schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("model unavailable")

    failure = route_sessions(store, failing, since=_timestamp(2))
    assert failure["error"] == "model unavailable"
    with store.connection() as conn:
        assert conn.execute(
            "SELECT count(*) FROM session_topics WHERE origin = 'local-curator-router'"
        ).fetchone()[0] == 0


def test_route_sessions_dry_run_writes_nothing(tmp_path: Path):
    store = _store(tmp_path)
    (session_id,) = _topic_routes(store, ("dry",))

    def chat(_prompt: str, _request: dict[str, Any], _schema: dict[str, Any]) -> dict[str, Any]:
        return {"routes": [{"session_id": session_id, "page_path": "topics/dry", "reason": "ok"}]}

    result = route_sessions(store, chat, since=_timestamp(2), dry_run=True)

    assert result["routed"] == 1
    assert result["new_topics"] == ["topics/dry"]
    with store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM session_topics").fetchone()[0] == 0


def test_routed_time_counts_as_new_evidence(tmp_path: Path):
    store = _store(tmp_path)
    store.write_page("topics/agent-atlas", "Agent Atlas", {"summary": "a", "body": "b"})
    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE pages SET updated_at = ? WHERE path = 'topics/agent-atlas'",
            (NOW.isoformat(),),
        )
    session_id = _ingest(
        store,
        "routed-at",
        str(HOME / "code"),
        (NOW - timedelta(hours=1)).isoformat(),
    )
    with store.connection(write=True) as conn:
        conn.execute(
            "INSERT INTO session_topics(session_id, page_path, origin, created_at) "
            "VALUES (?, 'topics/agent-atlas', 'local-curator-router', ?)",
            (session_id, (NOW + timedelta(hours=1)).isoformat()),
        )
        targets = session_targets(conn, home=HOME)
        assert targets[session_id] == "topics/agent-atlas"
    with store.connection() as conn:
        result = load_curation_backlog(
            conn,
            now=NOW + timedelta(hours=2),
            home=HOME,
        )
    assert result[0]["new_sessions"] == 1

    item = {
        "project": "agent-atlas",
        "living_page": "topics/agent-atlas",
        "new_sessions": 1,
        "last_session_at": (NOW + timedelta(hours=1)).isoformat(),
        "pages": ["topics/agent-atlas"],
        "last_page_update": NOW.isoformat(),
    }
    with store.connection(write=True) as conn:
        built = build_request(conn, item)
    assert built is not None
    assert built["request"]["evidence"][0]["text"] == "routed-at content"
    assert built["newest_evidence_at"] == (NOW + timedelta(hours=1)).isoformat()


def test_run_curator_routes_before_backlog_and_hides_routing_errors(tmp_path: Path):
    store = _store(tmp_path)
    store.write_page("topics/agent-atlas", "Agent Atlas", {"summary": "a", "body": "b"})
    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE pages SET updated_at = ? WHERE path = 'topics/agent-atlas'",
            (_timestamp(5),),
        )
    routed = _ingest(store, "routed", str(HOME / "code"), _timestamp(4))
    _ingest(store, "router", str(HOME / "code"))
    with store.connection(write=True) as conn:
        conn.execute(
            "INSERT INTO session_topics(session_id, page_path, origin, created_at) "
            "VALUES (?, 'topics/agent-atlas', 'manual', ?)",
            (routed, _timestamp(3)),
        )
    calls = 0

    def chat(prompt: str, request: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if prompt.startswith("# Route sessions"):
            raise RuntimeError("router temporarily unavailable")
        return {"proposals": []}

    result = run_curator(tmp_path / "wikibricks.db", chat=chat)

    assert calls == 3
    assert result["errors"] == 0
    assert result["routing"]["error"] == "router temporarily unavailable"
    assert result["routing"]["sessions"] == 1
    assert result["routing"]["invalid"] == 0
    assert result["projects"][0]["status"] == "no_changes"
    with store.connection() as conn:
        assert conn.execute(
            "SELECT count(*) FROM session_topics WHERE origin = 'local-curator-router'"
        ).fetchone()[0] == 0


def test_pages_named_after_a_container_folder_are_not_candidates(tmp_path: Path):
    # The live store already has a `topics/emails` page from before routing existed.
    store = _store(tmp_path)
    store.write_page("topics/emails", "Emails", {"summary": "mixed", "body": "Unrelated work."})
    store.write_page("topics/agent-atlas", "Agent Atlas", {"summary": "s", "body": "Page."})
    email = _ingest(store, "email-session", "/Users/u/work/50-communications/emails", _timestamp(1))
    seen: dict[str, Any] = {}

    def chat(_prompt: str, request: dict[str, Any], _schema: dict[str, Any]) -> dict[str, Any]:
        seen["candidates"] = [candidate["path"] for candidate in request["candidates"]]
        return {"routes": [{"session_id": email, "page_path": "topics/emails", "reason": "folder"}]}

    result = route_sessions(store, chat, since=NOW - timedelta(days=7))

    assert seen["candidates"] == ["topics/agent-atlas"]
    assert result["invalid"] == 1
    with store.connection() as conn:
        assert email in unrouted_sessions(conn, since=NOW - timedelta(days=7))
