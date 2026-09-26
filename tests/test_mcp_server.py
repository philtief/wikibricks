from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import types
from mcp.server import Server

from wikibricks.mcp_server import _build_tools, _serve, dispatch_tool, format_tool_response


def test_dispatch_tool_uses_injected_tools():
    def write(path: str, title: str, summary: str, body: str) -> dict[str, str]:
        return {"path": path, "title": title, "summary": summary, "body": body}

    tools = {"wiki_write_page": write}
    result = dispatch_tool(
        "wiki_write_page",
        {
            "path": "topics/mcp",
            "title": "MCP",
            "summary": "Contract",
            "body": "body",
        },
        tools,
    )

    assert result == {
        "path": "topics/mcp",
        "title": "MCP",
        "summary": "Contract",
        "body": "body",
    }


def test_dispatch_tool_rejects_unknown_tools():
    with pytest.raises(ValueError, match="Unknown tool: wiki_unknown"):
        dispatch_tool("wiki_unknown", {}, {"wiki_search": lambda query: []})


def test_format_tool_response_serializes_and_preserves_errors():
    class Timestamp:
        def isoformat(self) -> str:
            return "2026-01-01T00:00:00+00:00"

    result = json.loads(
        format_tool_response(
            "wiki_search",
            {"query": "needle"},
            {"wiki_search": lambda query: [{"when": Timestamp()}]},
        )
    )
    error = json.loads(
        format_tool_response(
            "wiki_search",
            {"query": "needle"},
            {"wiki_search": lambda query: 1 / 0},
        )
    )

    assert result == [{"when": "2026-01-01T00:00:00+00:00"}]
    assert error == {"error": "division by zero"}


def test_build_tools_binds_all_local_backends(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_DATABASE_PATH", str(tmp_path / "wikibricks.db"))

    tools = _build_tools()
    write = tools["wiki_write_page"](
        path="topics/local",
        title="Local",
        summary="Bound to local SQLite",
        body="Local backend marker",
    )
    promoted = tools["wiki_promote_answer"](
        question="Where is memory stored?",
        answer="In local SQLite.",
        source_paths=["topics/local"],
    )

    assert write == {"path": "topics/local", "status": "ok"}
    assert promoted == {"path": promoted["path"], "cited": 1}
    assert tools["wiki_search"]("local backend marker")[0]["path"] == "topics/local"
    assert tools["wiki_read_full"]("topics/local")["content"]["body"] == (
        "Local backend marker"
    )
    indexed = tools["wiki_index"]()
    assert "topics/local" in [page["path"] for page in indexed]
    assert indexed[-1]["path"] == "_meta/capture-status"
    assert next(page for page in indexed if page["path"] == "topics/local")[
        "page_type"
    ] == "concept"
    assert indexed[-1]["page_type"] == "warning"
    assert "memory capture has stopped" in indexed[-1]["summary"]


def test_serve_logs_database_initialization_failures(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setenv("WIKIBRICKS_DATABASE_PATH", str(tmp_path))
    monkeypatch.setenv("WIKIBRICKS_AUTOMATION_ENABLED", "false")
    servers: list[Server] = []

    class RecordingServer(Server):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            servers.append(self)

        async def run(self, read_stream, write_stream, initialization_options, **_):
            self.run_streams = (read_stream, write_stream)

    @asynccontextmanager
    async def fake_stdio():
        yield ("read-stream", "write-stream")

    monkeypatch.setattr("mcp.server.Server", RecordingServer)
    monkeypatch.setattr("mcp.server.stdio.stdio_server", fake_stdio)

    async def exercise():
        await _serve()

    asyncio.run(exercise())

    assert servers[0].run_streams == ("read-stream", "write-stream")
    assert "WikiBricks local database initialization failed" in caplog.text


def test_stdio_handlers_cover_tool_dispatch_and_validation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_DATABASE_PATH", str(tmp_path / "wikibricks.db"))
    monkeypatch.setenv("WIKIBRICKS_AUTOMATION_ENABLED", "false")

    servers: list[Server] = []

    class RecordingServer(Server):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            servers.append(self)

        async def run(self, read_stream, write_stream, initialization_options, **_):
            self.run_streams = (read_stream, write_stream)
            self.initialization_options = initialization_options

    @asynccontextmanager
    async def fake_stdio():
        yield ("read-stream", "write-stream")

    monkeypatch.setattr("mcp.server.Server", RecordingServer)
    monkeypatch.setattr("mcp.server.stdio.stdio_server", fake_stdio)

    async def exercise():
        await _serve()
        server = servers[0]
        assert server.name == "wikibricks"
        assert server.run_streams == ("read-stream", "write-stream")

        listed = await server.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest()
        )
        assert [tool.name for tool in listed.root.tools] == [
            "wiki_search",
            "wiki_read_full",
            "wiki_index",
            "wiki_write_page",
            "wiki_promote_answer",
        ]

        call_handler = server.request_handlers[types.CallToolRequest]
        params = types.CallToolRequestParams(
            name="wiki_search",
            arguments={"query": "anything"},
        )
        success = await call_handler(types.CallToolRequest(params=params))
        assert success.root.isError is False
        assert json.loads(success.root.content[0].text) == []

        missing = await call_handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(name="wiki_search", arguments={})
            )
        )
        assert missing.root.isError is True
        assert "query" in missing.root.content[0].text

        unknown = await call_handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(name="wiki_unknown", arguments={})
            )
        )
        assert unknown.root.isError is False
        assert "Unknown tool: wiki_unknown" in unknown.root.content[0].text

        malformed_request = types.CallToolRequest.model_construct(
            params=types.CallToolRequestParams.model_construct(
                name="wiki_search",
                arguments="not-an-object",
            )
        )
        malformed = await call_handler(malformed_request)
        assert malformed.root.isError is True
        assert "Input validation error" in malformed.root.content[0].text

    asyncio.run(exercise())
