from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request
from uuid import uuid4

import pytest

from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.storage.sqlite_store import SQLiteStore
from wikibricks_remote.resources import load_policy

TEN = "2026-01-03T10:00:00+00:00"
OLD_SESSION = "2026-01-03T09:00:00+00:00"
MIDDLE_SESSION = "2026-01-04T09:00:00+00:00"
NEW_SESSION = "2026-01-04T11:00:00+00:00"


def _recent_timestamp(offset_hours: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "wikibricks.db")
    store.migrate()
    return store


def _ingest(
    store: SQLiteStore,
    external_id: str,
    workspace: str,
    updated_at: str,
    events: list[SessionEvent],
) -> None:
    store.ingest_session(
        SessionRecord(
            harness="test-harness",
            external_id=external_id,
            user_id="user",
            workspace=workspace,
            updated_at=updated_at,
            events=events,
        )
    )


def _populate(tmp_path: Path) -> tuple[SQLiteStore, dict]:
    store = _store(tmp_path)
    store.write_page("topics/alpha", "Alpha", {"summary": "old", "body": "alpha page"})
    store.write_page(
        "topics/alpha-notes", "Alpha Notes", {"summary": "related", "body": "alpha notes"}
    )
    store.write_page("topics/beta", "Beta", {"summary": "other", "body": "beta page"})
    _ingest(
        store,
        "old-alpha",
        "/Users/u/work/Alpha One",
        OLD_SESSION,
        [
            SessionEvent("user-1", "user", "old user"),
            SessionEvent("tool-1", "tool_call", "old tool"),
        ],
    )
    _ingest(
        store,
        "middle-alpha",
        "/Users/u/work/alpha_one",
        MIDDLE_SESSION,
        [
            SessionEvent("user-2", "user", "middle user"),
            SessionEvent("assistant-2", "assistant", "middle assistant"),
            SessionEvent("tool-2", "tool_result", "middle tool"),
        ],
    )
    _ingest(
        store,
        "new-alpha",
        "/Users/u/work/Alpha One",
        NEW_SESSION,
        [
            SessionEvent("user-3", "user", "new user"),
            SessionEvent("assistant-3", "assistant", "new assistant"),
            SessionEvent("tool-3", "tool_call", "new tool"),
        ],
    )
    _ingest(
        store,
        "beta",
        "/Users/u/work/beta",
        NEW_SESSION,
        [SessionEvent("user-beta", "user", "beta user")],
    )
    item = {
        "project": "alpha-one",
        "workspace": "/Users/u/work/Alpha One",
        "new_sessions": 2,
        "last_session_at": NEW_SESSION,
        "pages": ["topics/alpha"],
        "last_page_update": TEN,
    }
    return store, item


def test_build_request_selects_active_pages_and_user_assistant_evidence(tmp_path: Path):
    from wikibricks_curator.evidence import build_request

    store, item = _populate(tmp_path)
    with store.connection() as conn:
        result = build_request(conn, item, related=1)

    assert result["request"]["living_page"] == "topics/alpha"
    assert [page["path"] for page in result["pages"]] == ["topics/alpha", "topics/alpha-notes"]
    with store.connection() as conn:
        version = conn.execute(
            "SELECT version_id, content_hash, content, tags, source_ids, page_type, title "
            "FROM page_versions v JOIN pages p ON p.page_id = v.page_id "
            "WHERE p.path = ?",
            ("topics/alpha",),
        ).fetchone()
    page = result["pages"][0]
    assert page["evidence_id"] == f"page-version:{version['version_id']}"
    assert page["base_version_id"] == version["version_id"]
    assert page["base_content_hash"] == version["content_hash"]
    assert page["content"] == json.loads(version["content"])

    evidence = result["request"]["evidence"]
    assert [entry["kind"] for entry in evidence] == ["user", "assistant", "user", "assistant"]
    assert [entry["text"] for entry in evidence] == [
        "middle user",
        "middle assistant",
        "new user",
        "new assistant",
    ]
    session_paths = {entry["session"] for entry in evidence}
    assert all(
        path.endswith(("middle-alpha", "new-alpha")) for path in session_paths
    )
    assert result["evidence_ids"] == {entry["evidence_id"] for entry in evidence} | {
        page["evidence_id"] for page in result["pages"]
    }
    assert result["newest_evidence_at"] == NEW_SESSION
    assert result["request"]["current_pages"] == result["pages"]
    assert result["request"]["evidence"] == evidence
    assert result["request"]["similarity_candidates"] == []


