"""Tests for WikiBricks wiki_ops module."""

from wikibricks.ops import (
    CATALOG,
    HISTORY_TABLE,
    LINKS_TABLE,
    LOG_TABLE,
    PAGES_TABLE,
    SCHEMA,
    SCHEMA_VOLUME_PATH,
    SOURCES_TABLE,
    SOURCES_VOLUME,
    VS_ENDPOINT,
    VS_INDEX,
    add_link_sql,
    broken_links_sql,
    cdf_since_sql,
    create_index_view_sql,
    create_schema_sql,
    create_tables_sql,
    create_uc_functions_sql,
    create_vs_index_spec,
    duplicate_paths_sql,
    get_schema,
    ingest_source_sql,
    log_operation_sql,
    orphan_pages_sql,
    read_page_sql,
    read_subtree_sql,
    search_query,
    stale_pages_sql,
    version_history_sql,
    write_page_sql,
)


class TestConstants:
    def test_catalog_and_schema(self):
        assert CATALOG == "agent_marketplace_catalog"
        assert SCHEMA == "wiki"

    def test_table_names_follow_three_level_namespace(self):
        assert PAGES_TABLE == f"{CATALOG}.{SCHEMA}.pages"
        assert HISTORY_TABLE == f"{CATALOG}.{SCHEMA}.pages_history"
        assert LINKS_TABLE == f"{CATALOG}.{SCHEMA}.links"
        assert SOURCES_TABLE == f"{CATALOG}.{SCHEMA}.sources"
        assert LOG_TABLE == f"{CATALOG}.{SCHEMA}.wiki_log"

    def test_sources_volume_path(self):
        assert SOURCES_VOLUME == f"/Volumes/{CATALOG}/{SCHEMA}/sources"

    def test_vs_index_name(self):
        assert VS_INDEX == f"{CATALOG}.{SCHEMA}.pages_index"


class TestCreateTablesSql:
    def test_returns_five_statements(self):
        stmts = create_tables_sql()
        assert len(stmts) == 5

    def test_pages_table_has_required_columns(self):
        stmts = create_tables_sql()
        pages_sql = stmts[0].upper()
        for col in ["PAGE_ID", "PATH", "TITLE", "PAGE_TYPE", "CONTENT", "CONTENT_TEXT", "VERSION"]:
            assert col in pages_sql, f"Missing column {col} in pages table"

    def test_pages_table_has_variant_content(self):
        stmts = create_tables_sql()
        assert "VARIANT" in stmts[0]

    def test_pages_table_has_cdf_enabled(self):
        stmts = create_tables_sql()
        assert "enableChangeDataFeed" in stmts[0]

    def test_pages_table_has_generated_columns(self):
        stmts = create_tables_sql()
        pages_sql = stmts[0]
        assert "GENERATED ALWAYS AS" in pages_sql
        assert "path_depth" in pages_sql
        assert "content_text" in pages_sql

    def test_pages_table_has_source_ids(self):
        stmts = create_tables_sql()
        assert "source_ids" in stmts[0]
        assert "ARRAY<STRING>" in stmts[0]

    def test_pages_table_has_no_is_current_flag(self):
        """Current table should not have is_current — every row IS current."""
        stmts = create_tables_sql()
        assert "is_current" not in stmts[0].lower()

    def test_history_table_has_archived_at(self):
        stmts = create_tables_sql()
        history_sql = stmts[1]
        assert "archived_at" in history_sql

    def test_links_table_has_link_type(self):
        stmts = create_tables_sql()
        links_sql = stmts[2]
        assert "link_type" in links_sql

    def test_sources_table_has_required_columns(self):
        stmts = create_tables_sql()
        sources_sql = stmts[3].upper()
        for col in ["SOURCE_ID", "URI", "TITLE", "CONTENT_TEXT", "SOURCE_TYPE", "METADATA"]:
            assert col in sources_sql, f"Missing column {col} in sources table"

    def test_sources_table_has_variant_metadata(self):
        stmts = create_tables_sql()
        assert "VARIANT" in stmts[3]

    def test_log_table_has_required_columns(self):
        stmts = create_tables_sql()
        log_sql = stmts[4].upper()
        for col in ["LOG_ID", "OP_TYPE", "PATH", "QUERY", "DETAILS", "CREATED_BY"]:
            assert col in log_sql, f"Missing column {col} in log table"

    def test_log_table_has_op_type_comment(self):
        stmts = create_tables_sql()
        assert "promote" in stmts[4]

    def test_all_tables_use_delta(self):
        for stmt in create_tables_sql():
            assert "USING DELTA" in stmt


