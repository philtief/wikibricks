"""Tests for WikiClient vocabulary helpers — slug counts + active list."""

from __future__ import annotations

from unittest.mock import MagicMock

from databricks.sdk.service.sql import StatementState, StatementStatus

from wikibricks.client import WikiClient
from wikibricks.ops import VOCABULARY_TABLE


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


def _client():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _mock_response([])
    return WikiClient(warehouse_id="wh", workspace_client=ws), ws


class TestUpsertVocabularySlugs:
    def test_no_op_on_empty_list(self):
        wiki, ws = _client()
        wiki.upsert_vocabulary_slugs([], source="llm")
        ws.statement_execution.execute_statement.assert_not_called()

    def test_merges_into_vocabulary_table(self):
        wiki, ws = _client()
        wiki.upsert_vocabulary_slugs(["solvd", "allianz-italy"], source="llm")
        ws.statement_execution.execute_statement.assert_called_once()
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert VOCABULARY_TABLE in sql
        assert "MERGE INTO" in sql
        assert "'solvd'" in sql
        assert "'allianz-italy'" in sql
        assert "'llm'" in sql

    def test_rejects_unknown_source(self):
        wiki, _ws = _client()
        import pytest
        with pytest.raises(ValueError):
            wiki.upsert_vocabulary_slugs(["s"], source="garbage")

    def test_normalizes_slugs(self):
        """Lowercase + hyphen-separated. Drops empties."""
        wiki, ws = _client()
        wiki.upsert_vocabulary_slugs(["Solvd Group", "  ", "AZ_CH"], source="llm")
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "'solvd-group'" in sql
        assert "'az-ch'" in sql

    def test_normalization_strips_unsafe_chars(self):
        """Apostrophes, semicolons, quotes are normalized out — slugs are alnum+hyphen."""
        wiki, ws = _client()
        wiki.upsert_vocabulary_slugs(["it's-a-slug", "drop;table", 'name"with quote'], source="llm")
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        # No raw apostrophes / semicolons / quotes from input made it through
        assert "it's-a-slug" not in sql
        assert "drop;table" not in sql
        assert 'name"with quote' not in sql
        # The cleaned slugs are present
        assert "'its-a-slug'" in sql
        assert "'droptable'" in sql
        assert "'namewith-quote'" in sql

    def test_dedupes_input_slugs_for_merge(self):
        """Duplicate or post-normalization-equivalent slugs must collapse to one
        source row. Delta MERGE rejects multiple source rows matching the same
        target row (DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE)."""
        wiki, ws = _client()
        n = wiki.upsert_vocabulary_slugs(
            ["solvd", "solvd", "Solvd", "allianz"], source="llm"
        )
        # All four normalize to {solvd, allianz}; return value reflects deduped count.
        assert n == 2
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        # Each unique slug appears exactly once in the VALUES rows.
        assert sql.count("('solvd',") == 1
        assert sql.count("('allianz',") == 1


class TestListActiveVocabulary:
    def test_returns_active_slugs(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[["solvd"], ["allianz-italy"], ["agi"]],
            columns=["slug"],
        )
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        assert wiki.list_active_vocabulary() == ["solvd", "allianz-italy", "agi"]

    def test_empty_result(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(rows=[])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        assert wiki.list_active_vocabulary() == []

    def test_sql_filters_active_status_and_recent(self):
        wiki, ws = _client()
        wiki.list_active_vocabulary()
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert VOCABULARY_TABLE in sql
        assert "status = 'active'" in sql
        assert "last_seen" in sql
