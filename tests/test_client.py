"""Tests for WikiClient — the high-level wiki API."""

from unittest.mock import MagicMock

import pytest
from databricks.sdk.service.sql import StatementState, StatementStatus

from wikibricks.client import WikiClient
from wikibricks.ops import HISTORY_TABLE, PAGES_TABLE


def _col(name):
    """Create a mock column with a .name attribute (MagicMock's name= is reserved)."""
    m = MagicMock()
    m.name = name
    return m


def _mock_response(rows=None, columns=None):
    """Build a mock StatementResponse."""
    resp = MagicMock()
    resp.status = StatementStatus(state=StatementState.SUCCEEDED, error=None)
    if rows is not None:
        resp.result.data_array = rows
        if columns:
            resp.manifest.columns = [_col(c) for c in columns]
    else:
        resp.result = None
    return resp


class TestWritePage:
    def test_executes_archive_and_merge(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        result = wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')

        assert result == "Wrote wiki page: test/page"
        assert ws.statement_execution.execute_statement.call_count == 2

    def test_archive_sql_targets_history(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')

        archive_call = ws.statement_execution.execute_statement.call_args_list[0]
        sql = archive_call.kwargs["statement"]
        assert HISTORY_TABLE in sql
        assert "INSERT INTO" in sql

    def test_merge_sql_targets_pages(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')

        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert PAGES_TABLE in sql
        assert "MERGE INTO" in sql

    def test_escapes_apostrophes(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("test/page", "It's a title", '{"summary":"don\'t panic","body":"b"}')

        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "It\\'s a title" in sql
        assert "don\\'t panic" in sql

    def test_accepts_dict_content(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("test/page", "Test", {"summary": "s", "body": "b"})

        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "PARSE_JSON" in sql

    def test_includes_tags(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}', tags=["fraud", "claims"])

        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "fraud" in sql
        assert "claims" in sql

    def test_raises_on_sql_failure(self):
        ws = MagicMock()
        resp = MagicMock()
        resp.status = StatementStatus(state=StatementState.FAILED, error="bad sql")
        ws.statement_execution.execute_statement.return_value = resp
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        with pytest.raises(RuntimeError, match="SQL execution failed"):
            wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')


class TestReadPage:
    def test_returns_dict_for_existing_page(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[["id-1", "test/page", "Test", "concept", "content", "[]", "agent", "2026-01-01", "2026-01-01", "1"]],
            columns=["page_id", "path", "title", "page_type", "content_text", "tags",
                      "created_by", "created_at", "updated_at", "version"],
        )
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        page = wiki.read_page("test/page")

        assert page["path"] == "test/page"
        assert page["title"] == "Test"
        assert page["version"] == "1"

    def test_returns_none_for_missing_page(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(rows=[])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        assert wiki.read_page("nonexistent") is None


class TestSearch:
    def test_calls_vector_search(self):
        ws = MagicMock()
        resp = MagicMock()
        resp.result.data_array = [["id-1", "test/page", "Test", "concept", "content", "[]", "1"]]
        resp.manifest.columns = [_col(c) for c in
                                  ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]]
        ws.vector_search_indexes.query_index.return_value = resp
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        results = wiki.search("test query")

        assert len(results) == 1
        assert results[0]["path"] == "test/page"
        ws.vector_search_indexes.query_index.assert_called_once()

    def test_returns_empty_on_no_results(self):
        ws = MagicMock()
        resp = MagicMock()
        resp.result = None
        ws.vector_search_indexes.query_index.return_value = resp
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        assert wiki.search("nothing") == []


class TestHistory:
    def test_returns_version_list(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[["2", "agent", "2026-01-02", "Updated"], ["1", "agent", "2026-01-01", "Original"]],
            columns=["version", "created_by", "created_at", "summary"],
        )
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        versions = wiki.history("test/page")

        assert len(versions) == 2
        assert versions[0]["version"] == "2"
        assert versions[1]["version"] == "1"

    def test_returns_empty_for_no_history(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(rows=[])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        assert wiki.history("new/page") == []
