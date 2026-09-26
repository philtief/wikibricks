"""End-to-end contract tests for Omnigent server import and capture staleness."""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest

from wikibricks import cli, mcp_server
from wikibricks.adapters.omnigent_export import export_to_session
from wikibricks.config import load_config
from wikibricks.maintenance import check_database
from wikibricks.omnigent_server import list_sessions
from wikibricks.storage.sqlite_store import SQLiteStore


def _lines(session_id: str, updated_at: int, *, user_text: str = "hello") -> list[str]:
    meta = {
        "record_type": "session_meta",
        "id": session_id,
        "harness": "claude-native",
        "workspace": "/tmp/project",
        "created_at": updated_at - 10,
        "updated_at": updated_at,
        "title": f"Session {session_id}",
        "kind": "default",
        "sub_agent_name": None,
        "parent_session_id": None,
        "archived": False,
        "agent_name": "Claude",
    }
    user = {
        "record_type": "item",
        "id": f"{session_id}-user",
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": user_text}],
        "created_at": updated_at - 5,
    }
    assistant = {
        "record_type": "item",
        "id": f"{session_id}-assistant",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Hi"}],
        "created_at": updated_at,
    }
    return [json.dumps(value) for value in (meta, user, assistant)]


def _sample_lines() -> list[str]:
    return [
        json.dumps(
            {
                "record_type": "session_meta",
                "id": "2567687242045784",
                "harness": "claude-native",
                "workspace": "/Users/x/project",
                "created_at": 1790447502,
                "updated_at": 1790447524,
                "title": "Model name inquiry",
                "sub_agent_name": None,
                "parent_session_id": None,
                "archived": False,
                "agent_name": "Claude",
            }
        ),
        json.dumps(
            {
                "record_type": "item",
                "id": "resource",
                "type": "resource_event",
                "event_type": "session.resource.created",
                "created_at": 1790447506,
                "resource_type": "terminal",
            }
        ),
        json.dumps(
            {
                "record_type": "item",
                "id": "user",
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "what is your exact model name"}],
                "created_at": 1790447516,
            }
        ),
        json.dumps(
            {
                "record_type": "item",
                "id": "assistant",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I am Claude"}],
                "created_at": 1790447524,
            }
        ),
        json.dumps(
            {
                "record_type": "item",
                "id": "call",
                "type": "function_call",
                "name": "Bash",
                "call_id": "c1",
                "arguments": '{"command":"ls"}',
                "created_at": 1790447530,
            }
        ),
        json.dumps(
            {
                "record_type": "item",
                "id": "result",
                "type": "function_call_output",
                "call_id": "c1",
                "output": "README.md",
                "created_at": 1790447531,
            }
        ),
        json.dumps(
            {
                "record_type": "item",
                "id": "reasoning",
                "type": "reasoning",
                "content": [{"type": "reasoning_text", "text": "private"}],
                "created_at": 1790447524,
            }
        ),
    ]


def test_export_to_session_contract() -> None:
    record = export_to_session(_sample_lines(), user_id="user", server="https://example.invalid")

    assert record is not None
    assert record.harness == "omnigent"
    assert record.external_id == "2567687242045784"
    assert record.agent == "Claude"
    assert record.workspace == "/Users/x/project"
    assert record.started_at == "2026-09-26T18:31:42+00:00"
    assert record.updated_at == "2026-09-26T18:32:04+00:00"
    assert record.metadata == {
        "title": "Model name inquiry",
        "omnigent_harness": "claude-native",
        "omnigent_server": "https://example.invalid",
    }
    assert [event.kind for event in record.events] == [
        "lifecycle",
        "user",
        "assistant",
        "tool_call",
        "tool_result",
    ]
    assert record.events[3].metadata == {"tool_name": "Bash", "call_id": "c1"}


@pytest.mark.parametrize(
    "field",
    ["sub_agent_name", "parent_session_id"],
)
def test_export_skips_sub_agents_and_archived(field: str) -> None:
    lines = _lines("skip", 100)
    meta = json.loads(lines[0])
    meta[field] = "parent" if field == "parent_session_id" else "child"
    meta["archived"] = field == "sub_agent_name"
    lines[0] = json.dumps(meta)

    assert export_to_session(lines, user_id="user", server="server") is None


def test_export_skips_without_user_message() -> None:
    lines = _lines("assistant-only", 100, user_text="")

    assert export_to_session(lines, user_id="user", server="server") is None


