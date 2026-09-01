from __future__ import annotations

import json

import pytest

from wikibricks.adapters.claude_code import state_to_session
from wikibricks.adapters.jsonl import iter_jsonl_sessions
from wikibricks.adapters.omnigent import conversation_to_session
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.session_ingest import (
    session_content_hash,
    session_identity,
    session_page_path,
    session_tags,
)


def _record(**overrides) -> SessionRecord:
    values = {
        "harness": "omnigent",
        "external_id": "conversation-123",
        "user_id": "philipp-at-example.com",
        "agent": "codex-native-ui",
        "workspace": "/tmp/project",
        "started_at": "2026-08-30T10:00:00+00:00",
        "updated_at": "2026-08-30T11:00:00+00:00",
        "events": [
            SessionEvent(external_id="0", kind="user", content="How does TOAST work?"),
            SessionEvent(external_id="1", kind="assistant", content="PostgreSQL stores large text out of line."),
        ],
        "metadata": {"z": 1, "a": {"second": 2, "first": 1}},
    }
    values.update(overrides)
    return SessionRecord(**values)


def test_session_identity_is_stable_and_not_path_derived():
    first = _record(workspace="/tmp/one")
    moved = _record(workspace="/tmp/two")

    assert session_identity(first) == session_identity(moved)
    assert str(session_identity(first)) == "4631c7f2-47d7-58c3-a5a3-006657dc8c5c"


def test_content_hash_is_canonical_for_metadata_key_order():
    first = _record(metadata={"z": 1, "a": {"second": 2, "first": 1}})
    reordered = _record(metadata={"a": {"first": 1, "second": 2}, "z": 1})

    assert session_content_hash(first) == session_content_hash(reordered)
    assert len(session_content_hash(first)) == 64


def test_record_rejects_duplicate_event_ids():
    duplicate = SessionEvent(external_id="same", kind="user", content="two")

    with pytest.raises(ValueError, match="duplicate event external_id"):
        _record(events=[SessionEvent(external_id="same", kind="user", content="one"), duplicate])


@pytest.mark.parametrize("kind", ["prompt", "chat", "unknown"])
def test_event_rejects_non_contract_kinds(kind: str):
    with pytest.raises(ValueError, match="unsupported session event kind"):
        SessionEvent(external_id="1", kind=kind, content="x")


def test_page_paths_preserve_existing_claude_and_omnigent_prefixes():
    claude = _record(harness="claude-code", external_id="session-1")
    omnigent = _record(harness="omnigent", external_id="conversation-1")

    assert session_page_path(claude) == "sessions/philipp-at-example.com/2026/08/30/session-1"
    assert session_page_path(omnigent) == "omnigent-sessions/philipp-at-example.com/2026/08/30/conversation-1"


def test_jsonl_contract_loads_a_version_one_session():
    payload = {
        "schema_version": 1,
        "session": {
            "harness": "custom-cli",
            "external_id": "s-1",
            "user_id": "u",
            "agent": "codex",
            "events": [
                {"external_id": "e-1", "kind": "user", "content": "hello", "metadata": {}}
            ],
            "metadata": {"source": "fixture"},
        },
    }

    records = list(iter_jsonl_sessions([json.dumps(payload)]))

    assert records == [
        SessionRecord(
            harness="custom-cli",
            external_id="s-1",
            user_id="u",
            agent="codex",
            events=[SessionEvent(external_id="e-1", kind="user", content="hello")],
            metadata={"source": "fixture"},
        )
    ]


def test_jsonl_contract_rejects_unsupported_versions():
    payload = json.dumps({"schema_version": 2, "session": {}})

    with pytest.raises(ValueError, match="unsupported WikiBricks session schema version: 2"):
        list(iter_jsonl_sessions([payload]))


def test_claude_adapter_normalizes_recorder_state():
    record = state_to_session(
        {
            "session_id": "claude-1",
            "started_at": "2026-08-30T10:00:00Z",
            "cwd": "/tmp/project",
            "model": "claude-opus",
            "events": [
                {"kind": "prompt", "prompt": "question"},
                {"kind": "tool", "tool_name": "Read", "summary": "src/app.py"},
                {"kind": "response", "summary": "answer"},
            ],
        },
        user_id="u",
    )

    assert record.harness == "claude-code"
    assert [event.kind for event in record.events] == ["user", "tool_call", "assistant"]
    assert record.events[1].metadata["tool_name"] == "Read"


def test_omnigent_adapter_preserves_codex_agent_and_tool_result():
    record = conversation_to_session(
        {
            "conversation_id": "omni-1",
            "created_at": 1788084000,
            "updated_at": 1788087600,
            "agent_name": "codex-native-ui",
            "workspace": "/tmp/project",
            "items": [
                (1, {"role": "user", "content": [{"text": "question"}]}),
                (2, {"name": "shell", "arguments": {"cmd": "pwd"}, "call_id": "call-1"}),
                (3, {"call_id": "call-1", "output": "/tmp/project"}),
                (1, {"role": "assistant", "content": "answer"}),
            ],
        },
        user_id="u",
    )

    assert record.agent == "codex-native-ui"
    assert [event.kind for event in record.events] == [
        "user",
        "tool_call",
        "tool_result",
        "assistant",
    ]
    assert record.events[2].content == "/tmp/project"
    assert session_tags(record) == ["session", "harness:omnigent", "agent:codex-native-ui", "user:u"]
