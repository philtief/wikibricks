from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wikibricks import mcp_server
from wikibricks.maintenance import curate_database
from wikibricks.mcp_server import dispatch_tool
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.storage.sqlite_store import SQLiteStore

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _timestamp(offset_hours: int, *, reference: datetime | None = None) -> str:
    reference = reference or datetime.now(timezone.utc)
    return (reference - timedelta(hours=offset_hours)).isoformat()


def test_curation_backlog_counts_recent_sessions_without_a_page():
    from wikibricks.curation.backlog import curation_backlog

    sessions = [
        ("one", "/Users/u/work/slide-hub", _timestamp(1, reference=NOW)),
        ("two", "/Users/u/work/slide-hub", _timestamp(2, reference=NOW)),
        ("three", "/Users/u/work/slide-hub", _timestamp(3, reference=NOW)),
    ]
    targets = {
        "one": "topics/slide-hub",
        "two": "topics/slide-hub",
        "three": "topics/slide-hub",
    }

    result = curation_backlog(sessions, [], now=NOW, targets=targets)

    assert result == [
        {
            "project": "slide-hub",
            "living_page": "topics/slide-hub",
            "workspace": "/Users/u/work/slide-hub",
            "workspaces": ["/Users/u/work/slide-hub"],
            "new_sessions": 3,
            "last_session_at": _timestamp(1, reference=NOW),
            "pages": [],
            "last_page_update": None,
        }
    ]


def test_curation_backlog_ignores_sessions_covered_by_a_newer_page():
    from wikibricks.curation.backlog import curation_backlog

    sessions = [
        ("one", "/Users/u/work/agent-compliance-cockpit", _timestamp(3, reference=NOW)),
        ("two", "/Users/u/work/agent-compliance-cockpit", _timestamp(4, reference=NOW)),
        ("three", "/Users/u/work/agent-compliance-cockpit", _timestamp(5, reference=NOW)),
    ]
    pages = [
        ("topics/agent-atlas", "Agent Atlas (agent-compliance-cockpit)", _timestamp(2, reference=NOW))
    ]

    assert curation_backlog(sessions, pages, now=NOW, targets={
        "one": "topics/agent-atlas",
        "two": "topics/agent-atlas",
        "three": "topics/agent-atlas",
    }) == []


def test_curation_backlog_counts_only_sessions_newer_than_the_page():
    from wikibricks.curation.backlog import curation_backlog

    sessions = [
        ("one", "/Users/u/work/slide-hub", _timestamp(1, reference=NOW)),
        ("two", "/Users/u/work/slide-hub", _timestamp(2, reference=NOW)),
        ("three", "/Users/u/work/slide-hub", _timestamp(5, reference=NOW)),
    ]
    pages = [("topics/slide-hub", "Slide Hub", _timestamp(3, reference=NOW))]
    result = curation_backlog(sessions, pages, now=NOW, targets={
        "one": "topics/slide-hub",
        "two": "topics/slide-hub",
        "three": "topics/slide-hub",
    })

    assert result[0]["new_sessions"] == 2
    assert result[0]["pages"] == ["topics/slide-hub"]
    assert result[0]["last_page_update"] == _timestamp(3, reference=NOW)