class TestCreateSchemaSql:
    def test_creates_schema(self):
        sql = create_schema_sql()
        assert f"{CATALOG}.{SCHEMA}" in sql
        assert "CREATE SCHEMA" in sql


class TestGetSchema:
    def test_returns_string(self):
        schema = get_schema()
        assert isinstance(schema, str)
        assert len(schema) > 100

    def test_contains_page_types(self):
        schema = get_schema()
        for pt in ("entity", "concept", "synthesis", "comparison"):
            assert pt in schema

    def test_contains_path_conventions(self):
        schema = get_schema()
        assert "topics/" in schema
        assert "guides/" in schema
        assert "_meta/" in schema

    def test_contains_link_types(self):
        schema = get_schema()
        for lt in ("related", "extends", "contradicts", "supersedes", "cites"):
            assert lt in schema

    def test_contains_content_structure(self):
        schema = get_schema()
        assert "summary" in schema
        assert "body" in schema

    def test_schema_volume_path(self):
        assert "WIKIBRICKS.MD" in SCHEMA_VOLUME_PATH


class TestWritePageSql:
    def test_returns_two_statements(self):
        stmts = write_page_sql("test/page", "Test", "concept", '{"summary":"s","body":"b"}', "agent")
        assert len(stmts) == 2

    def test_first_statement_archives_to_history(self):
        stmts = write_page_sql("test/page", "Test", "concept", '{"summary":"s","body":"b"}', "agent")
        archive_sql = stmts[0].upper()
        assert "INSERT INTO" in archive_sql
        assert "PAGES_HISTORY" in archive_sql

    def test_second_statement_is_merge(self):
        stmts = write_page_sql("test/page", "Test", "concept", '{"summary":"s","body":"b"}', "agent")
        merge_sql = stmts[1].upper()
        assert "MERGE INTO" in merge_sql
        assert "WHEN MATCHED" in merge_sql
        assert "WHEN NOT MATCHED" in merge_sql

    def test_merge_increments_version(self):
        stmts = write_page_sql("test/page", "Test", "concept", '{"summary":"s","body":"b"}', "agent")
        merge_sql = stmts[1]
        assert "version + 1" in merge_sql

    def test_merge_inserts_version_1_for_new_page(self):
        stmts = write_page_sql("test/page", "Test", "concept", '{"summary":"s","body":"b"}', "agent")
        merge_sql = stmts[1]
        # The NOT MATCHED branch should insert version 1
        assert "1)" in merge_sql

    def test_tags_included_when_provided(self):
        stmts = write_page_sql(
            "test/page", "Test", "concept", '{"summary":"s","body":"b"}', "agent", tags=["alpha", "beta"]
        )
        merge_sql = stmts[1]
        assert "alpha" in merge_sql
        assert "beta" in merge_sql

    def test_path_in_both_statements(self):
        stmts = write_page_sql("topics/example", "Example", "concept", "{}", "agent")
        for stmt in stmts:
            assert "topics/example" in stmt

    def test_content_dict_serialized(self):
        content = {"summary": "test summary", "body": "test body"}
        stmts = write_page_sql("test/page", "Test", "concept", content, "agent")
        merge_sql = stmts[1]
        assert "PARSE_JSON" in merge_sql

    def test_uses_parse_json(self):
        stmts = write_page_sql("test/page", "Test", "concept", '{"summary":"s"}', "agent")
        merge_sql = stmts[1]
        assert "PARSE_JSON" in merge_sql


