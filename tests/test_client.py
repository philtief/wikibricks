"""Tests for WikiClient - the high-level wiki API."""

from unittest.mock import MagicMock

import pytest
from databricks.sdk.service.sql import StatementState, StatementStatus

from wikibricks.client import WikiClient
from wikibricks.ops import HISTORY_TABLE, LOG_TABLE, PAGES_TABLE, SOURCES_TABLE


def _col(name):
    """Create a mock column with a .name attribute (MagicMock's name= is reserved)."""
    m = MagicMock()
    m.name = name
    return m


def _mock_response(rows=None, columns=None):
    """Build a mock StatementResponse. Mirrors real SDK shape: manifest.schema.columns."""
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


class TestWritePage:
    def test_executes_archive_and_merge(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        result = wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')

        assert result == "Wrote wiki page: test/page"
        # archive + merge + _sync_vs_source + _log
        assert ws.statement_execution.execute_statement.call_count == 4

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
        wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}', tags=["alpha", "beta"])

        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "alpha" in sql
        assert "beta" in sql

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


class TestListPages:
    def test_returns_rows_ordered_by_path(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[["a/one", "One", "concept", "1"], ["b/two", "Two", "entity", "2"]],
            columns=["path", "title", "page_type", "version"],
        )
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        pages = wiki.list_pages()

        assert [p["path"] for p in pages] == ["a/one", "b/two"]
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "ORDER BY path" in sql
        assert PAGES_TABLE in sql

    def test_filters_by_prefix(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[], columns=["path", "title", "page_type", "version"]
        )
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        wiki.list_pages(path_prefix="sample/")

        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "LIKE 'sample/%'" in sql

    def test_returns_empty_when_no_rows(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(rows=[])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        assert wiki.list_pages() == []


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


class TestWritePageSourceIds:
    def test_source_ids_in_merge_sql(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page(
            "test/page", "Test", '{"summary":"s","body":"b"}',
            source_ids=["src-1", "src-2"],
        )
        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "src-1" in sql
        assert "src-2" in sql
        assert "source_ids" in sql

    def test_null_source_ids_when_omitted(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')
        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "NULL" in sql


class TestLog:
    def test_write_logs_operation(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')
        # archive[0] + merge[1] + _sync_vs_source[2] + _log[3]
        log_call = ws.statement_execution.execute_statement.call_args_list[3]
        sql = log_call.kwargs["statement"]
        assert LOG_TABLE in sql
        assert "write" in sql

    def test_read_logs_operation(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[["id-1", "test/page", "Test", "concept", "c", "[]",
                   "agent", "2026-01-01", "2026-01-01", "1"]],
            columns=["page_id", "path", "title", "page_type", "content_text",
                      "tags", "created_by", "created_at", "updated_at", "version"],
        )
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.read_page("test/page")
        # read SQL + _log SQL
        assert ws.statement_execution.execute_statement.call_count == 2
        log_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = log_call.kwargs["statement"]
        assert "read" in sql

    def test_log_failure_does_not_raise(self):
        ws = MagicMock()
        call_count = [0]
        def side_effect(**kwargs):
            call_count[0] += 1
            # archive[1] + merge[2] + _sync_vs_source[3] succeed; _log[4] raises
            if call_count[0] <= 3:
                return _mock_response([])
            raise RuntimeError("log table missing")
        ws.statement_execution.execute_statement.side_effect = side_effect
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        result = wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')
        assert result == "Wrote wiki page: test/page"


class TestIngestSource:
    def test_inserts_into_sources(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        result = wiki.ingest_source("https://example.com", "Example", "content", "url")
        assert "Ingested source" in result
        insert_call = ws.statement_execution.execute_statement.call_args_list[0]
        sql = insert_call.kwargs["statement"]
        assert SOURCES_TABLE in sql
        assert "https://example.com" in sql

    def test_optional_fields_null(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.ingest_source("https://example.com")
        insert_call = ws.statement_execution.execute_statement.call_args_list[0]
        sql = insert_call.kwargs["statement"]
        assert "NULL" in sql

    def test_logs_ingest_operation(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.ingest_source("https://example.com", "Title", "Content", "url")
        log_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = log_call.kwargs["statement"]
        assert "ingest" in sql


class TestPromoteAnswer:
    def test_creates_promoted_page(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        path = wiki.promote_answer(
            "What is Delta Lake?", "Delta Lake is...", [],
        )
        assert path.startswith("promoted/")
        assert "delta" in path

    def test_slugifies_query(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        path = wiki.promote_answer("How do I use Unity Catalog?", "Answer", [])
        assert "how-do-i-use-unity-catalog" in path

    def test_logs_promote_operation(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.promote_answer("Test question", "Answer", [])
        calls = ws.statement_execution.execute_statement.call_args_list
        log_sqls = [c.kwargs["statement"] for c in calls if "promote" in c.kwargs.get("statement", "")]
        assert len(log_sqls) >= 1


class TestMaterializeIndex:
    def test_writes_meta_index_page(self):
        ws = MagicMock()
        resp_query = _mock_response(
            rows=[["topics/a", "Page A", "concept", "Summary A"]],
            columns=["path", "title", "page_type", "summary"],
        )
        resp_write = _mock_response([])
        ws.statement_execution.execute_statement.side_effect = [
            resp_query, resp_write, resp_write, resp_write,
        ]
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        result = wiki.materialize_index()
        assert "1 pages" in result

    def test_handles_empty_wiki(self):
        ws = MagicMock()
        resp_empty = _mock_response(rows=[])
        resp_write = _mock_response([])
        ws.statement_execution.execute_statement.side_effect = [
            resp_empty, resp_write, resp_write, resp_write,
        ]
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        result = wiki.materialize_index()
        assert "0 pages" in result


class TestBulkWritePages:
    def _jsonl(self, tmp_path, pages):
        import json
        p = tmp_path / "pages.jsonl"
        p.write_text("\n".join(json.dumps(page) for page in pages))
        return str(p)

    def test_writes_each_page(self, tmp_path):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        jsonl = self._jsonl(tmp_path, [
            {"path": "a/one", "title": "One", "page_type": "concept",
             "content": {"summary": "s", "body": "b"}, "created_by": "test", "tags": []},
            {"path": "a/two", "title": "Two", "page_type": "concept",
             "content": {"summary": "s", "body": "b"}, "created_by": "test", "tags": []},
        ])
        result = wiki.bulk_write_pages(jsonl)
        assert result["written"] == 2

    def test_dry_run_does_not_execute(self, tmp_path):
        ws = MagicMock()
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        jsonl = self._jsonl(tmp_path, [
            {"path": "a/one", "title": "One", "page_type": "concept",
             "content": {"summary": "s", "body": "b"}, "created_by": "test", "tags": []},
        ])
        result = wiki.bulk_write_pages(jsonl, dry_run=True)
        assert result["written"] == 0
        assert result["would_write"] == 1
        ws.statement_execution.execute_statement.assert_not_called()

    def test_logs_bulk_import_summary(self, tmp_path):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        jsonl = self._jsonl(tmp_path, [
            {"path": "a/one", "title": "One", "page_type": "concept",
             "content": {"summary": "s", "body": "b"}, "created_by": "test", "tags": []},
        ])
        wiki.bulk_write_pages(jsonl, source_tag="hotpot-dev")
        calls = ws.statement_execution.execute_statement.call_args_list
        logs = [c.kwargs["statement"] for c in calls
                if "bulk_import" in c.kwargs.get("statement", "")]
        assert len(logs) >= 1
        assert any("hotpot-dev" in s for s in logs)


def _vs_response(rows, columns):
    """Mock Vector Search response shape (manifest.columns, not manifest.schema.columns)."""
    resp = MagicMock()
    resp.result.data_array = rows
    resp.manifest.columns = [_col(c) for c in columns]
    return resp


class TestProposeEdges:
    def _setup(self, ws, page_row, other_pages, vs_hits):
        """Configure ws mock so read_page, list_pages, search, and id-resolver all work."""
        page_cols = ["page_id", "path", "title", "page_type", "content_text",
                     "tags", "created_by", "created_at", "updated_at", "version"]
        list_cols = ["path", "title", "page_type", "version"]
        vs_cols = ["page_id", "path", "title", "page_type", "content_text",
                   "tags", "version", "score"]

        def handler(**kwargs):
            sql = kwargs["statement"].strip()
            if sql.startswith("SELECT page_id, path, title, page_type, content_text, tags, created_by"):
                return _mock_response([page_row], columns=page_cols)
            if sql.startswith("SELECT path, title, page_type, version"):
                rows = [[p["path"], p["title"], p.get("page_type", "concept"),
                         p.get("version", 1)] for p in other_pages]
                return _mock_response(rows, columns=list_cols)
            if sql.startswith("SELECT page_id FROM"):
                for p in other_pages:
                    if f"'{p['path']}'" in sql:
                        return _mock_response([[p["page_id"]]], columns=["page_id"])
                return _mock_response([], columns=["page_id"])
            return _mock_response([])

        ws.statement_execution.execute_statement.side_effect = handler
        ws.vector_search_indexes.query_index.return_value = _vs_response(vs_hits, vs_cols)

    def test_returns_empty_when_page_not_found(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        assert wiki.propose_edges("missing/page") == []

    def test_vs_candidate_above_threshold_is_included(self):
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept",
                    "some content about apples", ["t"], "agent", "t", "t", 1]
        vs_hits = [["page-b", "topics/b", "B", "concept", "...", [], "1", 0.91]]
        self._setup(ws, page_row, other_pages=[], vs_hits=vs_hits)
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        edges = wiki.propose_edges("topics/a", min_similarity=0.7)

        assert len(edges) == 1
        assert edges[0]["target_page_id"] == "page-b"
        assert edges[0]["origin"] == "auto-vs"
        assert edges[0]["confidence"] == 0.91
        assert edges[0]["link_type"] == "related"

    def test_vs_candidate_below_threshold_is_dropped(self):
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept", "content", [], "agent", "t", "t", 1]
        vs_hits = [["page-b", "topics/b", "B", "concept", "...", [], "1", 0.3]]
        self._setup(ws, page_row, other_pages=[], vs_hits=vs_hits)
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        edges = wiki.propose_edges("topics/a", min_similarity=0.7)
        assert edges == []

    def test_excludes_self_reference(self):
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept", "content", [], "agent", "t", "t", 1]
        vs_hits = [["page-a", "topics/a", "A", "concept", "...", [], "1", 0.99]]
        self._setup(ws, page_row, other_pages=[], vs_hits=vs_hits)
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        assert wiki.propose_edges("topics/a") == []

    def test_exact_title_match_yields_auto_title_edge(self):
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A",
                    "concept", "This page discusses Databricks extensively.",
                    [], "agent", "t", "t", 1]
        other_pages = [
            {"page_id": "page-db", "path": "topics/databricks", "title": "Databricks"},
        ]
        self._setup(ws, page_row, other_pages=other_pages, vs_hits=[])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        edges = wiki.propose_edges("topics/a")
        assert len(edges) == 1
        assert edges[0]["origin"] == "auto-title"
        assert edges[0]["confidence"] == 1.0
        assert edges[0]["target_page_id"] == "page-db"

    def test_title_match_dedupes_against_vs_candidate(self):
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept",
                    "Talks about Databricks.", [], "agent", "t", "t", 1]
        other_pages = [
            {"page_id": "page-db", "path": "topics/databricks", "title": "Databricks"},
        ]
        vs_hits = [["page-db", "topics/databricks", "Databricks",
                    "concept", "...", [], "1", 0.85]]
        self._setup(ws, page_row, other_pages=other_pages, vs_hits=vs_hits)
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        edges = wiki.propose_edges("topics/a")
        # Same (target, link_type) pair → one edge, auto-title wins (more specific).
        assert len(edges) == 1
        assert edges[0]["origin"] == "auto-title"


class TestCommitEdges:
    def test_merges_valid_edges(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        written = wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 0.9, "origin": "auto-vs"},
            {"source_page_id": "a", "target_page_id": "c",
             "link_type": "related", "confidence": 1.0, "origin": "auto-title"},
        ])
        assert written == 2
        calls = [c.kwargs["statement"] for c in
                 ws.statement_execution.execute_statement.call_args_list]
        assert any("MERGE INTO" in s for s in calls)

    def test_skips_invalid_link_type(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        written = wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "bogus", "confidence": 0.9, "origin": "auto-vs"},
        ])
        assert written == 0

    def test_skips_invalid_origin(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        written = wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 0.9, "origin": "llm"},
        ])
        assert written == 0

    def test_skips_out_of_range_confidence(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        written = wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 2.0, "origin": "auto-vs"},
        ])
        assert written == 0

    def test_empty_list_is_noop(self):
        ws = MagicMock()
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        assert wiki.commit_edges([]) == 0
        ws.statement_execution.execute_statement.assert_not_called()


