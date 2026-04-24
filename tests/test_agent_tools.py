"""Tests for the custom agent-tool factory (wikibricks.agent_tools)."""

from unittest.mock import MagicMock

from databricks.sdk.service.sql import StatementState, StatementStatus

from wikibricks import make_agent_tools


def _col(name):
    m = MagicMock()
    m.name = name
    return m


def _mock_response(rows=None, columns=None):
    resp = MagicMock()
    resp.status = StatementStatus(state=StatementState.SUCCEEDED, error=None)
    if rows is not None:
        resp.result.data_array = rows
        if columns:
            resp.manifest.schema.columns = [_col(c) for c in columns]
        else:
            resp.manifest.schema = None
    else:
        resp.result = None
    return resp


class TestMakeAgentTools:
    def test_returns_write_and_promote_tools(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        tools = make_agent_tools(warehouse_id="wh-123", workspace_client=ws)

        assert set(tools) == {"wiki_write_page", "wiki_promote_answer"}
        assert callable(tools["wiki_write_page"])
        assert callable(tools["wiki_promote_answer"])

    def test_tools_have_docstrings(self):
        tools = make_agent_tools(warehouse_id="wh-123", workspace_client=MagicMock())
        assert "Create or update a wiki page" in tools["wiki_write_page"].__doc__
        assert "Promote a chat answer" in tools["wiki_promote_answer"].__doc__


class TestWritePageTool:
    def test_returns_path_and_status(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        tools = make_agent_tools(warehouse_id="wh-123", workspace_client=ws)

        result = tools["wiki_write_page"](
            path="topics/foo",
            title="Foo",
            summary="a summary",
            body="a body",
        )

        assert result == {"path": "topics/foo", "status": "ok"}

    def test_forwards_tags_and_page_type(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        tools = make_agent_tools(warehouse_id="wh-123", workspace_client=ws)

        tools["wiki_write_page"](
            path="topics/foo",
            title="Foo",
            summary="s",
            body="b",
            page_type="synthesis",
            tags=["x", "y"],
        )

        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "synthesis" in sql
        assert "x" in sql and "y" in sql


class TestPromoteAnswerTool:
    def test_resolves_source_paths_and_promotes(self):
        ws = MagicMock()
        read_resp = _mock_response(
            rows=[["pid-xyz", "topics/src", "Src", "concept", '{"summary":"s","body":"b"}',
                   None, None, None, [], 1]],
            columns=["page_id", "path", "title", "page_type", "content",
                     "created_by", "created_at", "updated_at", "tags", "version"],
        )
        write_resp = _mock_response([])
        ws.statement_execution.execute_statement.side_effect = [
            read_resp,  # read_page(source_paths[0]) lookup inside tool
            write_resp,  # archive
            write_resp,  # merge pages
            write_resp,  # sync vs source
            write_resp,  # _log write
            read_resp,  # read_page(promoted) inside promote_answer
            write_resp,  # merge cites link
            write_resp,  # _log promote
        ]

        tools = make_agent_tools(warehouse_id="wh-123", workspace_client=ws)
        result = tools["wiki_promote_answer"](
            question="What is foo?",
            answer="Foo is bar.",
            source_paths=["topics/src"],
        )

        assert result["path"].startswith("promoted/")
        assert result["cited"] == 1

    def test_skips_unknown_source_paths(self):
        ws = MagicMock()
        empty_resp = _mock_response(
            rows=[],
            columns=["page_id", "path", "title", "page_type", "content",
                     "created_by", "created_at", "updated_at", "tags", "version"],
        )
        write_resp = _mock_response([])
        ws.statement_execution.execute_statement.side_effect = [
            empty_resp,  # read_page(unknown) → no rows
            write_resp,  # archive
            write_resp,  # merge pages
            write_resp,  # sync vs source
            write_resp,  # _log write
            write_resp,  # _log promote
        ]

        tools = make_agent_tools(warehouse_id="wh-123", workspace_client=ws)
        result = tools["wiki_promote_answer"](
            question="What is foo?",
            answer="Foo is bar.",
            source_paths=["topics/unknown"],
        )

        assert result["cited"] == 0
