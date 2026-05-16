"""Tests for WikiClient - the high-level wiki API."""

from unittest.mock import MagicMock

import pytest
from databricks.sdk.service.sql import StatementState, StatementStatus

from wikibricks.client import WikiClient
from wikibricks.ops import (
    HISTORY_TABLE,
    LOG_TABLE,
    PAGES_TABLE,
    SOURCES_TABLE,
    VOCABULARY_TABLE,
)


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

    def test_excludes_ephemeral_stub_by_default(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[], columns=["path", "title", "page_type", "version"]
        )
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        wiki.list_pages()

        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "ephemeral:stub" in sql
        assert "array_contains" in sql

    def test_includes_ephemeral_stub_when_requested(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[], columns=["path", "title", "page_type", "version"]
        )
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        wiki.list_pages(include_ephemeral=True)

        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "ephemeral:stub" not in sql

    def test_ephemeral_filter_combines_with_prefix(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[], columns=["path", "title", "page_type", "version"]
        )
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        wiki.list_pages(path_prefix="sessions/")

        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "LIKE 'sessions/%'" in sql
        assert "ephemeral:stub" in sql


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

    def test_search_logs_returned_paths_in_details(self):
        # Citation tracking: the v0.5.0 promote/agent_traces view depends on
        # `wiki_log` rows where op_type='search' AND details contains
        # `returned_paths`. The search call must emit this JSON.
        ws = MagicMock()
        resp = MagicMock()
        resp.result.data_array = [
            ["id-1", "topics/foo", "Foo", "concept", "content", "[]", "1"],
            ["id-2", "topics/bar", "Bar", "concept", "content", "[]", "1"],
        ]
        resp.manifest.columns = [_col(c) for c in
                                  ["page_id", "path", "title", "page_type",
                                   "content_text", "tags", "version"]]
        ws.vector_search_indexes.query_index.return_value = resp
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.search("test query", num_results=7)

        # The _log INSERT into wiki_log is the last execute_statement call.
        log_call = ws.statement_execution.execute_statement.call_args_list[-1]
        sql = log_call.kwargs["statement"]
        assert "returned_paths" in sql
        assert "topics/foo" in sql
        assert "topics/bar" in sql
        # k and mode also embedded for analytics
        assert '"k": 7' in sql or "'k': 7" in sql
        assert "HYBRID" in sql

    def test_search_logs_empty_paths_when_no_results(self):
        # Even on empty results, the search must log so we know it ran. The
        # health probe counts these against the `citations_logged` threshold.
        ws = MagicMock()
        resp = MagicMock()
        resp.result = None
        ws.vector_search_indexes.query_index.return_value = resp
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.search("nothing")

        log_call = ws.statement_execution.execute_statement.call_args_list[-1]
        sql = log_call.kwargs["statement"]
        assert "returned_paths" in sql

    def test_search_excludes_ephemeral_stub_by_default(self):
        # VS tags come back as a JSON-serialised string; drop rows whose
        # tags include the stub marker before returning to the caller.
        ws = MagicMock()
        resp = MagicMock()
        resp.result.data_array = [
            ["id-1", "topics/real",    "Real",    "concept", "c", '["session"]',                       "1"],
            ["id-2", "sessions/stub",  "Stub",    "concept", "c", '["session","ephemeral:stub"]',     "1"],
            ["id-3", "topics/another", "Another", "concept", "c", '["session"]',                       "1"],
        ]
        resp.manifest.columns = [_col(c) for c in
                                  ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]]
        ws.vector_search_indexes.query_index.return_value = resp
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        results = wiki.search("anything")

        paths = [r["path"] for r in results]
        assert "sessions/stub" not in paths
        assert "topics/real" in paths
        assert "topics/another" in paths

    def test_search_includes_ephemeral_stub_when_requested(self):
        ws = MagicMock()
        resp = MagicMock()
        resp.result.data_array = [
            ["id-1", "topics/real",   "Real", "concept", "c", '["session"]',                   "1"],
            ["id-2", "sessions/stub", "Stub", "concept", "c", '["session","ephemeral:stub"]', "1"],
        ]
        resp.manifest.columns = [_col(c) for c in
                                  ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]]
        ws.vector_search_indexes.query_index.return_value = resp
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        results = wiki.search("anything", include_ephemeral=True)

        assert {r["path"] for r in results} == {"topics/real", "sessions/stub"}

    def test_search_overfetches_to_account_for_filtered_stubs(self):
        # If we drop stubs after VS returns N results, callers asking for
        # num_results=5 must still get 5 real hits when stubs are present.
        # The implementation overfetches; this asserts the contract.
        ws = MagicMock()
        resp = MagicMock()
        # 6 results: 3 stubs + 3 real. caller asks for 3 real.
        resp.result.data_array = [
            ["id-1", "p1", "T", "concept", "c", '["session","ephemeral:stub"]', "1"],
            ["id-2", "p2", "T", "concept", "c", '["session"]',                   "1"],
            ["id-3", "p3", "T", "concept", "c", '["session","ephemeral:stub"]', "1"],
            ["id-4", "p4", "T", "concept", "c", '["session"]',                   "1"],
            ["id-5", "p5", "T", "concept", "c", '["session","ephemeral:stub"]', "1"],
            ["id-6", "p6", "T", "concept", "c", '["session"]',                   "1"],
        ]
        resp.manifest.columns = [_col(c) for c in
                                  ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]]
        ws.vector_search_indexes.query_index.return_value = resp
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        results = wiki.search("anything", num_results=3)

        assert len(results) == 3
        assert all("ephemeral:stub" not in (r.get("tags") or "") for r in results)


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


