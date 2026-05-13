"""Tests for WikiClient.list_recent_by_cwd_tag — the helper that powers
the SessionStart prelude in the recorder."""

from __future__ import annotations

from unittest.mock import MagicMock

from databricks.sdk.service.sql import StatementState, StatementStatus

from wikibricks.client import WikiClient
from wikibricks.ops import PAGES_TABLE


def _col(name):
    c = MagicMock()
    c.name = name
    return c


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


def _client(rows=None, columns=None):
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _mock_response(rows, columns)
    return WikiClient(warehouse_id="wh", workspace_client=ws), ws


def test_empty_result():
    wiki, _ws = _client(rows=[])
    assert wiki.list_recent_by_cwd_tag("wikibricks") == []


def test_none_result():
    wiki, _ws = _client(rows=None)
    assert wiki.list_recent_by_cwd_tag("wikibricks") == []


def test_returns_dicts_with_expected_fields():
    rows = [
        ["sessions/2026/05/13/abc", "Fixed recorder bug", "summary text 1", "2026-05-13T08:00:00Z"],
        ["sessions/2026/05/12/def", "Added title heuristic", "summary text 2", "2026-05-12T15:00:00Z"],
    ]
    cols = ["path", "title", "summary", "updated_at"]
    wiki, _ws = _client(rows=rows, columns=cols)
    result = wiki.list_recent_by_cwd_tag("wikibricks", limit=2)
    assert len(result) == 2
    assert result[0]["path"] == "sessions/2026/05/13/abc"
    assert result[0]["title"] == "Fixed recorder bug"
    assert result[0]["summary"] == "summary text 1"


def test_sql_filters_by_cwd_tag_and_orders_by_updated_at():
    wiki, ws = _client(rows=[])
    wiki.list_recent_by_cwd_tag("my-project", limit=5)
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert PAGES_TABLE in sql
    assert "array_contains(tags, 'cwd:my-project')" in sql
    assert "ORDER BY updated_at DESC" in sql
    assert "LIMIT 5" in sql


def test_default_limit_3():
    wiki, ws = _client(rows=[])
    wiki.list_recent_by_cwd_tag("p")
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "LIMIT 3" in sql


def test_escapes_cwd_basename():
    wiki, ws = _client(rows=[])
    wiki.list_recent_by_cwd_tag("it's-a-dir")
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    # SQL-escaped apostrophe
    assert "it\\'s-a-dir" in sql or "it''s-a-dir" in sql or "it\\\\'s-a-dir" in sql


def test_rejects_empty_cwd():
    wiki, ws = _client(rows=[])
    assert wiki.list_recent_by_cwd_tag("") == []
    ws.statement_execution.execute_statement.assert_not_called()