class _ListServer(BaseHTTPRequestHandler):
    responses: list[dict[str, Any]] = []
    authorization: str | None = None

    def do_GET(self) -> None:
        _ListServer.authorization = self.headers.get("Authorization")
        after = "second" if "after=first" in self.path else ""
        body = json.dumps(self.responses[1] if after == "second" else self.responses[0]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None


def test_list_sessions_paginates() -> None:
    server = HTTPServer(("127.0.0.1", 0), _ListServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _ListServer.responses = [
        {"object": "list", "data": [{"id": "first"}], "last_id": "first", "has_more": True},
        {"object": "list", "data": [{"id": "second"}], "last_id": "second", "has_more": False},
    ]
    from wikibricks.omnigent_server import list_sessions

    try:
        result = list_sessions(f"http://127.0.0.1:{server.server_port}", "secret")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert result == [{"id": "first"}, {"id": "second"}]
    assert _ListServer.authorization == "Bearer secret"


def test_cli_import_is_idempotent_and_advances_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "wikibricks.db"
    sessions = [
        {"id": "a", "updated_at": 1000},
        {"id": "b", "updated_at": 1100},
    ]

    page = {
        "object": "list",
        "data": sessions,
        "last_id": "b",
        "has_more": False,
    }
    server = HTTPServer(("127.0.0.1", 0), _ListServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server_url = f"http://127.0.0.1:{server.server_port}"
    _ListServer.responses = [page]

    def fake_export(server_name: str, session_id: str, *, runner: Any = None) -> list[str]:
        lines = _lines(
            session_id,
            next(item["updated_at"] for item in sessions if item["id"] == session_id),
        )

        def fake_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output = Path(args[args.index("-o") + 1])
            output.write_text("\n".join(lines), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0)

        from wikibricks import omnigent_server

        return omnigent_server.export_session(server_name, session_id, runner=fake_runner)

    monkeypatch.setattr(
        cli,
        "list_sessions",
        lambda name, token, *, opener=None: list_sessions(name, token, opener=opener or urlopen),
    )
    monkeypatch.setattr(cli, "export_session", fake_export)
    monkeypatch.setenv("WIKIBRICKS_OMNIGENT_TOKEN", "token")

    parser = cli.build_parser(load_config(home=tmp_path, environ={}))
    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(["import", "omnigent-server", "--help"])
    assert exit_info.value.code == 0

    try:
        first = _run_capture(database, 2, 0, server=server_url)
        store = SQLiteStore(database)
        assert store.get_sync_cursor(f"omnigent-server:{server_url}") == {
            "id": "b",
            "updated_at": 1100,
        }
        sessions[1]["updated_at"] = 1101
        third = _run_capture(database, 1, 0, expected_scanned=1, server=server_url)
        assert store.get_sync_cursor(f"omnigent-server:{server_url}") == {
            "id": "b",
            "updated_at": 1101,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert third["imported"] == 1
    assert first["imported"] == 2


def _run_capture(
    database: Path,
    expected_imported: int,
    expected_errors: int,
    *,
    expected_scanned: int = 2,
    server: str = "https://example.invalid",
) -> dict[str, Any]:
    result = cli.import_omnigent_server(
        database_url=database,
        server=server,
        profile="unused",
        user_id="user",
    )
    assert result == {
        "scanned": expected_scanned,
        "imported": expected_imported,
        "skipped": 0,
        "errors": expected_errors,
    }
    return result


def test_cli_export_failure_preserves_cursor(tmp_path: Path) -> None:
    monkeypatch = pytest.MonkeyPatch()
    database = tmp_path / "failure.db"
    sessions = [{"id": "a", "updated_at": 1000}, {"id": "b", "updated_at": 1100}]

    monkeypatch.setattr(cli, "list_sessions", lambda *args, **kwargs: list(sessions))
    monkeypatch.setattr(
        cli,
        "export_session",
        lambda server, session_id, *, runner=None: (
            _lines("a", 1000)
            if session_id == "a"
            else (_ for _ in ()).throw(RuntimeError("export failed"))
        ),
    )
    monkeypatch.setattr(cli, "resolve_token", lambda profile: "token")
    cli.import_omnigent_server(
        database_url=database,
        server="https://example.invalid",
        profile="unused",
        user_id="user",
    )

    cursor = SQLiteStore(database).get_sync_cursor("omnigent-server:https://example.invalid")
    assert cursor == {"id": "a", "updated_at": 1000}
    monkeypatch.setattr(
        cli,
        "export_session",
        lambda server, session_id, *, runner=None: _lines(
            session_id, next(item["updated_at"] for item in sessions if item["id"] == session_id)
        ),
    )

    result = cli.import_omnigent_server(
        database_url=database,
        server="https://example.invalid",
        profile="unused",
        user_id="user",
    )

    assert result == {"scanned": 1, "imported": 1, "skipped": 0, "errors": 0}
    assert SQLiteStore(database).get_sync_cursor("omnigent-server:https://example.invalid") == {
        "id": "b",
        "updated_at": 1100,
    }


def test_capture_staleness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "capture.db"
    store = SQLiteStore(database)
    store.migrate()
    monkeypatch.setenv("WIKIBRICKS_DATABASE_PATH", str(database))

    def index_warnings() -> list[dict[str, Any]]:
        result = mcp_server.dispatch_tool("wiki_index", {}, tools=mcp_server._build_tools())
        return [item for item in result if item.get("page_type") == "warning"]

    assert check_database(database)["capture_stale"] is True
    record = export_to_session(_lines("fresh", 1000), user_id="user", server="https://s")
    assert record is not None
    store.ingest_session(record)

    report = check_database(database)
    assert report["ok"] is True
    assert report["capture_stale"] is False
    assert report["capture"]["age_hours"] < 1
    assert index_warnings() == []

    with store.connection(write=True) as connection:
        connection.execute("UPDATE sessions SET updated_at = datetime('now', '-3 days')")

    report = check_database(database)
    assert report["ok"] is True
    assert report["capture_stale"] is True
    assert index_warnings()[0]["path"] == "_meta/capture-status"


def test_cli_import_includes_sessions_updated_in_the_cursor_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "same-second.db"
    listed = [{"id": "a", "updated_at": 500}]
    monkeypatch.setattr(cli, "resolve_token", lambda profile: "token")
    monkeypatch.setattr(cli, "list_sessions", lambda *args, **kwargs: list(listed))
    monkeypatch.setattr(
        cli,
        "export_session",
        lambda server, session_id, **kwargs: _lines(session_id, 500),
    )

    def run() -> dict[str, int]:
        return cli.import_omnigent_server(
            database_url=database, server="https://s", profile="p", user_id="user"
        )

    assert run()["imported"] == 1
    listed.append({"id": "b", "updated_at": 500})
    assert run()["imported"] == 1
    assert run()["scanned"] == 0