def test_build_request_trims_to_the_newest_evidence(tmp_path: Path):
    from wikibricks_curator.evidence import build_request

    store, item = _populate(tmp_path)
    with store.connection() as conn:
        limited = build_request(conn, item, max_events=3)
    assert [entry["text"] for entry in limited["request"]["evidence"]] == [
        "middle assistant",
        "new user",
        "new assistant",
    ]

    with store.connection() as conn:
        bounded = build_request(conn, item, max_chars=24, max_event_chars=20)
    assert [entry["text"] for entry in bounded["request"]["evidence"]] == [
        "new user",
        "new assistant",
    ]
    assert bounded["newest_evidence_at"] == NEW_SESSION


def test_build_request_cursor_excludes_older_sessions(tmp_path: Path):
    from wikibricks_curator.evidence import build_request

    store, item = _populate(tmp_path)
    with store.connection() as conn:
        result = build_request(conn, item, cursor="2026-01-04T10:00:00+00:00")

    assert [entry["text"] for entry in result["request"]["evidence"]] == [
        "new user",
        "new assistant",
    ]
    assert result["newest_evidence_at"] == NEW_SESSION


def test_build_request_truncates_each_event_and_returns_none_without_evidence(tmp_path: Path):
    from wikibricks_curator.evidence import build_request

    store, item = _populate(tmp_path)
    with store.connection() as conn:
        result = build_request(conn, item, max_event_chars=6)
    assert [entry["text"] for entry in result["request"]["evidence"]] == [
        "middle",
        "middle",
        "new us",
        "new as",
    ]

    old_item = {**item, "last_page_update": NEW_SESSION}
    with store.connection() as conn:
        assert build_request(conn, old_item) is None


def test_build_request_defaults_living_page_for_a_project_without_coverage(tmp_path: Path):
    from wikibricks_curator.evidence import build_request

    store, _ = _populate(tmp_path)
    with store.connection() as conn:
        result = build_request(
            conn,
            {
                "project": "beta",
                "workspace": "/Users/u/work/beta",
                "new_sessions": 1,
                "last_session_at": NEW_SESSION,
                "pages": [],
                "last_page_update": None,
            },
            related=0,
        )

    assert result["request"]["living_page"] == "topics/beta"
    assert [page["path"] for page in result["pages"]] == ["topics/beta"]
    assert [entry["text"] for entry in result["request"]["evidence"]] == ["beta user"]


def test_evidence_pages_support_a_cited_update_page_patch(tmp_path: Path):
    from wikibricks_curator.evidence import build_request
    from wikibricks_remote.proposals import build_patches

    store, item = _populate(tmp_path)
    with store.connection() as conn:
        result = build_request(conn, item)
    evidence_id = result["request"]["evidence"][0]["evidence_id"]
    raw_result = {
        "proposals": [
            {
                "group": "update-alpha",
                "operation": "update_page",
                "path": "topics/alpha",
                "title": "Updated Alpha",
                "page_type": "concept",
                "summary": "Updated summary",
                "body": "Derived from session evidence.",
                "tags": ["alpha"],
                "source_ids": [],
                "target_path": None,
                "evidence_ids": [evidence_id],
                "reason": "Session evidence changed the project state.",
                "risk_class": "low",
            }
        ]
    }

    patches = build_patches(
        raw_result,
        run_id=uuid4(),
        pages=result["pages"],
        evidence_ids=result["evidence_ids"],
        policy=load_policy(),
    )

    assert len(patches) == 1
    patch = patches[0]
    assert patch["operation"] == "update_page"
    assert patch["path"] == "topics/alpha"
    assert patch["proposal"]["content"] == {
        "summary": "Updated summary",
        "body": "Derived from session evidence.",
    }
    assert patch["base_version_id"] == result["pages"][0]["base_version_id"]
    assert patch["base_content_hash"] == result["pages"][0]["base_content_hash"]
    assert patch["evidence_ids"] == [evidence_id]


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content

    def read(self) -> bytes:
        return self.content.encode("utf-8")


