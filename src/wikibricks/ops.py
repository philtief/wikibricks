"""WikiBricks: Delta + Vector Search wiki store operations for AI agents."""

import json
from pathlib import Path

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
SOURCES_TABLE = f"{CATALOG}.{SCHEMA}.sources"
LOG_TABLE = f"{CATALOG}.{SCHEMA}.wiki_log"
VS_INDEX = f"{CATALOG}.{SCHEMA}.pages_index"
VS_ENDPOINT = "wiki-vs-endpoint"
EMBEDDING_MODEL = "databricks-bge-large-en"
SOURCES_VOLUME = f"/Volumes/{CATALOG}/{SCHEMA}/sources"
SCHEMA_VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/sources/WIKIBRICKS.MD"


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
            source_ids   ARRAY<STRING>,
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
        f"""
        CREATE TABLE IF NOT EXISTS {SOURCES_TABLE} (
            source_id    STRING     NOT NULL,
            uri          STRING     NOT NULL,
            title        STRING,
            content_text STRING,
            source_type  STRING     COMMENT 'url, document, api, manual',
            ingested_at  TIMESTAMP  DEFAULT current_timestamp(),
            metadata     VARIANT
        )
        USING DELTA
        TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {LOG_TABLE} (
            log_id      STRING     NOT NULL,
            op_type     STRING     NOT NULL COMMENT 'write, search, read, ingest, lint, promote',
            path        STRING,
            query       STRING,
            details     STRING,
            created_by  STRING     DEFAULT 'agent',
            created_at  TIMESTAMP  DEFAULT current_timestamp()
        )
        USING DELTA
        TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
        """,
    ]


def create_schema_sql():
    """Return SQL to create the wiki schema."""
    return f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}"


def get_schema() -> str:
    """Read and return the wiki schema definition (WIKIBRICKS.MD)."""
    schema_path = Path(__file__).parent / "WIKIBRICKS.MD"
    return schema_path.read_text()


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

    fn_log = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_log(
        num_entries INT DEFAULT 20 COMMENT 'Number of recent log entries to return'
    )
    RETURNS STRING
    COMMENT 'Get recent wiki operation log entries. Returns JSON array of log records.'
    RETURN (
        SELECT to_json(collect_list(struct(
            log_id, op_type, path, query, details, created_by, created_at
        )))
        FROM (
            SELECT log_id, op_type, path, query, details, created_by, created_at,
                   row_number() OVER (ORDER BY created_at DESC) AS rn
            FROM {LOG_TABLE}
        )
        WHERE rn <= num_entries
    )
    """

    fn_index = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_index()
    RETURNS STRING
    COMMENT 'Get the full wiki page index. Returns JSON array of all pages with path, title, type, summary.'
    RETURN (
        SELECT to_json(collect_list(struct(
            path, title, page_type, summary, tags, version, updated_at
        )))
        FROM (
            SELECT path, title, page_type,
                   content:summary::STRING AS summary,
                   tags, version, updated_at
            FROM {PAGES_TABLE}
            ORDER BY path
        )
    )
    """

    schema_content = get_schema().replace("'", "\\'")
    fn_schema = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_schema()
    RETURNS STRING
    COMMENT 'Get the wiki schema definition: page types, path conventions, tag taxonomy, link types.'
    RETURN ('{schema_content}')
    """

    write_help = (
        "WikiBricks write operations require DML and cannot be called via UC functions. "
        "Use the WikiClient Python API instead:\\n\\n"
        "from wikibricks import WikiClient\\n"
        "wiki = WikiClient(warehouse_id=\\'<warehouse-id>\\')\\n"
        "wiki.write_page(\\'topics/my-topic\\', \\'Title\\', "
        "{\\'summary\\': \\'...\\', \\'body\\': \\'...\\'})\\n\\n"
        "Available write methods: write_page, ingest_source, promote_answer, materialize_index.\\n"
        "Required content fields: summary (one sentence), body (full text).\\n"
        "Page types: entity, concept, synthesis, comparison.\\n"
        "Path format: category/slug (must contain a slash)."
    )
    fn_write_help = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_write_help()
    RETURNS STRING
    COMMENT 'How to write wiki pages. Write ops use WikiClient Python API (UC functions cannot do DML).'
    RETURN ('{write_help}')
    """

    return [fn_search, fn_read, fn_history, fn_log, fn_index, fn_schema, fn_write_help]


def seed_pages(domain: str = "insurance"):
    """Return seed wiki pages for the given domain (insurance | hotpot | custom | none)."""
    from wikibricks import seeds
    return seeds.load(domain)




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