class TestSearchQuery:
    def test_hybrid_mode(self):
        kwargs = search_query("example query", mode="HYBRID")
        assert kwargs["index_name"] == VS_INDEX
        assert kwargs["query_text"] == "example query"
        assert kwargs["query_type"] == "HYBRID"

    def test_full_text_mode(self):
        kwargs = search_query("CLM-4005", mode="FULL_TEXT")
        assert kwargs["query_type"] == "FULL_TEXT"

    def test_ann_mode_has_no_query_type(self):
        kwargs = search_query("example query", mode="ANN")
        assert "query_type" not in kwargs

    def test_default_num_results(self):
        kwargs = search_query("test")
        assert kwargs["num_results"] == 5

    def test_custom_num_results(self):
        kwargs = search_query("test", num_results=10)
        assert kwargs["num_results"] == 10

    def test_columns_include_content_text(self):
        kwargs = search_query("test")
        assert "content_text" in kwargs["columns"]
        assert "page_id" in kwargs["columns"]
        assert "path" in kwargs["columns"]

    def test_invalid_mode_raises(self):
        try:
            search_query("test", mode="INVALID")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "INVALID" in str(e)

    def test_no_is_current_filter(self):
        """Current table only has current pages — no filter needed."""
        kwargs = search_query("test")
        assert "filters" not in kwargs


class TestReadPageSql:
    def test_joins_links_and_targets(self):
        sql = read_page_sql("topics/example")
        assert "LEFT JOIN" in sql
        assert LINKS_TABLE in sql
        assert "target_path" in sql

    def test_filters_by_path(self):
        sql = read_page_sql("topics/example")
        assert "topics/example" in sql


class TestReadSubtreeSql:
    def test_uses_like_for_prefix(self):
        sql = read_subtree_sql("topics/example")
        assert "LIKE 'topics/example/%'" in sql

    def test_includes_exact_path(self):
        sql = read_subtree_sql("topics/example")
        assert "path = 'topics/example'" in sql

    def test_orders_by_depth(self):
        sql = read_subtree_sql("topics")
        assert "path_depth" in sql


class TestVersionHistorySql:
    def test_queries_history_table(self):
        sql = version_history_sql("topics/example")
        assert HISTORY_TABLE in sql

    def test_orders_by_version_desc(self):
        sql = version_history_sql("test")
        assert "ORDER BY version DESC" in sql


class TestCreateVsIndexSpec:
    def test_spec_structure(self):
        spec = create_vs_index_spec()
        assert spec["name"] == VS_INDEX
        assert spec["endpoint_name"] == VS_ENDPOINT
        assert spec["primary_key"] == "page_id"
        assert spec["index_type"].value == "DELTA_SYNC"

    def test_delta_sync_spec(self):
        spec = create_vs_index_spec()
        ds = spec["delta_sync_index_spec"]
        assert ds.source_table == PAGES_TABLE
        assert ds.pipeline_type.value == "TRIGGERED"

    def test_embedding_config(self):
        spec = create_vs_index_spec()
        emb = spec["delta_sync_index_spec"].embedding_source_columns[0]
        assert emb.name == "content_text"
        assert "bge" in emb.embedding_model_endpoint_name

    def test_columns_to_sync_no_is_current(self):
        """No is_current column — source table only has current pages."""
        spec = create_vs_index_spec()
        cols = spec["delta_sync_index_spec"].columns_to_sync
        assert "is_current" not in cols
        assert "page_id" in cols
        assert "path" in cols
        assert "content_text" in cols