def _opener(content: str):
    calls: list[Request] = []

    def fake_opener(request: Request, timeout: float) -> _Response:
        calls.append(request)
        if content is None:
            response = ""
        else:
            response = json.dumps({"choices": [{"message": {"content": content}}]})
        return _Response(response)

    return calls, fake_opener


def _payload(request: Request) -> dict:
    return json.loads(request.data)


def test_chat_json_posts_model_messages_and_parses_plain_json():
    from wikibricks_curator.gateway import chat_json

    calls, opener = _opener('{"proposals": []}')
    schema = {"type": "object"}
    request = {"project": "alpha"}
    result = chat_json(
        "Curate pages",
        request,
        schema,
        base_url="https://gateway.example/",
        token="secret",
        model="databricks-model",
        opener=opener,
    )

    assert result == {"proposals": []}
    assert len(calls) == 1
    assert calls[0].get_full_url() == "https://gateway.example/chat/completions"
    assert calls[0].get_header("Authorization") == "Bearer secret"
    payload = _payload(calls[0])
    assert payload["model"] == "databricks-model"
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == 8192
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"].startswith("Curate pages")
    assert 'Return JSON matching this schema:' in payload["messages"][0]["content"]
    assert json.loads(payload["messages"][0]["content"].split(":", 1)[1]) == schema
    assert payload["messages"][1] == {"role": "user", "content": json.dumps(request, ensure_ascii=False)}


def test_chat_json_parses_fenced_and_trailing_json():
    from wikibricks_curator.gateway import chat_json

    _, fenced_opener = _opener('```json\n{"value": 1}\n```')
    assert chat_json(
        "s",
        {},
        {},
        base_url="https://x.example",
        token="t",
        model="m",
        opener=fenced_opener,
    ) == {"value": 1}

    _, trailing_opener = _opener('{"value": 2}\n\nModel commentary.')
    assert chat_json(
        "s",
        {},
        {},
        base_url="https://x.example",
        token="t",
        model="m",
        opener=trailing_opener,
    ) == {"value": 2}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "empty content"),
        ("[]", "JSON object"),
        ("not json", "decode"),
    ],
)
def test_chat_json_rejects_invalid_model_output(content: str | None, message: str):
    from wikibricks_curator.gateway import chat_json

    _, opener = _opener(content or "")
    with pytest.raises(RuntimeError, match=message):
        chat_json(
            "s",
            {},
            {},
            base_url="https://x.example",
            token="t",
            model="m",
            opener=opener,
        )


def test_chat_json_wraps_http_errors():
    from wikibricks_curator.gateway import chat_json

    def opener(request: Request, timeout: float):
        raise HTTPError("url", 500, "Server Error", None, None)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        chat_json(
            "s",
            {},
            {},
            base_url="https://x.example",
            token="t",
            model="m",
            opener=opener,
        )


def test_resolve_token_prefers_env(monkeypatch: pytest.MonkeyPatch):
    from wikibricks_curator.gateway import resolve_token

    monkeypatch.setenv("WIKIBRICKS_GATEWAY_TOKEN", "env-token")
    assert resolve_token("profile") == "env-token"


