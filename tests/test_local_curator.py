from __future__ import annotations

import json
import subprocess
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