def test_curation_backlog_filters_expired_or_invalid_workspaces_and_orders_results():
    from wikibricks.curation.backlog import curation_backlog

    sessions = [
        ("old-slide", "/Users/u/work/slide-hub", _timestamp(24 * 8, reference=NOW)),
        ("slide", "/Users/u/work/slide-hub", _timestamp(1, reference=NOW)),
        ("home", str(Path.home()), _timestamp(1, reference=NOW)),
        ("empty", "", _timestamp(1, reference=NOW)),
        ("missing", None, _timestamp(1, reference=NOW)),
        ("zeta-one", "/Users/u/work/zeta", _timestamp(2, reference=NOW)),
        ("zeta-two", "/Users/u/work/zeta", _timestamp(3, reference=NOW)),
    ]
    targets = {
        "old-slide": "topics/slide-hub",
        "slide": "topics/slide-hub",
        "home": None,
        "empty": None,
        "missing": None,
        "zeta-one": "topics/zeta",
        "zeta-two": "topics/zeta",
    }

    assert curation_backlog(
        sessions, [], now=NOW, days=7, targets=targets
    ) == [
        {
            "project": "zeta",
            "living_page": "topics/zeta",
            "workspace": "/Users/u/work/zeta",
            "workspaces": ["/Users/u/work/zeta"],
            "new_sessions": 2,
            "last_session_at": _timestamp(2, reference=NOW),
            "pages": [],
            "last_page_update": None,
        },
        {
            "project": "slide-hub",
            "living_page": "topics/slide-hub",
            "workspace": "/Users/u/work/slide-hub",
            "workspaces": ["/Users/u/work/slide-hub"],
            "new_sessions": 1,
            "last_session_at": _timestamp(1, reference=NOW),
            "pages": [],
            "last_page_update": None,
        },
    ]


def test_sqlite_curation_and_mcp_index_report_the_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    database = tmp_path / "wikibricks.db"
    store = SQLiteStore(database)
    store.migrate()
    store.write_page(
        "topics/wikibricks", "WikiBricks", {"summary": "curated", "body": "page"}
    )
    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE pages SET updated_at = ? WHERE path = 'topics/wikibricks'",
            (_timestamp(1),),
        )

    for index in range(3):
        store.ingest_session(
            SessionRecord(
                harness="test-harness",
                external_id=f"slide-{index}",
                user_id="user",
                workspace="/Users/u/work/slide-hub",
                updated_at=_timestamp(1),
                events=[SessionEvent("0", "user", "slide hub work")],
            )
        )
        store.ingest_session(
            SessionRecord(
                harness="test-harness",
                external_id=f"wikibricks-{index}",
                user_id="user",
                workspace="/Users/u/work/wikibricks",
                updated_at=_timestamp(2),
                events=[SessionEvent("0", "user", "wikibricks work")],
            )
        )

    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE workspace = '/Users/u/work/slide-hub'",
            (_timestamp(1),),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE workspace = '/Users/u/work/wikibricks'",
            (_timestamp(2),),
        )

    result = curate_database(database)
    assert [item["project"] for item in result["curation_backlog"]] == ["slide-hub"]
    assert result["curation_backlog"][0]["new_sessions"] == 3

    monkeypatch.setenv("WIKIBRICKS_DATABASE_PATH", str(database))
    indexed = dispatch_tool("wiki_index", {}, tools=mcp_server._build_tools())
    backlog = [item for item in indexed if item["page_type"] == "backlog"]
    assert len(backlog) == 1
    assert backlog[0]["path"] == "_meta/curation-backlog"
    assert backlog[0]["items"][0]["project"] == "slide-hub"

    with store.connection(write=True) as conn:
        conn.execute("UPDATE sessions SET updated_at = ?", (_timestamp(72),))

    store.write_page(
        "topics/slide-hub", "Slide Hub", {"summary": "curated", "body": "page"}
    )
    indexed = dispatch_tool("wiki_index", {}, tools=mcp_server._build_tools())
    assert not [item for item in indexed if item["page_type"] == "backlog"]


def test_backfilled_old_sessions_do_not_enter_the_backlog(tmp_path: Path):
    # A nightly backfill imports old sessions today; only when the work happened counts.
    database = tmp_path / "wikibricks.db"
    store = SQLiteStore(database)
    store.migrate()
    for index, age_hours in enumerate((24 * 30, 24 * 30, 2)):
        store.ingest_session(
            SessionRecord(
                harness="test-harness",
                external_id=f"workshop-{index}",
                user_id="user",
                workspace="/Users/u/blog/workshop",
                updated_at=_timestamp(age_hours),
                events=[SessionEvent("0", "user", "workshop work")],
            )
        )

    backlog = curate_database(database)["curation_backlog"]

    assert [(item["project"], item["new_sessions"]) for item in backlog] == [("workshop", 1)]