def test_resolve_token_calls_databricks_profile(monkeypatch: pytest.MonkeyPatch):
    from wikibricks_curator.gateway import resolve_token

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='{"access_token": "cli-token"}', stderr="")

    monkeypatch.delenv("WIKIBRICKS_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr("subprocess.run", fake_run)
    assert resolve_token("staging") == "cli-token"
    assert calls == [["databricks", "auth", "token", "--profile", "staging"]]


def test_build_request_compares_timestamps_across_utc_offsets(tmp_path: Path):
    from wikibricks_curator.evidence import build_request

    store = _store(tmp_path)
    # 12:30+02:00 is 10:30 UTC: before the 11:00 UTC page update, although it sorts after as text.
    _ingest(store, "before", "/Users/u/work/offsets", "2026-01-04T12:30:00+02:00",
            [SessionEvent("0", "user", "before the page update")])
    _ingest(store, "after", "/Users/u/work/offsets", "2026-01-04T11:30:00+00:00",
            [SessionEvent("0", "user", "after the page update")])
    item = {"project": "offsets", "pages": [], "last_page_update": "2026-01-04T11:00:00+00:00"}

    with store.connection() as conn:
        built = build_request(conn, item)
        later = build_request(conn, item, cursor="2026-01-04T13:15:00+02:00")

    assert [entry["text"] for entry in built["request"]["evidence"]] == ["after the page update"]
    assert [entry["text"] for entry in later["request"]["evidence"]] == ["after the page update"]


def _backlog_project(
    store: SQLiteStore,
    *,
    project: str,
    page: bool = False,
) -> dict:
    from wikibricks.curation.backlog import load_curation_backlog

    if page:
        store.write_page(
            f"topics/{project}",
            project.title(),
            {"summary": "current summary", "body": "The project page contains durable facts."},
        )
        with store.connection(write=True) as conn:
            conn.execute(
                "UPDATE pages SET updated_at = ? WHERE path = ?",
                (_recent_timestamp(3), f"topics/{project}"),
            )
    store.ingest_session(
        SessionRecord(
            harness="test-harness",
            external_id=f"{project}-new",
            user_id="user",
            workspace=f"/Users/u/work/{project}",
            updated_at=_recent_timestamp(1),
            events=[SessionEvent("user-1", "user", f"{project} learned one durable fact")],
        )
    )
    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE external_id = ?",
            (_recent_timestamp(1), f"{project}-new"),
        )
    with store.connection() as conn:
        backlog = load_curation_backlog(conn, limit=3)
    return next(item for item in backlog if item["project"] == project)


def _proposal(
    *,
    operation: str,
    path: str,
    evidence_id: str,
    body: str = "Derived from session evidence.",
    summary: str = "Updated summary",
) -> dict:
    return {
        "group": "main",
        "operation": operation,
        "path": path,
        "title": path.rsplit("/", 1)[-1].title(),
        "page_type": "entity",
        "summary": summary,
        "body": body,
        "tags": [],
        "source_ids": [],
        "target_path": None,
        "evidence_ids": [evidence_id],
        "reason": "The session evidence changed the project state.",
        "risk_class": "low",
    }


def test_run_curator_creates_page_then_stops_at_cursor(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="create-project")

    def chat(system_prompt: str, request: dict, schema: dict) -> dict:
        assert system_prompt
        assert schema
        return {"proposals": [_proposal(
            operation="create_page",
            path=request["living_page"],
            evidence_id=request["evidence"][0]["evidence_id"],
        )]}

    result = run_curator(store.database_path, chat=chat, projects=1)
    project = result["projects"][0]
    assert project["status"] == "applied"
    assert project["guarded"] == 0
    assert project["counts"] == {"applied": 1}
    assert project["proposals"][0]["operation"] == "create_page"
    assert store.read_page("topics/create-project")["version"] == 1
    assert store.get_sync_cursor("curator:create-project")["newest_evidence_at"]

    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE pages SET updated_at = ? WHERE path = 'topics/create-project'",
            (_recent_timestamp(3),),
        )

    second = run_curator(store.database_path, chat=chat, projects=1)
    assert second["projects"][0]["status"] == "no_evidence"
    assert second["projects"][0]["run_id"] is None


