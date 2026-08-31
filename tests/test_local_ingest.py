from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from wikibricks.adapters import claude_code_buffer, claude_code_hook
from wikibricks.cli import import_jsonl, import_omnigent
from wikibricks.postgres_store import PostgresStore


def _omnigent_db(path: Path) -> str:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE conversations (id BLOB, created_at INT, updated_at INT, "
        "title TEXT, archived INT, agent_id BLOB, workspace_id INT)"
    )
    connection.execute("CREATE TABLE agents (id BLOB, name TEXT, workspace_id INT)")
    connection.execute(
        "CREATE TABLE conversation_items (conversation_id BLOB, workspace_id INT, "
        "position INT, type INT, data TEXT)"
    )
    conversation_id = bytes.fromhex("aa" * 16)
    agent_id = bytes.fromhex("bb" * 16)
    connection.execute(
        "INSERT INTO agents VALUES (?, ?, ?)", (agent_id, "codex-native-ui", 0)
    )
    connection.execute(
        "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, 1788084000, 1788087600, "Design local memory", 0, agent_id, 0),
    )
    rows = [
        (1, {"role": "user", "content": [{"text": "Use PostgreSQL locally"}]}),
        (2, {"name": "shell", "arguments": {"cmd": "psql --version"}, "call_id": "c1"}),
        (3, {"output": "psql 16", "call_id": "c1"}),
        (1, {"role": "assistant", "content": "Done"}),
    ]
    for position, (item_type, data) in enumerate(rows):
        connection.execute(
            "INSERT INTO conversation_items VALUES (?, ?, ?, ?, ?)",
            (conversation_id, 0, position, item_type, json.dumps(data)),
        )
    connection.commit()
    connection.close()
    return "aa" * 16


def test_omnigent_import_is_read_only_resumable_and_keeps_codex_metadata(
    postgres_url: str,
    tmp_path: Path,
):
    chat_db = tmp_path / "chat.db"
    conversation_id = _omnigent_db(chat_db)

    first = import_omnigent(
        database_url=postgres_url,
        db_path=chat_db,
        user_id="u",
    )
    second = import_omnigent(
        database_url=postgres_url,
        db_path=chat_db,
        user_id="u",
    )

    store = PostgresStore(postgres_url)
    page = store.read_page(f"omnigent-sessions/u/2026/08/30/{conversation_id}")
    assert first == {"scanned": 1, "imported": 1, "skipped": 0, "errors": 0}
    assert second == {"scanned": 0, "imported": 0, "skipped": 0, "errors": 0}
    assert "agent:codex-native-ui" in page["tags"]
    assert [event["kind"] for event in page["events"]] == [
        "user",
        "tool_call",
        "tool_result",
        "assistant",
    ]
    with sqlite3.connect(chat_db) as connection:
        assert connection.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1


def test_jsonl_import_reports_bad_records_without_aborting_batch(
    postgres_url: str,
    tmp_path: Path,
):
    source = tmp_path / "sessions.jsonl"
    source.write_text(
        "not-json\n"
        + json.dumps(
            {
                "schema_version": 1,
                "session": {
                    "harness": "other",
                    "external_id": "ok",
                    "user_id": "u",
                    "events": [{"external_id": "0", "kind": "user", "content": "hello"}],
                },
            }
        )
        + "\n"
    )

    result = import_jsonl(database_url=postgres_url, source=source)

    assert result == {"scanned": 2, "imported": 1, "errors": 1}
    assert PostgresStore(postgres_url).read_page("other-sessions/u/1970/01/01/ok") is not None


def test_claude_hook_flushes_normalized_session_to_local_postgres(
    postgres_url: str,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("WIKIBRICKS_DATABASE_URL", postgres_url)
    monkeypatch.setenv("WIKIBRICKS_RECORDER_DIR", str(tmp_path / "recorder"))
    monkeypatch.setenv("WIKIBRICKS_USER_ID", "u")
    monkeypatch.setenv("WIKIBRICKS_RECORDER_MIN_EVENTS", "0")
    state = {
        "session_id": "claude-local",
        "cwd": "/Users/u/project",
        "events": [
            {"kind": "prompt", "prompt": "Keep this local", "ts": "2026-08-30T10:00:00Z"},
            {"kind": "tool", "tool_name": "Read", "tool_input": {"path": "README.md"}},
            {"kind": "tool_result", "tool_name": "Read", "output": "contents"},
        ],
        "first_prompt": "Keep this local",
        "started_at": "2026-08-30T10:00:00Z",
        "model": "claude-opus",
    }
    claude_code_buffer.save(state)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "claude-local"})))

    claude_code_hook.on_stop()

    page = PostgresStore(postgres_url).read_page(
        "sessions/u/2026/08/30/claude-local"
    )
    assert page is not None
    assert [event["kind"] for event in page["events"]] == [
        "user",
        "tool_call",
        "tool_result",
    ]


def test_claude_hook_skips_temporary_sessions(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_RECORDER_MIN_EVENTS", "0")
    for cwd in ("/tmp", "/private/tmp/job", "/var/tmp/tool"):
        state = {
            "session_id": "utility",
            "cwd": cwd,
            "events": [{"kind": "prompt", "prompt": "x"}],
        }
        assert claude_code_hook.should_skip(state) is True


def test_claude_hook_skips_single_system_prompt(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_RECORDER_MIN_EVENTS", "1")
    state = {
        "session_id": "utility",
        "cwd": "/Users/u/project",
        "first_prompt": "You are summarizing a transcript",
        "events": [
            {
                "kind": "prompt",
                "prompt": "You are summarizing a transcript",
            }
        ],
    }
    assert claude_code_hook.should_skip(state) is True


def test_claude_buffer_recovers_from_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_RECORDER_DIR", str(tmp_path))
    path = claude_code_buffer.state_path("broken")
    path.parent.mkdir(parents=True)
    path.write_text("not-json")

    assert claude_code_buffer.load("broken")["events"] == []
