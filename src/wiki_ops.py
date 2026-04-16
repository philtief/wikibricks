"""WikiBricks: Delta + Vector Search wiki store operations for AI agents."""

import json

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
            page_id      STRING        DEFAULT uuid(),
            path         STRING        NOT NULL,
            path_depth   INT           GENERATED ALWAYS AS (size(split(path, '/'))),
            title        STRING        NOT NULL,
            page_type    STRING        NOT NULL,
            content      VARIANT       NOT NULL,
            content_text STRING        GENERATED ALWAYS AS (
                             concat(content:summary::STRING, ' ', content:body::STRING)),
            tags         ARRAY<STRING>,
            created_by   STRING        NOT NULL,
            created_at   TIMESTAMP     DEFAULT current_timestamp(),
            updated_at   TIMESTAMP     DEFAULT current_timestamp(),
            version      INT           NOT NULL DEFAULT 1
        )
        USING DELTA
        TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
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
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {LINKS_TABLE} (
            source_page_id  STRING  NOT NULL,
            target_page_id  STRING  NOT NULL,
            link_type       STRING  NOT NULL DEFAULT 'related',
            created_at      TIMESTAMP DEFAULT current_timestamp()
        )
        USING DELTA
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
        tags = {tags_sql},
        created_by = '{created_by}',
        updated_at = current_timestamp(),
        version = target.version + 1
    WHEN NOT MATCHED THEN INSERT
        (path, title, page_type, content, tags, created_by, version)
    VALUES ('{path}', '{title}', '{page_type}',
            PARSE_JSON('{content_escaped}'), {tags_sql}, '{created_by}', 1)
    """

    return [archive_sql, merge_sql]


def search_query(query_text, mode="HYBRID", num_results=5):
    """Return kwargs for vector_search_indexes.query_index.

    Args:
        query_text: The search query.
        mode: One of "ANN", "KEYWORD", "HYBRID".
        num_results: Max results to return.
    """
    if mode not in ("ANN", "KEYWORD", "HYBRID"):
        raise ValueError(f"Invalid search mode: {mode}. Must be ANN, KEYWORD, or HYBRID.")

    kwargs = {
        "index_name": VS_INDEX,
        "columns": ["page_id", "path", "title", "page_type", "content", "tags", "version"],
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
    """Return the spec for creating the Vector Search index."""
    return {
        "name": VS_INDEX,
        "endpoint_name": VS_ENDPOINT,
        "primary_key": "page_id",
        "index_type": "DELTA_SYNC",
        "delta_sync_index_spec": {
            "source_table": PAGES_TABLE,
            "pipeline_type": "TRIGGERED",
            "embedding_source_columns": [
                {
                    "name": "content_text",
                    "embedding_model_endpoint_name": EMBEDDING_MODEL,
                }
            ],
            "columns_to_sync": [
                "page_id",
                "path",
                "title",
                "page_type",
                "content",
                "tags",
                "version",
            ],
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