def test_run_curator_updates_covered_page(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    item = _backlog_project(store, project="update-project", page=True)
    assert item["pages"] == ["topics/update-project"]

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        return {"proposals": [_proposal(
            operation="update_page",
            path=request["living_page"],
            evidence_id=request["evidence"][0]["evidence_id"],
            summary="New current summary",
            body="New body is at least half the current page length.",
        )]}

    result = run_curator(store.database_path, chat=chat, projects=1)
    assert result["projects"][0]["status"] == "applied"
    page = store.read_page("topics/update-project")
    assert page["version"] == 2
    assert page["content"] == {
        "summary": "New current summary",
        "body": "New body is at least half the current page length.",
    }


def test_run_curator_labels_created_page_and_applied_link(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    store.write_page(
        "topics/related-target",
        "Related Target",
        {"summary": "Label project target", "body": "The label project target already exists."},
    )
    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE pages SET updated_at = ? WHERE path = 'topics/related-target'",
            (_recent_timestamp(4),),
        )
    _backlog_project(store, project="label-project")

    calls = []

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        calls.append(request)
        if len(calls) == 1:
            return {"proposals": [_proposal(
                operation="create_page",
                path=request["living_page"],
                evidence_id=request["evidence"][0]["evidence_id"],
            )]}
        return {
            "proposals": [{
                "group": "main",
                "operation": "add_link",
                "path": request["living_page"],
                "target_path": "topics/related-target",
                "link_type": "related",
                "evidence_ids": [request["evidence"][0]["evidence_id"]],
                "reason": "The pages cover related concepts.",
                "risk_class": "low",
            }]
        }

    result = run_curator(store.database_path, chat=chat, projects=1)
    assert result["projects"][0]["status"] == "applied"

    with store.connection(write=True) as conn:
        conn.execute(
            "UPDATE pages SET updated_at = ? WHERE path = ?",
            (_recent_timestamp(3), "topics/label-project"),
        )

    _ingest(
        store,
        "label-project-second",
        "/Users/u/work/label-project",
        _recent_timestamp(1),
        [SessionEvent("user-2", "user", "another durable fact")],
    )

    result = run_curator(store.database_path, chat=chat, projects=1)
    assert result["projects"][0]["status"] == "applied"
    assert result["projects"][0]["counts"] == {"applied": 1}
    with store.connection() as conn:
        version = conn.execute(
            "SELECT created_by FROM page_versions "
            "WHERE version_id = (SELECT current_version_id FROM pages WHERE path = ?)",
            ("topics/label-project",),
        ).fetchone()
        link = conn.execute(
            "SELECT origin FROM links WHERE source_page_id = "
            "(SELECT page_id FROM pages WHERE path = ?) "
            "AND target_page_id = (SELECT page_id FROM pages WHERE path = ?)",
            ("topics/label-project", "topics/related-target"),
        ).fetchone()

    assert tuple(version) == ("local-curator",)
    assert tuple(link) == ("local-curator",)


def test_shrink_guard_leaves_page_for_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from uuid import UUID

    from wikibricks import mcp_server
    from wikibricks.curation import apply_run
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="guard-project", page=True)

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        return {"proposals": [_proposal(
            operation="update_page",
            path=request["living_page"],
            evidence_id=request["evidence"][0]["evidence_id"],
            summary="tiny",
            body="tiny",
        )]}

    result = run_curator(store.database_path, chat=chat, projects=1)
    project = result["projects"][0]
    assert project["status"] == "review_required"
    assert project["guarded"] == 1
    assert store.read_page("topics/guard-project")["version"] == 1

    monkeypatch.setenv("WIKIBRICKS_DATABASE_PATH", str(store.database_path))
    indexed = mcp_server.dispatch_tool(
        "wiki_index", {}, tools=mcp_server._build_tools()
    )
    review = [page for page in indexed if page["path"] == "_meta/curation-review"]
    assert len(review) == 1
    assert review[0]["items"] == [{
        "run_id": project["run_id"],
        "pending_patches": 1,
    }]

    applied = apply_run(store, UUID(project["run_id"]), policy="all")
    assert applied["counts"] == {"applied": 1}
    indexed = mcp_server.dispatch_tool(
        "wiki_index", {}, tools=mcp_server._build_tools()
    )
    assert not [page for page in indexed if page["path"] == "_meta/curation-review"]


