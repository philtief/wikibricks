"""Tests for WikiBricks wiki_ops module."""

from wiki_ops import (
    CATALOG,
    HISTORY_TABLE,
    LINKS_TABLE,
    PAGES_TABLE,
    SCHEMA,
    VS_ENDPOINT,
    VS_INDEX,
    add_link_sql,
    create_schema_sql,
    create_tables_sql,
    create_uc_functions_sql,
    create_vs_index_spec,
    read_page_sql,
    read_subtree_sql,
    search_query,
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

    def test_vs_index_name(self):
        assert VS_INDEX == f"{CATALOG}.{SCHEMA}.pages_index"


class TestCreateTablesSql:
    def test_returns_three_statements(self):
        stmts = create_tables_sql()
        assert len(stmts) == 3

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

    def test_all_tables_use_delta(self):
        for stmt in create_tables_sql():
            assert "USING DELTA" in stmt


class TestCreateSchemaSql:
    def test_creates_schema(self):
        sql = create_schema_sql()
        assert f"{CATALOG}.{SCHEMA}" in sql
        assert "CREATE SCHEMA" in sql


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
            "test/page", "Test", "concept", '{"summary":"s","body":"b"}', "agent", tags=["fraud", "claims"]
        )
        merge_sql = stmts[1]
        assert "fraud" in merge_sql
        assert "claims" in merge_sql

    def test_path_in_both_statements(self):
        stmts = write_page_sql("claims/fraud/patterns", "Fraud", "concept", "{}", "agent")
        for stmt in stmts:
            assert "claims/fraud/patterns" in stmt

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
        kwargs = search_query("fraud patterns", mode="HYBRID")
        assert kwargs["index_name"] == VS_INDEX
        assert kwargs["query_text"] == "fraud patterns"
        assert kwargs["query_type"] == "HYBRID"

    def test_keyword_mode(self):
        kwargs = search_query("CLM-4005", mode="KEYWORD")
        assert kwargs["query_type"] == "KEYWORD"

    def test_ann_mode_has_no_query_type(self):
        kwargs = search_query("fraud patterns", mode="ANN")
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
        sql = read_page_sql("claims/fraud/patterns")
        assert "LEFT JOIN" in sql
        assert LINKS_TABLE in sql
        assert "target_path" in sql

    def test_filters_by_path(self):
        sql = read_page_sql("claims/fraud/patterns")
        assert "claims/fraud/patterns" in sql


class TestReadSubtreeSql:
    def test_uses_like_for_prefix(self):
        sql = read_subtree_sql("claims/fraud")
        assert "LIKE 'claims/fraud/%'" in sql

    def test_includes_exact_path(self):
        sql = read_subtree_sql("claims/fraud")
        assert "path = 'claims/fraud'" in sql

    def test_orders_by_depth(self):
        sql = read_subtree_sql("claims")
        assert "path_depth" in sql


class TestVersionHistorySql:
    def test_queries_history_table(self):
        sql = version_history_sql("claims/fraud/patterns")
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
    def test_returns_four_functions(self):
        stmts = create_uc_functions_sql("warehouse-id-123")
        assert len(stmts) == 4

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

    def test_fn_wiki_write(self):
        stmts = create_uc_functions_sql("wh-123")
        write_fn = stmts[2]
        assert "fn_wiki_write" in write_fn
        assert "page_path STRING" in write_fn
        assert "title STRING" in write_fn
        assert "content_json STRING" in write_fn
        assert PAGES_TABLE in write_fn

    def test_fn_wiki_history(self):
        stmts = create_uc_functions_sql("wh-123")
        history_fn = stmts[3]
        assert "fn_wiki_history" in history_fn
        assert "page_path STRING" in history_fn
        assert HISTORY_TABLE in history_fn

    def test_all_functions_use_catalog_schema(self):
        stmts = create_uc_functions_sql("wh-123")
        for stmt in stmts:
            assert f"{CATALOG}.{SCHEMA}" in stmt

    def test_write_fn_uses_merge_pattern(self):
        stmts = create_uc_functions_sql("wh-123")
        write_fn = stmts[2]
        assert "MERGE" in write_fn.upper()

    def test_write_fn_archives_to_history(self):
        stmts = create_uc_functions_sql("wh-123")
        write_fn = stmts[2]
        assert HISTORY_TABLE in write_fn

    def test_search_fn_has_comment_about_modes(self):
        stmts = create_uc_functions_sql("wh-123")
        search_fn = stmts[0]
        assert "KEYWORD" in search_fn or "keyword" in search_fn


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
