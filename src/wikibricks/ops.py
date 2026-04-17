"""WikiBricks: Delta + Vector Search wiki store operations for AI agents."""

import json

from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EmbeddingSourceColumn,
    PipelineType,
    VectorIndexType,
)

CATALOG = "agent_marketplace_catalog"
SCHEMA = "wiki"
PAGES_TABLE = f"{CATALOG}.{SCHEMA}.pages"
HISTORY_TABLE = f"{CATALOG}.{SCHEMA}.pages_history"
LINKS_TABLE = f"{CATALOG}.{SCHEMA}.links"
VS_INDEX = f"{CATALOG}.{SCHEMA}.pages_index"
VS_ENDPOINT = "wiki-vs-endpoint"
EMBEDDING_MODEL = "databricks-bge-large-en"


def create_tables_sql():
    """Return SQL statements to create the wiki tables."""
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {PAGES_TABLE} (
            page_id      STRING        NOT NULL,
            path         STRING        NOT NULL,
            path_depth   INT           GENERATED ALWAYS AS (size(split(path, '/'))),
            title        STRING        NOT NULL,
            page_type    STRING        NOT NULL,
            content      VARIANT       NOT NULL,
            content_text STRING,
            tags         ARRAY<STRING>,
            created_by   STRING        NOT NULL,
            created_at   TIMESTAMP     DEFAULT current_timestamp(),
            updated_at   TIMESTAMP     DEFAULT current_timestamp(),
            version      INT           NOT NULL DEFAULT 1
        )
        USING DELTA
        TBLPROPERTIES (
            'delta.enableChangeDataFeed' = 'true',
            'delta.feature.allowColumnDefaults' = 'supported'
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
            page_id      STRING        NOT NULL,
            path         STRING        NOT NULL,
            title        STRING        NOT NULL,
            page_type    STRING        NOT NULL,
            content      VARIANT       NOT NULL,
            content_text STRING,
            tags         ARRAY<STRING>,
            created_by   STRING        NOT NULL,
            created_at   TIMESTAMP     NOT NULL,
            version      INT           NOT NULL,
            archived_at  TIMESTAMP     DEFAULT current_timestamp()
        )
        USING DELTA
        TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {LINKS_TABLE} (
            source_page_id  STRING  NOT NULL,
            target_page_id  STRING  NOT NULL,
            link_type       STRING  NOT NULL DEFAULT 'related',
            created_at      TIMESTAMP DEFAULT current_timestamp()
        )
        USING DELTA
        TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
        """,
    ]


def create_schema_sql():
    """Return SQL to create the wiki schema."""
    return f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}"


def write_page_sql(path, title, page_type, content_json, created_by, tags=None):
    """Return SQL statements for the archive-then-MERGE write pattern.

    Returns a list of two SQL statements:
    1. Archive current version to history (no-op if page doesn't exist)
    2. MERGE into current table (insert or update)
    """
    tags_sql = f"ARRAY({','.join(repr(t) for t in tags)})" if tags else "ARRAY()"
    content_escaped = json.dumps(content_json) if isinstance(content_json, dict) else content_json
    content_escaped = content_escaped.replace("'", "\\'")

    archive_sql = f"""
    INSERT INTO {HISTORY_TABLE}
    (page_id, path, title, page_type, content, content_text, tags, created_by, created_at, version)
    SELECT page_id, path, title, page_type, content, content_text, tags, created_by, created_at, version
    FROM {PAGES_TABLE}
    WHERE path = '{path}'
    """

    merge_sql = f"""
    MERGE INTO {PAGES_TABLE} AS target
    USING (SELECT '{path}' AS path) AS source
    ON target.path = source.path
    WHEN MATCHED THEN UPDATE SET
        title = '{title}',
        page_type = '{page_type}',
        content = PARSE_JSON('{content_escaped}'),
        content_text = concat(
            PARSE_JSON('{content_escaped}'):summary::STRING, ' ',
            PARSE_JSON('{content_escaped}'):body::STRING),
        tags = {tags_sql},
        created_by = '{created_by}',
        updated_at = current_timestamp(),
        version = target.version + 1
    WHEN NOT MATCHED THEN INSERT
        (page_id, path, title, page_type, content, content_text, tags, created_by, version)
    VALUES (uuid(), '{path}', '{title}', '{page_type}',
            PARSE_JSON('{content_escaped}'),
            concat(
                PARSE_JSON('{content_escaped}'):summary::STRING, ' ',
                PARSE_JSON('{content_escaped}'):body::STRING),
            {tags_sql}, '{created_by}', 1)
    """

    return [archive_sql, merge_sql]


def search_query(query_text, mode="HYBRID", num_results=5):
    """Return kwargs for vector_search_indexes.query_index.

    Args:
        query_text: The search query.
        mode: One of "ANN", "FULL_TEXT", "HYBRID".
        num_results: Max results to return.
    """
    if mode not in ("ANN", "FULL_TEXT", "HYBRID"):
        raise ValueError(f"Invalid search mode: {mode}. Must be ANN, FULL_TEXT, or HYBRID.")

    kwargs = {
        "index_name": VS_INDEX,
        "columns": ["page_id", "path", "title", "page_type", "content_text", "tags", "version"],
        "query_text": query_text,
        "num_results": num_results,
    }
    if mode != "ANN":
        kwargs["query_type"] = mode

    return kwargs


def read_page_sql(path):
    """Return SQL to read a page by path with its cross-references."""
    return f"""
    SELECT p.page_id, p.path, p.title, p.page_type, p.content, p.tags,
           p.created_by, p.created_at, p.updated_at, p.version,
           l.target_page_id, l.link_type,
           t.path AS target_path, t.title AS target_title
    FROM {PAGES_TABLE} p
    LEFT JOIN {LINKS_TABLE} l ON p.page_id = l.source_page_id
    LEFT JOIN {PAGES_TABLE} t ON l.target_page_id = t.page_id
    WHERE p.path = '{path}'
    """


def read_subtree_sql(path_prefix):
    """Return SQL to read all current pages under a path prefix."""
    return f"""
    SELECT page_id, path, title, page_type,
           content:summary::STRING AS summary, tags, version
    FROM {PAGES_TABLE}
    WHERE path = '{path_prefix}' OR path LIKE '{path_prefix}/%'
    ORDER BY path_depth, title
    """


def version_history_sql(path):
    """Return SQL to read version history for a page."""
    return f"""
    SELECT version, created_by, created_at, content:summary::STRING AS summary
    FROM {HISTORY_TABLE}
    WHERE path = '{path}'
    ORDER BY version DESC
    """


def create_vs_index_spec():
    """Return kwargs for ``w.vector_search_indexes.create_index()``."""
    return {
        "name": VS_INDEX,
        "endpoint_name": VS_ENDPOINT,
        "primary_key": "page_id",
        "index_type": VectorIndexType.DELTA_SYNC,
        "delta_sync_index_spec": DeltaSyncVectorIndexSpecRequest(
            source_table=PAGES_TABLE,
            pipeline_type=PipelineType.TRIGGERED,
            embedding_source_columns=[
                EmbeddingSourceColumn(
                    name="content_text",
                    embedding_model_endpoint_name=EMBEDDING_MODEL,
                )
            ],
            columns_to_sync=[
                "page_id",
                "path",
                "title",
                "page_type",
                "content_text",
                "tags",
                "version",
            ],
        ),
    }


def create_uc_functions_sql(warehouse_id):
    """Return SQL statements to create the wiki UC read functions.

    Returns [fn_wiki_search, fn_wiki_read, fn_wiki_history].
    Write is handled by a custom agent tool (UC functions can't do DML).
    """
    fn_search = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_search(
        question STRING COMMENT 'The search query text',
        mode STRING DEFAULT 'HYBRID' COMMENT 'Search mode: ANN (semantic), FULL_TEXT (exact), or HYBRID (both)'
    )
    RETURNS STRING
    COMMENT 'Search wiki pages by semantic similarity, keyword match, or hybrid. Returns JSON array of matching pages.'
    RETURN (
        SELECT to_json(collect_list(struct(
            page_id, path, title, page_type,
            content_text, tags, version
        )))
        FROM {PAGES_TABLE}
        WHERE content_text LIKE concat('%', question, '%')
           OR title LIKE concat('%', question, '%')
    )
    """

    fn_read = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_read(
        page_path STRING COMMENT 'The wiki page path, e.g. topics/my-topic'
    )
    RETURNS STRING
    COMMENT 'Read a wiki page by path. Returns JSON with full page content.'
    RETURN (
        SELECT first(to_json(struct(
            page_id, path, title, page_type, content, tags,
            created_by, created_at, updated_at, version
        )))
        FROM {PAGES_TABLE}
        WHERE path = page_path
    )
    """

    fn_history = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_history(
        page_path STRING COMMENT 'The wiki page path to get history for'
    )
    RETURNS STRING
    COMMENT 'Get version history for a wiki page. Returns JSON array of past versions.'
    RETURN (
        SELECT to_json(collect_list(struct(
            version, created_by, created_at, summary
        )))
        FROM (
            SELECT version, created_by, created_at,
                   content:summary::STRING AS summary
            FROM {HISTORY_TABLE}
            WHERE path = page_path
            ORDER BY version DESC
        )
    )
    """

    return [fn_search, fn_read, fn_history]


def seed_pages():
    """Return a list of generic sample wiki pages for testing the wiki store."""
    return [
        {
            "path": "topics/getting-started",
            "title": "Getting Started with WikiBricks",
            "page_type": "concept",
            "content": {
                "summary": "Introduction to the WikiBricks wiki memory system.",
                "body": (
                    "WikiBricks provides a structured knowledge store backed by Delta tables "
                    "and Vector Search. Pages are organized in a path hierarchy and support "
                    "full-text, semantic, and hybrid search. Each page has a summary, body, "
                    "tags, and automatic version history."
                ),
            },
            "created_by": "setup",
            "tags": ["getting-started", "overview", "onboarding"],
        },
        {
            "path": "topics/architecture/overview",
            "title": "Architecture Overview",
            "page_type": "concept",
            "content": {
                "summary": "High-level architecture of the WikiBricks storage layer.",
                "body": (
                    "WikiBricks uses three Delta tables: pages (current state), pages_history "
                    "(archived versions), and links (page-to-page relationships). A Vector "
                    "Search index on the pages table enables semantic retrieval. Writes use "
                    "MERGE for upsert semantics and archive the previous version automatically."
                ),
            },
            "created_by": "setup",
            "tags": ["architecture", "delta", "vector-search"],
        },
        {
            "path": "guides/setup",
            "title": "Setup Guide",
            "page_type": "entity",
            "content": {
                "summary": "Step-by-step guide for deploying WikiBricks to a workspace.",
                "body": (
                    "Prerequisites: a Databricks workspace with Unity Catalog enabled and a "
                    "SQL warehouse. Steps: 1) Create the catalog and schema. 2) Run the table "
                    "creation DDL. 3) Create the Vector Search endpoint and index. 4) Seed "
                    "initial pages. 5) Verify with a search query."
                ),
            },
            "created_by": "setup",
            "tags": ["guide", "setup", "deployment"],
        },
        {
            "path": "guides/troubleshooting",
            "title": "Troubleshooting Common Issues",
            "page_type": "synthesis",
            "content": {
                "summary": "Solutions for frequently encountered problems.",
                "body": (
                    "Issue: search returns no results. Check that the Vector Search index has "
                    "synced after writing pages. Issue: PARSE_JSON fails. Ensure content JSON "
                    "does not contain unescaped backslashes or newlines. Issue: permission "
                    "denied. Grant USE CATALOG, USE SCHEMA, and SELECT to the service principal."
                ),
            },
            "created_by": "setup",
            "tags": ["guide", "troubleshooting", "faq"],
        },
        {
            "path": "comparisons/search-modes",
            "title": "Search Modes Comparison",
            "page_type": "comparison",
            "content": {
                "summary": "Comparison of ANN, full-text, and hybrid search modes.",
                "body": (
                    "ANN (approximate nearest neighbor): best for semantic similarity, uses "
                    "embedding vectors. Full-text: best for exact keyword matching and known "
                    "identifiers. Hybrid: combines both approaches and generally provides the "
                    "best results for natural-language queries. Default mode is HYBRID."
                ),
            },
            "created_by": "setup",
            "tags": ["comparison", "search", "vector-search"],
        },
    ]


def autoeval_config():
    """Return configuration for Vector Search AutoEval."""
    return {
        "index_name": VS_INDEX,
        "num_queries": 20,
        "metrics": {
            "recall": {"k": [3, 5, 10]},
            "ndcg": {"k": [3, 5, 10]},
            "precision": {"k": [3, 5]},
            "mrr": {"k": [5, 10]},
        },
    }


def add_link_sql(source_page_id, target_page_id, link_type="related"):
    """Return SQL to add a cross-reference link (idempotent via MERGE)."""
    if link_type not in ("related", "contradicts", "extends", "supersedes", "cites"):
        raise ValueError(f"Invalid link type: {link_type}")

    return f"""
    MERGE INTO {LINKS_TABLE} AS t
    USING (SELECT '{source_page_id}' AS src, '{target_page_id}' AS tgt, '{link_type}' AS lt) AS s
    ON t.source_page_id = s.src AND t.target_page_id = s.tgt AND t.link_type = s.lt
    WHEN NOT MATCHED THEN INSERT (source_page_id, target_page_id, link_type)
    VALUES (s.src, s.tgt, s.lt)
    """