def test_run_curator_reports_errors_and_processes_other_projects(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="failed-project")
    _backlog_project(store, project="passed-project")

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        if request["project"] == "failed-project":
            raise RuntimeError("model failed")
        return {"proposals": [_proposal(
            operation="create_page",
            path=request["living_page"],
            evidence_id=request["evidence"][0]["evidence_id"],
        )]}

    result = run_curator(store.database_path, chat=chat, projects=2)
    statuses = {item["project"]: item["status"] for item in result["projects"]}
    assert statuses == {"failed-project": "error", "passed-project": "applied"}
    assert result["errors"] == 1
    assert store.get_sync_cursor("curator:failed-project") == {}
    assert store.get_sync_cursor("curator:passed-project")


def test_run_curator_rejects_unknown_evidence(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="unknown-evidence")

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        return {"proposals": [_proposal(
            operation="create_page",
            path=request["living_page"],
            evidence_id="session-event:not-real",
        )]}

    result = run_curator(store.database_path, chat=chat, projects=1)
    assert result["errors"] == 1
    assert "unknown evidence" in result["projects"][0]["error"]
    assert store.read_page("topics/unknown-evidence") is None


def test_dry_run_reports_proposals_without_writes(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="dry-run")

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        return {"proposals": [_proposal(
            operation="create_page",
            path=request["living_page"],
            evidence_id=request["evidence"][0]["evidence_id"],
        )]}

    result = run_curator(store.database_path, chat=chat, projects=1, dry_run=True)
    project = result["projects"][0]
    assert project["status"] == "dry_run"
    assert project["proposals"] == [{
        "operation": "create_page",
        "path": "topics/dry-run",
        "title": "Dry-Run",
        "risk_class": "low",
        "reason": "The session evidence changed the project state.",
    }]
    assert store.read_page("topics/dry-run") is None
    with store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM curation_runs").fetchone()[0] == 0
    assert store.get_sync_cursor("curator:dry-run") == {}


def test_prompt_schema_and_policy_reject_forbidden_operations(tmp_path: Path):
    from wikibricks_curator.curator import run_curator
    from wikibricks_remote.resources import load_prompt, load_schema

    store = _store(tmp_path)
    _backlog_project(store, project="policy-project", page=True)
    calls: list[tuple[str, dict, dict]] = []

    def chat(system_prompt: str, request: dict, schema: dict) -> dict:
        calls.append((system_prompt, request, schema))
        return {"proposals": [{
            **_proposal(
                operation="supersede_page",
                path=request["living_page"],
                evidence_id=request["evidence"][0]["evidence_id"],
            ),
            "target_path": "topics/other",
        }]}

    result = run_curator(store.database_path, chat=chat, projects=1)
    prompt, _request, schema = calls[0]
    assert prompt.startswith(load_prompt() + "\n\n")
    assert schema == load_schema()
    assert "# Local nightly curation for one project" in prompt
    assert result["errors"] == 1
    assert "disabled by remote policy" in result["projects"][0]["error"]