class TestGraphNeighbors:
    def test_returns_neighbor_rows(self):
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept", "c", [], "agent", "t", "t", 1]
        page_cols = ["page_id", "path", "title", "page_type", "content_text",
                     "tags", "created_by", "created_at", "updated_at", "version"]
        neighbor_cols = ["source_page_id", "target_page_id", "target_path",
                         "target_title", "link_type", "confidence", "origin", "hop"]
        neighbor_rows = [
            ["page-a", "page-b", "topics/b", "B", "related", 0.9, "auto-vs", 1],
        ]

        def handler(**kwargs):
            sql = kwargs["statement"].strip()
            if sql.startswith("SELECT page_id, path, title"):
                return _mock_response([page_row], columns=page_cols)
            return _mock_response(neighbor_rows, columns=neighbor_cols)

        ws.statement_execution.execute_statement.side_effect = handler
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        neighbors = wiki.graph_neighbors("topics/a", depth=1)
        assert len(neighbors) == 1
        assert neighbors[0]["target_path"] == "topics/b"
        assert neighbors[0]["hop"] == 1

    def test_returns_empty_when_page_not_found(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        assert wiki.graph_neighbors("missing") == []

    def test_depth_out_of_range_raises(self):
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept", "c", [], "agent", "t", "t", 1]
        page_cols = ["page_id", "path", "title", "page_type", "content_text",
                     "tags", "created_by", "created_at", "updated_at", "version"]
        ws.statement_execution.execute_statement.return_value = _mock_response(
            [page_row], columns=page_cols)
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        with pytest.raises(ValueError):
            wiki.graph_neighbors("topics/a", depth=5)


class TestFixBrokenLinks:
    def test_returns_delta_count(self):
        ws = MagicMock()
        responses = iter([
            _mock_response([[10]], columns=["c"]),
            _mock_response([]),
            _mock_response([[7]], columns=["c"]),
        ])
        ws.statement_execution.execute_statement.side_effect = lambda **_: next(responses)
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        deleted = wiki.fix_broken_links()
        assert deleted == 3

    def test_zero_when_nothing_deleted(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([[5]], columns=["c"])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        assert wiki.fix_broken_links() == 0