def eval_queries():
    """Return labeled evaluation queries for a motor-insurance seed wiki.

    Each entry maps a natural-language query to the path(s) of the relevant
    seed page(s). Used to compute recall@k / precision@k / MRR baselines.
    """
    return [
        {
            "query": "how to assess a total loss on a vehicle",
            "relevant_paths": ["sops/motor/total-loss"],
        },
        {
            "query": "handling multi-party liability claims",
            "relevant_paths": ["claims/liability/comparative-negligence"],
        },
        {
            "query": "common collision fraud indicators",
            "relevant_paths": ["claims/fraud/patterns"],
        },
        {
            "query": "what coverage tiers do we sell for auto",
            "relevant_paths": ["products/motor/coverage-tiers"],
        },
        {
            "query": "claimants with multiple recent claims",
            "relevant_paths": ["claims/fraud/repeat-claimants"],
        },
        {
            "query": "language preferences per customer",
            "relevant_paths": ["customers/preferences/language"],
        },
        {
            "query": "hail storm claim surge playbook",
            "relevant_paths": ["claims/weather/hail-surge"],
        },
        {
            "query": "salvage value in total loss calculation",
            "relevant_paths": ["sops/motor/total-loss"],
        },
        {
            "query": "comparative negligence percentages",
            "relevant_paths": ["claims/liability/comparative-negligence"],
        },
        {
            "query": "repeat claimant analysis",
            "relevant_paths": ["claims/fraud/repeat-claimants"],
        },
    ]


def eval_recall_at_k(retrieved_paths, relevant_paths, k):
    """Return 1.0 if any relevant path is in top-k retrieved, else 0.0."""
    top_k = retrieved_paths[:k]
    return 1.0 if any(p in top_k for p in relevant_paths) else 0.0


def eval_precision_at_k(retrieved_paths, relevant_paths, k):
    """Return fraction of top-k retrieved that are relevant."""
    top_k = retrieved_paths[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for p in top_k if p in relevant_paths)
    return hits / len(top_k)


def eval_mrr(retrieved_paths, relevant_paths):
    """Return reciprocal rank of first relevant result, or 0.0 if none."""
    for i, p in enumerate(retrieved_paths, 1):
        if p in relevant_paths:
            return 1.0 / i
    return 0.0


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


def ingest_source_sql(uri, title=None, content_text=None, source_type="manual"):
    """Return SQL to insert a source into the sources table."""
    title_sql = f"'{title}'" if title else "NULL"
    content_sql = f"'{content_text}'" if content_text else "NULL"
    return f"""
    INSERT INTO {SOURCES_TABLE} (source_id, uri, title, content_text, source_type)
    VALUES (uuid(), '{uri}', {title_sql}, {content_sql}, '{source_type}')
    """


def log_operation_sql(op_type, path=None, query=None, details=None, created_by="agent"):
    """Return SQL to log a wiki operation."""
    path_sql = f"'{path}'" if path else "NULL"
    query_sql = f"'{query}'" if query else "NULL"
    details_sql = f"'{details}'" if details else "NULL"
    return f"""
    INSERT INTO {LOG_TABLE} (log_id, op_type, path, query, details, created_by)
    VALUES (uuid(), '{op_type}', {path_sql}, {query_sql}, {details_sql}, '{created_by}')
    """


def create_index_view_sql():
    """Return SQL to create the wiki index view."""
    return f"""
    CREATE OR REPLACE VIEW {CATALOG}.{SCHEMA}.wiki_index AS
    SELECT path, title, page_type, content:summary::STRING AS summary,
           tags, version, updated_at
    FROM {PAGES_TABLE}
    ORDER BY path
    """


def cdf_since_sql(table, since_timestamp):
    """Return SQL to query Change Data Feed changes since a timestamp."""
    return f"""
    SELECT * FROM table_changes('{table}', '{since_timestamp}')
    WHERE _change_type IN ('insert', 'update_postimage')
    """


def orphan_pages_sql():
    """Return SQL to find pages with no incoming links (excluding _meta/ pages)."""
    return f"""
    SELECT p.page_id, p.path, p.title, p.page_type, p.updated_at
    FROM {PAGES_TABLE} p
    LEFT JOIN {LINKS_TABLE} l ON p.page_id = l.target_page_id
    WHERE l.target_page_id IS NULL
      AND p.path NOT LIKE '_meta/%'
    ORDER BY p.updated_at DESC
    """