class TestWritePagePreservesLlmTags:
    """Bug 3 regression — write_page must preserve `llm:`-prefixed tags from
    prior versions.

    Background: the recorder writes session pages with mechanical tags only
    (session, cwd:..., model:..., user:...). The wiki_tag task adds llm:* tags
    via WikiClient.append_page_tags. Before the fix, every subsequent
    write_page call wiped llm: tags because the MERGE UPDATE clause did a full
    replacement of `tags`. After the fix, the MERGE preserves llm:* tags via
    filter() + array_union.
    """

    def test_update_branch_preserves_existing_llm_tags_via_filter(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.write_page("p", "T", '{"summary":"s","body":"b"}',
                        tags=["session", "cwd:x"])
        merge_sql = ws.statement_execution.execute_statement.call_args_list[1].kwargs["statement"]
        # Caller's tags must still appear
        assert "'session'" in merge_sql
        assert "'cwd:x'" in merge_sql
        # The UPDATE branch must union with filter(COALESCE(target.tags(...llm:%) on existing tags
        assert "filter(COALESCE(target.tags" in merge_sql
        assert "llm:" in merge_sql
        # And produce a deduped union (so caller's tags and llm: tags merge safely)
        assert "array_union" in merge_sql
        assert "array_distinct" in merge_sql

    def test_update_branch_uses_target_tags_for_preservation(self):
        # The preservation must read from `target.tags` (the pre-merge row),
        # not from a new constant. Otherwise the merge would always preserve
        # an empty array.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.write_page("p", "T", '{"summary":"s","body":"b"}')
        merge_sql = ws.statement_execution.execute_statement.call_args_list[1].kwargs["statement"]
        assert "target.tags" in merge_sql

    def test_insert_branch_does_not_need_preservation(self):
        # New pages have no prior tags — only the UPDATE branch needs the
        # preservation dance. INSERT branch should be plain.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.write_page("p", "T", '{"summary":"s","body":"b"}', tags=["session"])
        merge_sql = ws.statement_execution.execute_statement.call_args_list[1].kwargs["statement"]
        # Confirm there's only ONE filter(COALESCE(target.tags call (in the UPDATE branch).
        assert merge_sql.count("filter(COALESCE(target.tags") == 1

    def test_no_tags_arg_still_preserves_llm(self):
        # Even when caller passes no tags, llm: tags on the existing row stay.
        # `tags = array_distinct(array_union(ARRAY(), filter(COALESCE(target.tags(target.tags, ...)))`
        # i.e. caller-empty + preserved-llm = just the llm: tags.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.write_page("p", "T", '{"summary":"s","body":"b"}')  # no tags
        merge_sql = ws.statement_execution.execute_statement.call_args_list[1].kwargs["statement"]
        # filter(COALESCE(target.tags still appears even with empty caller tags
        assert "filter(COALESCE(target.tags" in merge_sql


class TestWritePagesBatchPreservesLlmTags:
    """Bug 3 regression — write_pages (batched path used by segregate)
    must also preserve llm: tags.
    """

    def test_batch_merge_includes_filter_for_llm_preservation(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.write_pages([
            {"path": "p1", "title": "T1", "content": {"summary": "s", "body": "b"},
             "tags": ["session"]},
            {"path": "p2", "title": "T2", "content": {"summary": "s", "body": "b"},
             "tags": ["chunk"]},
        ])
        # The MERGE call is the second statement (after archive)
        merge_sql = ws.statement_execution.execute_statement.call_args_list[1].kwargs["statement"]
        assert "MERGE INTO" in merge_sql
        assert "filter(COALESCE(target.tags" in merge_sql
        assert "llm:" in merge_sql
        assert "array_union" in merge_sql


class TestWritePageChunks:
    """parent_id + chunk_index let segregate split oversize pages."""

    def test_chunk_kwargs_in_merge_sql(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page(
            "topics/foo/chunks/01", "Foo - Section A",
            '{"summary":"s","body":"b"}',
            parent_id="parent-uuid-abc",
            chunk_index=1,
        )
        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "parent_id" in sql
        assert "parent-uuid-abc" in sql
        assert "chunk_index" in sql

    def test_null_parent_id_when_omitted(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("topics/foo", "Foo", '{"summary":"s","body":"b"}')
        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        # Top-level pages have NULL parent_id and NULL chunk_index — assert
        # both columns appear with NULL nearby.
        assert "parent_id" in sql

    def test_chunk_index_zero_is_written(self):
        # Edge: chunk_index=0 is valid (not falsy) — must not be coerced to NULL.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page(
            "topics/foo/chunks/00", "Foo - Zero",
            '{"summary":"s","body":"b"}',
            parent_id="parent-uuid-abc",
            chunk_index=0,
        )
        merge_call = ws.statement_execution.execute_statement.call_args_list[1]
        sql = merge_call.kwargs["statement"]
        assert "chunk_index" in sql
        # The zero literal must appear in the SQL
        assert " 0" in sql or ",0" in sql or "=0" in sql


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

    def test_log_sql_includes_log_id(self):
        # wiki_log.log_id is NOT NULL in the DDL. If the INSERT omits it the
        # statement fails and _log silently swallows — so library-side writes
        # never land in the audit log. Regression test for bug found during
        # Phase 3 promote-path validation.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)
        wiki.write_page("test/page", "Test", '{"summary":"s","body":"b"}')
        log_call = ws.statement_execution.execute_statement.call_args_list[3]
        sql = log_call.kwargs["statement"]
        assert "log_id" in sql
        assert "uuid()" in sql

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


class TestUpsertVocabulary:
    def test_empty_observations_skips_sql(self):
        ws = MagicMock()
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        assert wiki.upsert_vocabulary([]) == 0
        ws.statement_execution.execute_statement.assert_not_called()

    def test_emits_merge_against_vocab_table(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.upsert_vocabulary([{"slug": "row-level-security", "source": "auto_tag"}])
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "MERGE INTO" in sql
        assert VOCABULARY_TABLE in sql
        assert "row-level-security" in sql

    def test_approve_threshold_baked_into_sql(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.upsert_vocabulary([{"slug": "x", "source": "auto_tag"}], approve_threshold=5)
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        # Both the matched (count+1) and not-matched (count=1) branches must
        # carry the threshold so the status flip is consistent.
        assert ">= 5" in sql
        assert "'approved'" in sql
        assert "'pending'" in sql

    def test_default_source_is_auto_tag(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.upsert_vocabulary([{"slug": "x"}])  # no source
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "auto_tag" in sql

    def test_batches_multiple_observations_into_one_statement(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        n = wiki.upsert_vocabulary([
            {"slug": "a", "source": "auto_tag"},
            {"slug": "b", "source": "auto_tag"},
            {"slug": "c", "source": "auto_tag"},
        ])
        assert n == 3
        assert ws.statement_execution.execute_statement.call_count == 1
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "UNION ALL" in sql
        for slug in ("a", "b", "c"):
            assert f"'{slug}'" in sql

    def test_handles_duplicate_slugs_in_batch(self):
        # Bug 5: when the LLM proposes the same tag across multiple pages,
        # the batch arrives with duplicate slugs. Without GROUP BY, Delta
        # rejects the MERGE with DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.upsert_vocabulary([
            {"slug": "data-ingestion", "source": "auto_tag"},
            {"slug": "data-ingestion", "source": "auto_tag"},
            {"slug": "data-ingestion", "source": "auto_tag"},
        ])
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        # The CTE must collapse duplicates with GROUP BY slug.
        assert "GROUP BY slug" in sql
        # And the MERGE updates count by occurrences (sum from the GROUP BY),
        # not by a hardcoded 1.
        assert "t.count + s.occurrences" in sql
        # The threshold check uses the post-merge count.
        assert "t.count + s.occurrences >=" in sql

    def test_escapes_single_quotes_in_slug(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        # Should not raise / inject — _escape uses backslash escape (existing convention).
        wiki.upsert_vocabulary([{"slug": "o'brien", "source": "auto_tag"}])
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "o\\'brien" in sql


class TestAppendPageTags:
    def test_empty_tags_is_noop(self):
        ws = MagicMock()
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.append_page_tags("topics/foo", [])
        ws.statement_execution.execute_statement.assert_not_called()

    def test_emits_update_with_array_union_distinct(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.append_page_tags("topics/foo", ["llm:row-level-security", "llm:delta-lake"])
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert PAGES_TABLE in sql
        assert "array_distinct" in sql
        assert "array_union" in sql
        assert "WHERE path = 'topics/foo'" in sql
        assert "llm:row-level-security" in sql
        assert "llm:delta-lake" in sql

    def test_updates_updated_at(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.append_page_tags("p", ["llm:x"])
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "updated_at = current_timestamp()" in sql

    def test_escapes_single_quotes_in_path(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.append_page_tags("topics/o'brien", ["llm:foo"])
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "o\\'brien" in sql


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
        """Configure ws mock so read_page, list_pages, and search all work."""
        page_cols = ["page_id", "path", "title", "page_type", "content_text",
                     "tags", "created_by", "created_at", "updated_at", "version"]
        list_cols = ["page_id", "path", "title", "page_type", "version"]
        vs_cols = ["page_id", "path", "title", "page_type", "content_text",
                   "tags", "version", "score"]

        def handler(**kwargs):
            sql = kwargs["statement"].strip()
            if sql.startswith("SELECT page_id, path, title, page_type, content_text, tags, created_by"):
                return _mock_response([page_row], columns=page_cols)
            if sql.startswith("SELECT page_id, path, title, page_type, version"):
                rows = [[p["page_id"], p["path"], p["title"],
                         p.get("page_type", "concept"), p.get("version", 1)]
                        for p in other_pages]
                return _mock_response(rows, columns=list_cols)
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

    def test_caller_supplied_other_pages_skips_list_pages_sql(self):
        # When the caller pre-fetches list_pages() and passes it in (curate's
        # batch loop), propose_edges must not re-issue the list_pages SELECT.
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept",
                    "This page mentions Databricks.", [], "agent", "t", "t", 1]
        # _setup's handler will still answer a list_pages query if asked, but
        # the assertion below checks that branch was never hit.
        self._setup(ws, page_row, other_pages=[], vs_hits=[])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        prefetched = [
            {"page_id": "page-db", "path": "topics/databricks", "title": "Databricks"},
        ]
        edges = wiki.propose_edges("topics/a", other_pages=prefetched)

        statements = [c.kwargs["statement"] for c in
                      ws.statement_execution.execute_statement.call_args_list]
        # Only read_page should have hit the warehouse — no list_pages SELECT.
        assert not any(
            s.strip().startswith("SELECT page_id, path, title, page_type, version")
            for s in statements
        ), f"list_pages SQL was issued despite other_pages being supplied: {statements}"
        # Title match still works against the supplied list.
        assert len(edges) == 1
        assert edges[0]["target_page_id"] == "page-db"
        assert edges[0]["origin"] == "auto-title"

    def test_other_pages_none_falls_back_to_list_pages(self):
        # Default behaviour preserved: when the caller does not supply
        # other_pages, propose_edges still issues the list_pages SELECT.
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept",
                    "Mentions Databricks.", [], "agent", "t", "t", 1]
        other_pages = [
            {"page_id": "page-db", "path": "topics/databricks", "title": "Databricks"},
        ]
        self._setup(ws, page_row, other_pages=other_pages, vs_hits=[])
        wiki = WikiClient(warehouse_id="wh-123", workspace_client=ws)

        edges = wiki.propose_edges("topics/a")  # no other_pages arg

        statements = [c.kwargs["statement"] for c in
                      ws.statement_execution.execute_statement.call_args_list]
        assert any(
            s.strip().startswith("SELECT page_id, path, title, page_type, version")
            for s in statements
        )
        assert len(edges) == 1
        assert edges[0]["origin"] == "auto-title"


class TestCommitEdges:
    def test_writes_valid_edges(self):
        # Bi-temporal model (v0.6.0): commit_edges produces two batched
        # statements — an UPDATE that closes any prior open rows for the
        # same (src, tgt, link_type) keys, and an INSERT for the new rows.
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
        assert any("UPDATE" in s and "valid_until = current_timestamp()" in s
                   for s in calls)
        assert any(s.strip().startswith("INSERT INTO") and "VALUES" in s
                   for s in calls)

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


class TestUpdateGraphScores:
    """v0.7.0 — `WikiClient.update_graph_scores` batch-merges PageRank hub_scores
    and community_ids into the pages table. Called by the wiki_graph_analytics
    notebook after each curate run."""

    def test_empty_list_is_noop(self):
        ws = MagicMock()
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        assert wiki.update_graph_scores([]) == 0
        ws.statement_execution.execute_statement.assert_not_called()

    def test_single_score_emits_merge_into_pages(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        n = wiki.update_graph_scores([
            {"page_id": "p1", "hub_score": 0.123, "community_id": 7}
        ])
        assert n == 1
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "MERGE INTO" in sql
        assert PAGES_TABLE in sql
        assert "p1" in sql
        assert "0.123" in sql
        assert "7" in sql

    def test_null_values_pass_through_as_sql_null(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.update_graph_scores([
            {"page_id": "p1", "hub_score": None, "community_id": None}
        ])
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert ", NULL," in sql or ", NULL)" in sql  # at least one NULL literal

    def test_batches_multiple_pages_into_one_statement(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        n = wiki.update_graph_scores([
            {"page_id": "p1", "hub_score": 0.1, "community_id": 1},
            {"page_id": "p2", "hub_score": 0.2, "community_id": 1},
            {"page_id": "p3", "hub_score": 0.3, "community_id": 2},
        ])
        assert n == 3
        assert ws.statement_execution.execute_statement.call_count == 1
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        for pid in ("p1", "p2", "p3"):
            assert pid in sql


class TestSearchRerankWithPagerank:
    """v0.7.0 — `search(rerank_with_pagerank=True)` blends VS rank and
    PageRank rank via Reciprocal Rank Fusion (k=60). Backward-compat:
    default is False, no behavior change for existing callers."""

    def _vs_resp(self, rows, columns):
        resp = MagicMock()
        resp.result.data_array = rows
        resp.manifest.columns = [_col(c) for c in columns]
        return resp

    def test_default_off_does_not_query_pages_for_hub_score(self):
        # Backward-compat: no pages query when rerank flag is False.
        ws = MagicMock()
        ws.vector_search_indexes.query_index.return_value = self._vs_resp(
            [["id-1", "topics/foo", "Foo", "concept", "c", "[]", "1"]],
            ["page_id", "path", "title", "page_type", "content_text", "tags", "version"],
        )
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.search("q", rerank_with_pagerank=False)
        # Only the _log INSERT runs on execute_statement (search doesn't query pages).
        for c in ws.statement_execution.execute_statement.call_args_list:
            sql = c.kwargs["statement"]
            assert "hub_score" not in sql, f"unexpected hub_score query: {sql}"

    def test_flag_on_queries_pages_for_hub_score(self):
        # VS ranks [id-1, id-2, id-3]; PageRank scores rank [id-3, id-1, id-2]
        # (id-3 is the most-cited page). RRF with k=60:
        #   id-1: 1/61 + 1/62 = 0.03252  ← VS#1 + PR#2 → fused #1
        #   id-3: 1/63 + 1/61 = 0.03226  ← VS#3 + PR#1 → fused #2 (moved up)
        #   id-2: 1/62 + 1/63 = 0.03200  ← VS#2 + PR#3 → fused #3 (moved down)
        # Demonstrates rerank moves a highly-cited page up the list.
        ws = MagicMock()
        ws.vector_search_indexes.query_index.return_value = self._vs_resp(
            [
                ["id-1", "topics/foo", "Foo", "concept", "c", "[]", "1"],
                ["id-2", "topics/bar", "Bar", "concept", "c", "[]", "1"],
                ["id-3", "topics/qux", "Qux", "concept", "c", "[]", "1"],
            ],
            ["page_id", "path", "title", "page_type", "content_text", "tags", "version"],
        )

        def handler(**kwargs):
            sql = kwargs.get("statement", "")
            if "hub_score" in sql:
                return _mock_response(
                    rows=[["id-1", 0.20], ["id-2", 0.05], ["id-3", 0.90]],
                    columns=["page_id", "hub_score"],
                )
            return _mock_response([])  # _log INSERT

        ws.statement_execution.execute_statement.side_effect = handler
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        results = wiki.search("q", rerank_with_pagerank=True)
        assert [r["page_id"] for r in results] == ["id-1", "id-3", "id-2"]

    def test_log_records_reranked_flag(self):
        ws = MagicMock()
        ws.vector_search_indexes.query_index.return_value = self._vs_resp(
            [["id-1", "topics/foo", "Foo", "concept", "c", "[]", "1"]],
            ["page_id", "path", "title", "page_type", "content_text", "tags", "version"],
        )
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[["id-1", 0.5]], columns=["page_id", "hub_score"],
        )
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.search("q", rerank_with_pagerank=True)
        log_calls = [c.kwargs["statement"]
                     for c in ws.statement_execution.execute_statement.call_args_list
                     if "wiki_log" in c.kwargs["statement"]]
        assert any('"reranked": true' in s.lower() or '"reranked":true' in s.lower()
                   or '"reranked": true' in s for s in log_calls), (
            f"reranked flag not in log details; calls: {log_calls}"
        )


class TestCommitEdgesBiTemporal:
    """Track 1 (v0.6.0): edges carry validity intervals.

    commit_edges no longer does a content-update MERGE. Edges are
    append-only: a new edge for an existing (src, dst, link_type) closes
    the old row's valid_until and inserts a new row. This matches
    Graphiti's bi-temporal model on a Delta substrate.
    """

    def test_new_edges_get_valid_from_default_and_null_valid_until(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 0.9, "origin": "auto-vs"},
        ])
        statements = [c.kwargs["statement"]
                       for c in ws.statement_execution.execute_statement.call_args_list]
        insert_sql = "\n".join(statements)
        # New row must initialise valid_from = current_timestamp()
        assert "valid_from" in insert_sql
        assert "current_timestamp()" in insert_sql
        # valid_until is left NULL (currently valid)
        assert "valid_until" in insert_sql

    def test_supersede_closes_previous_validity_window(self):
        # When a new edge for an existing (src, dst, link_type) lands, the
        # prior open row must get valid_until = current_timestamp() so the
        # graph reads correctly point at the new row.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 0.95, "origin": "manual"},
        ])
        sql = "\n".join(c.kwargs["statement"]
                        for c in ws.statement_execution.execute_statement.call_args_list)
        assert "UPDATE" in sql
        assert "valid_until = current_timestamp()" in sql
        # The close-out targets currently-open rows for the same key.
        assert "valid_until IS NULL" in sql


class TestCommitEdgesAcceptsValidFromValidUntil:
    """Real bi-temporal: caller-supplied event times, distinct from
    transaction time (`created_at`). Without this, every "valid_from" was
    just current_timestamp() and the model was uni-temporal in disguise.
    """

    def test_caller_supplied_valid_from_is_used_in_insert(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 1.0, "origin": "manual",
             "valid_from": "2020-01-01T00:00:00",
             "valid_until": "2023-06-01T00:00:00"},
        ])
        statements = [c.kwargs["statement"]
                       for c in ws.statement_execution.execute_statement.call_args_list]
        insert_sql = next(s for s in statements if s.strip().startswith("INSERT"))
        assert "2020-01-01T00:00:00" in insert_sql
        assert "2023-06-01T00:00:00" in insert_sql
        # Confirm the literals are typed as TIMESTAMP — bare strings would
        # silently truncate or fail on Databricks.
        assert "TIMESTAMP '2020-01-01T00:00:00'" in insert_sql

    def test_caller_unspecified_falls_back_to_current_timestamp(self):
        # Default behavior unchanged: a commit_edges call without valid_from
        # still records "valid since now, currently valid." Backward-compat.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 1.0, "origin": "manual"},
        ])
        statements = [c.kwargs["statement"]
                       for c in ws.statement_execution.execute_statement.call_args_list]
        insert_sql = next(s for s in statements if s.strip().startswith("INSERT"))
        assert "current_timestamp()" in insert_sql

    def test_supersede_uses_new_edges_valid_from_as_old_valid_until(self):
        # Continuous validity intervals: when a new edge backdates to T, the
        # prior open row should be closed at T (not at current_timestamp()),
        # so there's no gap and no overlap in the timeline.
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 1.0, "origin": "manual",
             "valid_from": "2023-06-01T00:00:00"},
        ])
        statements = [c.kwargs["statement"]
                       for c in ws.statement_execution.execute_statement.call_args_list]
        update_sql = next(s for s in statements if s.strip().startswith("UPDATE"))
        # The close-out timestamp must match the new edge's valid_from,
        # so consecutive facts have no gap.
        assert "valid_until = TIMESTAMP '2023-06-01T00:00:00'" in update_sql

    def test_supersede_default_close_time_is_current_when_no_valid_from(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.commit_edges([
            {"source_page_id": "a", "target_page_id": "b",
             "link_type": "related", "confidence": 1.0, "origin": "manual"},
        ])
        statements = [c.kwargs["statement"]
                       for c in ws.statement_execution.execute_statement.call_args_list]
        update_sql = next(s for s in statements if s.strip().startswith("UPDATE"))
        assert "valid_until = current_timestamp()" in update_sql


class TestGraphNeighborsFiltersValid:
    """Reads only return currently-valid edges by default."""

    def _setup(self, ws):
        page_row = ["page-a", "topics/a", "A", "concept", "c", [], "agent", "t", "t", 1]
        page_cols = ["page_id", "path", "title", "page_type", "content_text",
                     "tags", "created_by", "created_at", "updated_at", "version"]
        neighbor_cols = ["source_page_id", "target_page_id", "target_path",
                         "target_title", "link_type", "confidence", "origin", "hop"]

        def handler(**kwargs):
            sql = kwargs["statement"].strip()
            if sql.startswith("SELECT page_id, path, title"):
                return _mock_response([page_row], columns=page_cols)
            return _mock_response([], columns=neighbor_cols)

        ws.statement_execution.execute_statement.side_effect = handler

    def test_graph_neighbors_filters_to_currently_valid(self):
        ws = MagicMock()
        self._setup(ws)
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.graph_neighbors("topics/a", depth=1)
        # Find the BFS query (it joins LINKS_TABLE)
        joins = [c.kwargs["statement"]
                 for c in ws.statement_execution.execute_statement.call_args_list
                 if "links l" in c.kwargs["statement"].lower()
                 or "links " in c.kwargs["statement"].lower()]
        assert any("valid_until IS NULL" in s for s in joins), (
            f"no neighbor SQL filters by valid_until IS NULL; got: {joins}"
        )


class TestGraphNeighborsAt:
    """`graph_neighbors_at(path, at_timestamp)` returns the state of the
    graph as of a specific timestamp — the bi-temporal point query."""

    def test_filters_by_validity_interval_for_given_timestamp(self):
        ws = MagicMock()
        page_row = ["page-a", "topics/a", "A", "concept", "c", [], "agent", "t", "t", 1]
        page_cols = ["page_id", "path", "title", "page_type", "content_text",
                     "tags", "created_by", "created_at", "updated_at", "version"]

        def handler(**kwargs):
            sql = kwargs["statement"].strip()
            if sql.startswith("SELECT page_id, path, title"):
                return _mock_response([page_row], columns=page_cols)
            return _mock_response([], columns=["source_page_id", "target_page_id",
                "target_path", "target_title", "link_type", "confidence",
                "origin", "hop"])

        ws.statement_execution.execute_statement.side_effect = handler
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        wiki.graph_neighbors_at("topics/a", at_timestamp="2026-01-15T12:00:00", depth=1)
        # The BFS query is the one with LINKS_TABLE join.
        joins = [c.kwargs["statement"]
                 for c in ws.statement_execution.execute_statement.call_args_list
                 if "links l" in c.kwargs["statement"].lower()
                 or "links " in c.kwargs["statement"].lower()]
        assert joins, "expected at least one neighbor join SQL"
        sql = joins[0]
        assert "'2026-01-15T12:00:00'" in sql
        assert "valid_from" in sql
        assert "valid_until" in sql


class TestLinkHistory:
    """`link_history(src_path, dst_path)` returns the full chronological
    trace of edge versions between two pages."""

    def test_returns_versions_ordered_by_valid_from(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response(
            rows=[
                ["related", 0.9, "auto-vs", "2026-01-01", "2026-02-01"],
                ["related", 0.95, "manual",   "2026-02-01", None],
            ],
            columns=["link_type", "confidence", "origin", "valid_from", "valid_until"],
        )
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        history = wiki.link_history("topics/a", "topics/b")
        assert len(history) == 2
        assert history[0]["confidence"] == 0.9
        assert history[1]["valid_until"] is None
        sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
        assert "ORDER BY valid_from" in sql

    def test_empty_when_no_versions(self):
        ws = MagicMock()
        ws.statement_execution.execute_statement.return_value = _mock_response([])
        wiki = WikiClient(warehouse_id="wh", workspace_client=ws)
        assert wiki.link_history("topics/a", "topics/b") == []


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
