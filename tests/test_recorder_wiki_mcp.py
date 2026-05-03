"""Tests for wiki_mcp — unified stdio MCP server (3 reads + 2 writes).

The MCP runtime (stdio loop, JSON-RPC) is not exercised here; we test the
pure pieces — schema definitions, tool dispatch, env-var config.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

EXPECTED_TOOL_NAMES = {
    "wiki_search",
    "wiki_read_full",
    "wiki_index",
    "wiki_write_page",
    "wiki_promote_answer",
}


def _full_tools_dict() -> dict:
    """Mocked tools dict with a MagicMock for every expected tool."""
    return {name: MagicMock() for name in EXPECTED_TOOL_NAMES}


# ---------------------------------------------------------------------------
# tool schemas — what Claude Code sees in tools/list
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def test_returns_five_tools(self):
        from wikibricks_recorder.wiki_mcp import get_tool_schemas
        names = {t["name"] for t in get_tool_schemas()}
        assert names == EXPECTED_TOOL_NAMES

    def test_each_tool_has_input_schema(self):
        from wikibricks_recorder.wiki_mcp import get_tool_schemas
        for tool in get_tool_schemas():
            assert tool["inputSchema"]["type"] == "object"
            assert "properties" in tool["inputSchema"]

    def test_each_tool_has_nonempty_description(self):
        from wikibricks_recorder.wiki_mcp import get_tool_schemas
        for tool in get_tool_schemas():
            assert tool["description"]

    def test_search_required_fields(self):
        from wikibricks_recorder.wiki_mcp import get_tool_schemas
        tool = next(t for t in get_tool_schemas() if t["name"] == "wiki_search")
        required = set(tool["inputSchema"].get("required", []))
        assert "query" in required

    def test_read_full_required_fields(self):
        from wikibricks_recorder.wiki_mcp import get_tool_schemas
        tool = next(t for t in get_tool_schemas() if t["name"] == "wiki_read_full")
        required = set(tool["inputSchema"].get("required", []))
        assert "path" in required

    def test_index_has_no_required_fields(self):
        from wikibricks_recorder.wiki_mcp import get_tool_schemas
        tool = next(t for t in get_tool_schemas() if t["name"] == "wiki_index")
        assert tool["inputSchema"].get("required", []) == []

    def test_write_page_required_fields(self):
        from wikibricks_recorder.wiki_mcp import get_tool_schemas
        tool = next(t for t in get_tool_schemas() if t["name"] == "wiki_write_page")
        required = set(tool["inputSchema"].get("required", []))
        assert {"path", "title", "summary", "body"} <= required

    def test_promote_answer_required_fields(self):
        from wikibricks_recorder.wiki_mcp import get_tool_schemas
        tool = next(t for t in get_tool_schemas() if t["name"] == "wiki_promote_answer")
        required = set(tool["inputSchema"].get("required", []))
        assert {"question", "answer"} <= required


# ---------------------------------------------------------------------------
# dispatch — name + args -> underlying callable result
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_search_invokes_with_kwargs(self):
        from wikibricks_recorder.wiki_mcp import dispatch_tool
        fake = MagicMock(return_value=[{"path": "x"}])
        tools = _full_tools_dict()
        tools["wiki_search"] = fake
        result = dispatch_tool("wiki_search", {"query": "delta", "k": 3}, tools=tools)
        fake.assert_called_once_with(query="delta", k=3)
        assert result == [{"path": "x"}]

    def test_read_full_invokes_with_path(self):
        from wikibricks_recorder.wiki_mcp import dispatch_tool
        fake = MagicMock(return_value={"path": "topics/x", "title": "X"})
        tools = _full_tools_dict()
        tools["wiki_read_full"] = fake
        result = dispatch_tool("wiki_read_full", {"path": "topics/x"}, tools=tools)
        fake.assert_called_once_with(path="topics/x")
        assert result["title"] == "X"

    def test_index_invokes_no_args(self):
        from wikibricks_recorder.wiki_mcp import dispatch_tool
        fake = MagicMock(return_value=[{"path": "a"}, {"path": "b"}])
        tools = _full_tools_dict()
        tools["wiki_index"] = fake
        result = dispatch_tool("wiki_index", {}, tools=tools)
        fake.assert_called_once_with()
        assert len(result) == 2

    def test_index_invokes_with_prefix(self):
        from wikibricks_recorder.wiki_mcp import dispatch_tool
        fake = MagicMock(return_value=[{"path": "topics/x"}])
        tools = _full_tools_dict()
        tools["wiki_index"] = fake
        result = dispatch_tool("wiki_index", {"prefix": "topics/"}, tools=tools)
        fake.assert_called_once_with(prefix="topics/")
        assert result[0]["path"] == "topics/x"

    def test_write_page_invokes_with_kwargs(self):
        from wikibricks_recorder.wiki_mcp import dispatch_tool
        fake = MagicMock(return_value={"path": "topics/x", "status": "ok"})
        tools = _full_tools_dict()
        tools["wiki_write_page"] = fake
        result = dispatch_tool(
            "wiki_write_page",
            {"path": "topics/x", "title": "T", "summary": "S", "body": "B"},
            tools=tools,
        )
        fake.assert_called_once_with(path="topics/x", title="T", summary="S", body="B")
        assert result["status"] == "ok"

    def test_promote_answer_invokes_with_kwargs(self):
        from wikibricks_recorder.wiki_mcp import dispatch_tool
        fake = MagicMock(return_value={"path": "topics/qa", "cited": 1})
        tools = _full_tools_dict()
        tools["wiki_promote_answer"] = fake
        result = dispatch_tool(
            "wiki_promote_answer",
            {"question": "Q?", "answer": "A.", "source_paths": ["a"]},
            tools=tools,
        )
        fake.assert_called_once_with(question="Q?", answer="A.", source_paths=["a"])
        assert result["cited"] == 1

    def test_unknown_tool_raises(self):
        from wikibricks_recorder.wiki_mcp import dispatch_tool
        with pytest.raises(ValueError, match="Unknown tool"):
            dispatch_tool("not_a_tool", {}, tools={})


# ---------------------------------------------------------------------------
# read_full — UC function call with catalog.schema injected from config
# ---------------------------------------------------------------------------


class TestReadFull:
    def test_calls_uc_function_with_catalog_and_schema(self):
        from wikibricks_recorder.wiki_mcp import _read_full
        client = MagicMock()
        result = MagicMock()
        result.result.data_array = [['{"path":"topics/x","title":"X"}']]
        client._exec.return_value = result
        out = _read_full(client, "cat1", "sch1", "topics/x")
        sql = client._exec.call_args[0][0]
        assert "cat1.sch1.fn_wiki_read_full" in sql
        assert out == {"path": "topics/x", "title": "X"}

    def test_returns_none_when_uc_function_returns_null(self):
        from wikibricks_recorder.wiki_mcp import _read_full
        client = MagicMock()
        result = MagicMock()
        result.result.data_array = [[None]]
        client._exec.return_value = result
        assert _read_full(client, "cat1", "sch1", "topics/missing") is None