class TestCreateUcFunctionsSql:
    def test_returns_seven_functions(self):
        stmts = create_uc_functions_sql("warehouse-id-123")
        assert len(stmts) == 7

    def test_fn_wiki_search(self):
        stmts = create_uc_functions_sql("wh-123")
        search_fn = stmts[0]
        assert "fn_wiki_search" in search_fn
        assert "CREATE OR REPLACE FUNCTION" in search_fn
        assert "RETURNS STRING" in search_fn
        assert "question STRING" in search_fn
        assert "mode STRING" in search_fn

    def test_fn_wiki_read(self):
        stmts = create_uc_functions_sql("wh-123")
        read_fn = stmts[1]
        assert "fn_wiki_read" in read_fn
        assert "page_path STRING" in read_fn
        assert PAGES_TABLE in read_fn

    def test_fn_wiki_history(self):
        stmts = create_uc_functions_sql("wh-123")
        history_fn = stmts[2]
        assert "fn_wiki_history" in history_fn
        assert "page_path STRING" in history_fn
        assert HISTORY_TABLE in history_fn
        assert history_fn.count("SELECT") >= 2

    def test_fn_wiki_log(self):
        stmts = create_uc_functions_sql("wh-123")
        log_fn = stmts[3]
        assert "fn_wiki_log" in log_fn
        assert "CREATE OR REPLACE FUNCTION" in log_fn
        assert "num_entries INT" in log_fn
        assert LOG_TABLE in log_fn
        assert log_fn.count("SELECT") >= 2

    def test_fn_wiki_index(self):
        stmts = create_uc_functions_sql("wh-123")
        index_fn = stmts[4]
        assert "fn_wiki_index" in index_fn
        assert "CREATE OR REPLACE FUNCTION" in index_fn
        assert PAGES_TABLE in index_fn
        assert "summary" in index_fn

    def test_all_functions_use_catalog_schema(self):
        stmts = create_uc_functions_sql("wh-123")
        for stmt in stmts:
            assert f"{CATALOG}.{SCHEMA}" in stmt

    def test_no_dml_write_function(self):
        """No UC function does DML writes — write_help only returns docs."""
        stmts = create_uc_functions_sql("wh-123")
        write_help_fn = stmts[6]
        assert "fn_wiki_write_help" in write_help_fn
        assert "INSERT" not in write_help_fn
        assert "MERGE" not in write_help_fn

    def test_search_fn_has_comment_about_modes(self):
        stmts = create_uc_functions_sql("wh-123")
        search_fn = stmts[0]
        assert "FULL_TEXT" in search_fn or "full_text" in search_fn

    def test_fn_wiki_schema(self):
        stmts = create_uc_functions_sql("wh-123")
        schema_fn = stmts[5]
        assert "fn_wiki_schema" in schema_fn
        assert "CREATE OR REPLACE FUNCTION" in schema_fn
        assert "Page Types" in schema_fn or "page_type" in schema_fn.lower()

    def test_fn_wiki_write_help(self):
        stmts = create_uc_functions_sql("wh-123")
        help_fn = stmts[6]
        assert "fn_wiki_write_help" in help_fn
        assert "WikiClient" in help_fn
        assert "write_page" in help_fn


class TestAddLinkSql:
    def test_uses_merge_for_idempotency(self):
        sql = add_link_sql("page-1", "page-2", "related")
        assert "MERGE INTO" in sql

    def test_includes_link_type(self):
        sql = add_link_sql("page-1", "page-2", "contradicts")
        assert "contradicts" in sql

    def test_invalid_link_type_raises(self):
        try:
            add_link_sql("page-1", "page-2", "invalid_type")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "invalid_type" in str(e)

    def test_valid_link_types(self):
        for lt in ("related", "contradicts", "extends", "supersedes", "cites"):
            sql = add_link_sql("a", "b", lt)
            assert lt in sql


class TestIngestSourceSql:
    def test_inserts_into_sources_table(self):
        sql = ingest_source_sql("https://example.com/doc", "Example Doc", "Some content", "url")
        assert "INSERT INTO" in sql
        assert SOURCES_TABLE in sql

    def test_includes_all_fields(self):
        sql = ingest_source_sql("https://example.com", "Title", "Content", "url")
        assert "https://example.com" in sql
        assert "Title" in sql
        assert "Content" in sql
        assert "url" in sql

    def test_null_optional_fields(self):
        sql = ingest_source_sql("https://example.com")
        assert "NULL" in sql
        assert "https://example.com" in sql

    def test_default_source_type(self):
        sql = ingest_source_sql("https://example.com")
        assert "manual" in sql