def test_cli_propose_binds_gateway_and_reports_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from wikibricks_curator import cli

    database_path = tmp_path / "wikibricks.db"
    store = SQLiteStore(database_path)
    store.migrate()
    _backlog_project(store, project="cli-project")
    tokens: list[str | None] = []

    def resolve_token(profile: str | None) -> str:
        tokens.append(profile)
        return "test-token"

    def chat_json(_prompt, request, _schema, *, base_url, token, model):
        assert base_url == "https://gateway.example"
        assert token == "test-token"
        assert model == "system.ai.glm-5-3-flash"
        return {"proposals": [_proposal(
            operation="create_page",
            path=request["living_page"],
            evidence_id=request["evidence"][0]["evidence_id"],
        )]}

    monkeypatch.setattr(cli, "resolve_token", resolve_token)
    monkeypatch.setattr(cli, "chat_json", chat_json)
    code = cli.main([
        "propose",
        "--database-path",
        str(database_path),
        "--base-url",
        "https://gateway.example",
        "--projects",
        "1",
    ])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert tokens == [None]
    assert output["projects"][0]["project"] == "cli-project"
    assert store.read_page("topics/cli-project")

    _backlog_project(store, project="cli-error")

    def failing_chat_json(*_args, **_kwargs):
        raise RuntimeError("model failed")

    monkeypatch.setattr(cli, "chat_json", failing_chat_json)
    assert cli.main([
        "propose",
        "--database-path",
        str(database_path),
        "--base-url",
        "https://gateway.example",
        "--projects",
        "2",
        "--profile",
        "staging",
        "--no-apply",
    ]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["errors"] == 1


def test_store_manifest_is_idempotent(tmp_path: Path):
    from uuid import uuid4

    from wikibricks.curation import build_manifest, store_manifest
    from wikibricks_remote.proposals import build_patches
    from wikibricks_remote.resources import load_policy

    store = _store(tmp_path)
    patches = build_patches(
        {"proposals": [_proposal(
            operation="create_page",
            path="topics/new",
            evidence_id="session-event:test",
        )]},
        run_id=uuid4(),
        pages=[],
        evidence_ids={"session-event:test"},
        policy=load_policy(),
    )
    manifest = build_manifest(
        replica_id=uuid4(),
        input_watermark=0,
        patches=patches,
    )
    assert store_manifest(store, manifest) is True
    assert store_manifest(store, manifest) is False


def test_run_curator_uses_a_deterministic_run_id_for_the_same_input(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="stable-run")

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        return {"proposals": [_proposal(
            operation="create_page",
            path=request["living_page"],
            evidence_id=request["evidence"][0]["evidence_id"],
        )]}

    first = run_curator(store.database_path, chat=chat, projects=1, dry_run=True)
    second = run_curator(store.database_path, chat=chat, projects=1, dry_run=True)
    assert first["projects"][0]["run_id"] == second["projects"][0]["run_id"]


def test_local_curator_resource_is_packaged():
    resource = files("wikibricks_curator").joinpath("resources", "local-curator.md")
    assert resource.read_text(encoding="utf-8").startswith(
        "# Local nightly curation for one project"
    )


def test_dry_run_without_changes_does_not_advance_the_cursor(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="quiet")

    result = run_curator(
        store.database_path, chat=lambda *_: {"proposals": []}, projects=1, dry_run=True
    )

    assert result["projects"][0]["status"] == "no_changes"
    assert store.get_sync_cursor("curator:quiet") == {}


def test_shrink_guard_handles_pages_without_a_summary():
    from wikibricks_curator.curator import _guard_shrinking_updates

    raw = {"proposals": [{"operation": "update_page", "path": "topics/x", "summary": "", "body": "x"}]}
    pages = [{"path": "topics/x", "content": {"body": "long body " * 20}}]

    assert _guard_shrinking_updates(raw, pages) == 1
    assert raw["proposals"][0]["risk_class"] == "high"


def test_chat_json_passes_timeout_by_keyword_and_asks_for_low_reasoning():
    # urllib.request.urlopen(url, data, timeout): a positional timeout becomes the body.
    from wikibricks_curator.gateway import chat_json

    seen: dict = {}

    def keyword_only_opener(request: Request, *, timeout: float) -> _Response:
        seen["timeout"] = timeout
        seen["payload"] = _payload(request)
        return _Response(json.dumps({"choices": [{"message": {"content": '{"proposals": []}'}}]}))

    result = chat_json(
        "prompt", {"a": 1}, {"type": "object"},
        base_url="https://gateway.example/v1", token="t", model="m", timeout=42,
        opener=keyword_only_opener,
    )

    assert result == {"proposals": []}
    assert seen["timeout"] == 42
    # GLM 5.3 Flash spends the whole output budget on reasoning without this.
    assert seen["payload"]["reasoning_effort"] == "low"


def test_links_from_a_page_created_in_the_same_run_are_dropped_not_fatal(tmp_path: Path):
    # Real GLM output: create the living page and link it in one reply. build_patches only
    # knows existing pages, so the link must not cost the page.
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    store.write_page("topics/other", "Other", {"summary": "s", "body": "Existing page."})
    _backlog_project(store, project="fresh")

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        evidence_id = request["evidence"][0]["evidence_id"]
        link = _proposal(operation="add_link", path=request["living_page"], evidence_id=evidence_id)
        link.update({"target_path": "topics/other", "link_type": "related", "title": "",
                     "page_type": "entity", "summary": "", "body": ""})
        return {"proposals": [
            _proposal(operation="create_page", path=request["living_page"], evidence_id=evidence_id),
            link,
        ]}

    result = run_curator(store.database_path, chat=chat, projects=1)
    project = result["projects"][0]

    assert project["status"] == "applied", project["error"]
    assert project["dropped_links"] == 1
    assert store.read_page("topics/fresh") is not None


def test_neutral_defaults_fill_fields_models_omit(tmp_path: Path):
    # Real GLM output omitted risk_class, and links rarely carry title/body/tags.
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="omits", page=True)
    store.write_page("topics/other", "Other", {"summary": "s", "body": "Existing page."})

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        evidence_id = request["evidence"][0]["evidence_id"]
        update = _proposal(operation="update_page", path=request["living_page"], evidence_id=evidence_id,
                           body="The project page contains durable facts and one more fact.")
        del update["risk_class"], update["tags"], update["group"]
        update["confidence"] = 0.9
        link = {"operation": "add_link", "path": request["living_page"], "target_path": "topics/other",
                "link_type": "related", "evidence_ids": [evidence_id], "reason": "Builds on it."}
        return {"proposals": [update, link]}

    project = run_curator(store.database_path, chat=chat, projects=1)["projects"][0]

    assert project["status"] == "applied", project["error"]
    assert "one more fact" in store.read_page("topics/omits")["content_text"]


def test_page_proposals_without_content_still_fail(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="empty")

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        proposal = _proposal(operation="create_page", path=request["living_page"],
                             evidence_id=request["evidence"][0]["evidence_id"])
        del proposal["body"]
        return {"proposals": [proposal]}

    assert run_curator(store.database_path, chat=chat, projects=1)["projects"][0]["status"] == "error"


def test_one_retry_after_an_invalid_model_reply(tmp_path: Path):
    from wikibricks_curator.curator import run_curator

    store = _store(tmp_path)
    _backlog_project(store, project="flaky")
    replies = [RuntimeError("curation model output could not be decoded"), None]

    def chat(_prompt: str, request: dict, _schema: dict) -> dict:
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"proposals": [_proposal(operation="create_page", path=request["living_page"],
                                        evidence_id=request["evidence"][0]["evidence_id"])]}

    project = run_curator(store.database_path, chat=chat, projects=1)["projects"][0]

    assert project["status"] == "applied", project["error"]
    assert project["attempts"] == 2