class TestLogOperationSql:
    def test_inserts_into_log_table(self):
        sql = log_operation_sql("write", path="topics/test")
        assert "INSERT INTO" in sql
        assert LOG_TABLE in sql

    def test_includes_op_type(self):
        sql = log_operation_sql("search", query="test query")
        assert "search" in sql
        assert "test query" in sql

    def test_null_optional_fields(self):
        sql = log_operation_sql("read")
        assert sql.count("NULL") >= 2

    def test_default_created_by(self):
        sql = log_operation_sql("write")
        assert "agent" in sql

    def test_custom_created_by(self):
        sql = log_operation_sql("promote", created_by="chat")
        assert "chat" in sql

    def test_all_op_types_accepted(self):
        for op in ("write", "search", "read", "ingest", "lint", "promote"):
            sql = log_operation_sql(op)
            assert op in sql


class TestCreateIndexViewSql:
    def test_creates_view(self):
        sql = create_index_view_sql()
        assert "CREATE OR REPLACE VIEW" in sql
        assert f"{CATALOG}.{SCHEMA}.wiki_index" in sql

    def test_selects_from_pages(self):
        sql = create_index_view_sql()
        assert PAGES_TABLE in sql

    def test_includes_summary_extraction(self):
        sql = create_index_view_sql()
        assert "summary" in sql.lower()

    def test_orders_by_path(self):
        sql = create_index_view_sql()
        assert "ORDER BY path" in sql


class TestCdfSinceSql:
    def test_queries_table_changes(self):
        sql = cdf_since_sql(PAGES_TABLE, "2026-01-01T00:00:00")
        assert "table_changes" in sql
        assert PAGES_TABLE in sql

    def test_includes_timestamp(self):
        sql = cdf_since_sql(PAGES_TABLE, "2026-04-01T00:00:00")
        assert "2026-04-01T00:00:00" in sql

    def test_filters_change_types(self):
        sql = cdf_since_sql(PAGES_TABLE, "2026-01-01")
        assert "insert" in sql
        assert "update_postimage" in sql


class TestOrphanPagesSql:
    def test_joins_pages_and_links(self):
        sql = orphan_pages_sql()
        assert PAGES_TABLE in sql
        assert LINKS_TABLE in sql
        assert "LEFT JOIN" in sql

    def test_excludes_meta_pages(self):
        sql = orphan_pages_sql()
        assert "_meta/%" in sql

    def test_filters_null_targets(self):
        sql = orphan_pages_sql()
        assert "IS NULL" in sql


class TestStalePagesSql:
    def test_default_is_90_days(self):
        sql = stale_pages_sql()
        assert "90" in sql
        assert "INTERVAL" in sql

    def test_custom_window(self):
        sql = stale_pages_sql(days=30)
        assert "30" in sql

    def test_queries_pages_table(self):
        sql = stale_pages_sql()
        assert PAGES_TABLE in sql

    def test_excludes_pages_with_recent_hits(self):
        sql = stale_pages_sql()
        assert LOG_TABLE in sql
        assert "search" in sql

    def test_excludes_meta_pages(self):
        sql = stale_pages_sql()
        assert "_meta/%" in sql


class TestDuplicatePathsSql:
    def test_groups_by_lowercase_path(self):
        sql = duplicate_paths_sql()
        assert "LOWER" in sql
        assert "GROUP BY" in sql

    def test_filters_count_greater_than_one(self):
        sql = duplicate_paths_sql()
        assert "COUNT(*)" in sql
        assert "> 1" in sql

    def test_queries_pages_table(self):
        sql = duplicate_paths_sql()
        assert PAGES_TABLE in sql


class TestBrokenLinksSql:
    def test_joins_links_and_pages(self):
        sql = broken_links_sql()
        assert LINKS_TABLE in sql
        assert PAGES_TABLE in sql

    def test_filters_missing_targets(self):
        sql = broken_links_sql()
        assert "IS NULL" in sql
