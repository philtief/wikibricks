"""WikiBricks: Delta + Vector Search wiki store operations for AI agents."""

import json
import os
from pathlib import Path

from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EmbeddingSourceColumn,
    PipelineType,
    VectorIndexType,
)

CATALOG = os.environ.get("WIKIBRICKS_CATALOG", "main")
SCHEMA = os.environ.get("WIKIBRICKS_SCHEMA", "wiki")
PAGES_TABLE = f"{CATALOG}.{SCHEMA}.pages"
HISTORY_TABLE = f"{CATALOG}.{SCHEMA}.pages_history"
LINKS_TABLE = f"{CATALOG}.{SCHEMA}.links"
SOURCES_TABLE = f"{CATALOG}.{SCHEMA}.sources"
LOG_TABLE = f"{CATALOG}.{SCHEMA}.wiki_log"
PAGES_VS_SOURCE_TABLE = f"{CATALOG}.{SCHEMA}.pages_vs_source"
PROMOTE_CHECKPOINT_TABLE = f"{CATALOG}.{SCHEMA}.promote_checkpoint"
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
            page_id           STRING        NOT NULL,
            path              STRING        NOT NULL,
            path_depth        INT           GENERATED ALWAYS AS (size(split(path, '/'))),
            title             STRING        NOT NULL,
            page_type         STRING        NOT NULL,
            content           VARIANT       NOT NULL,
            content_text      STRING,
            tags              ARRAY<STRING>,
            source_ids        ARRAY<STRING>,
            parent_id         STRING,
            chunk_index       INT,
            health_status     STRING        DEFAULT 'unknown',
            health_score      DOUBLE,
            last_health_check TIMESTAMP,
            created_by        STRING        NOT NULL,
            created_at        TIMESTAMP     DEFAULT current_timestamp(),
            updated_at        TIMESTAMP     DEFAULT current_timestamp(),
            version           INT           NOT NULL DEFAULT 1
        )
        USING DELTA
        TBLPROPERTIES (
            'delta.enableChangeDataFeed' = 'true',
            'delta.feature.allowColumnDefaults' = 'supported'
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
            page_id           STRING        NOT NULL,
            path              STRING        NOT NULL,
            title             STRING        NOT NULL,
            page_type         STRING        NOT NULL,
            content           VARIANT       NOT NULL,
            content_text      STRING,
            tags              ARRAY<STRING>,
            parent_id         STRING,
            chunk_index       INT,
            health_status     STRING,
            health_score      DOUBLE,
            last_health_check TIMESTAMP,
            created_by        STRING        NOT NULL,
            created_at        TIMESTAMP     NOT NULL,
            version           INT           NOT NULL,
            archived_at       TIMESTAMP     DEFAULT current_timestamp()
        )
        USING DELTA
        TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {LINKS_TABLE} (
            source_page_id  STRING  NOT NULL,
            target_page_id  STRING  NOT NULL,
            link_type       STRING  NOT NULL DEFAULT 'related',
            confidence      FLOAT   NOT NULL DEFAULT 1.0,
            origin          STRING  NOT NULL DEFAULT 'manual',
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
        f"""
        CREATE TABLE IF NOT EXISTS {PAGES_VS_SOURCE_TABLE} (
            page_id      STRING        NOT NULL,
            path         STRING        NOT NULL,
            title        STRING        NOT NULL,
            page_type    STRING        NOT NULL,
            content_text STRING,
            tags         ARRAY<STRING>,
            version      INT           NOT NULL
        )
        USING DELTA
        TBLPROPERTIES (
            'delta.enableChangeDataFeed' = 'true',
            'delta.feature.allowColumnDefaults' = 'supported'
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {PROMOTE_CHECKPOINT_TABLE} (
            checkpoint_id     STRING    NOT NULL,
            last_watermark_ts TIMESTAMP NOT NULL,
            updated_at        TIMESTAMP DEFAULT current_timestamp()
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


def create_vs_index_spec():
    """Return kwargs for ``w.vector_search_indexes.create_index()``."""
    return {
        "name": VS_INDEX,
        "endpoint_name": VS_ENDPOINT,
        "primary_key": "page_id",
        "index_type": VectorIndexType.DELTA_SYNC,
        "delta_sync_index_spec": DeltaSyncVectorIndexSpecRequest(
            source_table=PAGES_VS_SOURCE_TABLE,
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


UC_FUNCTION_NAMES = (
    "fn_wiki_search",
    "fn_wiki_read",
    "fn_wiki_history",
    "fn_wiki_log",
    "fn_wiki_index",
    "fn_wiki_schema",
    "fn_wiki_write_help",
    "fn_wiki_read_full",
)


def create_uc_functions_sql(warehouse_id, enabled=None):
    """Return SQL statements to create the wiki UC read functions.

    Each statement creates one MCP-exposed UC function. Returns the eight
    statements in the canonical order
    (fn_wiki_search, fn_wiki_read, fn_wiki_history, fn_wiki_log,
    fn_wiki_index, fn_wiki_schema, fn_wiki_write_help, fn_wiki_read_full)
    when `enabled=None`. Pass `enabled` (set/list of function names) to
    deploy a subset — useful when a personal/embedded deployment only
    wants the read paths exposed via managed MCP. Unknown names raise
    ValueError so typos can't silently produce a partial deploy.
    Write ops are not exposed here (UC functions can't do DML); see
    `make_agent_tools` in client code.
    """
    # vector_search() requires foldable INT for num_results (cannot reference
    # a UDF parameter directly), so we fix the inner K and trim with
    # ROW_NUMBER() in the outer query.
    fn_search = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_search(
        question STRING COMMENT 'Natural-language search query',
        num_results INT DEFAULT 5 COMMENT 'Top-K pages to return (1-20)'
    )
    RETURNS STRING
    COMMENT 'HYBRID Vector Search (semantic + FULL_TEXT) over wiki pages. Returns JSON top-K pages with content_text.'
    RETURN (
        SELECT to_json(collect_list(struct(
            page_id, path, title, page_type, content_text, tags, version, search_score
        )))
        FROM (
            SELECT *, ROW_NUMBER() OVER (ORDER BY search_score DESC) AS rn
            FROM vector_search(
                index => '{VS_INDEX}',
                query_text => question,
                num_results => 20,
                query_type => 'HYBRID'
            )
        )
        WHERE rn <= num_results
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

    fn_read_full = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_read_full(
        page_path STRING COMMENT 'Path of the parent or standalone page'
    )
    RETURNS STRING
    COMMENT 'Read a page plus all chunk contents in order. Use when a page may have been segregated by maintenance.'
    RETURN (
        WITH parent AS (
            SELECT page_id, path, title, page_type, content, tags,
                   created_by, created_at, updated_at, version
            FROM {PAGES_TABLE}
            WHERE path = page_path
        ),
        chunks AS (
            SELECT c.chunk_index, c.content
            FROM {PAGES_TABLE} c
            JOIN parent p ON c.parent_id = p.page_id
            ORDER BY c.chunk_index
        )
        SELECT to_json(struct(
            (SELECT first(page_id) FROM parent) AS page_id,
            (SELECT first(path) FROM parent) AS path,
            (SELECT first(title) FROM parent) AS title,
            (SELECT first(page_type) FROM parent) AS page_type,
            (SELECT first(content) FROM parent) AS content,
            (SELECT collect_list(content) FROM chunks) AS chunk_contents,
            (SELECT first(tags) FROM parent) AS tags,
            (SELECT first(version) FROM parent) AS version
        ))
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

    by_name = {
        "fn_wiki_search": fn_search,
        "fn_wiki_read": fn_read,
        "fn_wiki_history": fn_history,
        "fn_wiki_log": fn_log,
        "fn_wiki_index": fn_index,
        "fn_wiki_schema": fn_schema,
        "fn_wiki_write_help": fn_write_help,
        "fn_wiki_read_full": fn_read_full,
    }
    if enabled is None:
        return [by_name[n] for n in UC_FUNCTION_NAMES]
    requested = set(enabled)
    unknown = requested - set(UC_FUNCTION_NAMES)
    if unknown:
        raise ValueError(
            f"Unknown UC function name(s): {sorted(unknown)}. "
            f"Valid names: {list(UC_FUNCTION_NAMES)}"
        )
    return [by_name[n] for n in UC_FUNCTION_NAMES if n in requested]


def seed_pages(domain: str = "sample"):
    """Return seed wiki pages for the given domain (sample | hotpot | custom | none)."""
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
    """Labeled queries over the `sample` seed. Each maps to the relevant page paths.

    Used by `run_autoeval` to compute recall@k / precision@k / MRR baselines on
    the default sample corpus. Custom deployments should supply their own.
    """
    return [
        {
            "query": "how do I get started with WikiBricks",
            "relevant_paths": ["topics/getting-started"],
        },
        {
            "query": "what is the architecture of WikiBricks",
            "relevant_paths": ["topics/architecture/overview"],
        },
        {
            "query": "which Delta tables does the wiki use",
            "relevant_paths": ["topics/architecture/overview"],
        },
        {
            "query": "how to deploy the wiki to a workspace",
            "relevant_paths": ["guides/setup"],
        },
        {
            "query": "steps for creating the catalog and schema",
            "relevant_paths": ["guides/setup"],
        },
        {
            "query": "search returns no results what should I check",
            "relevant_paths": ["guides/troubleshooting"],
        },
        {
            "query": "PARSE_JSON failure in content",
            "relevant_paths": ["guides/troubleshooting"],
        },
        {
            "query": "difference between ANN full-text and hybrid search",
            "relevant_paths": ["comparisons/search-modes"],
        },
        {
            "query": "which search mode is best for keyword matching",
            "relevant_paths": ["comparisons/search-modes"],
        },
        {
            "query": "automatic version history and archiving",
            "relevant_paths": ["topics/architecture/overview"],
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


def eval_recall_at_k_multi(retrieved_paths, relevant_paths, k):
    """Fraction of relevant paths present in top-k (multi-truth recall). 1.0 if no truth set."""
    if not relevant_paths:
        return 1.0
    top_k = set(retrieved_paths[:k])
    hits = sum(1 for p in relevant_paths if p in top_k)
    return hits / len(relevant_paths)


def eval_mrr_multi(retrieved_paths, relevant_paths):
    """Reciprocal rank of the FIRST relevant path. Alias for eval_mrr - explicit for multi-truth reporting."""
    return eval_mrr(retrieved_paths, relevant_paths)


def eval_supporting_fact_f1(retrieved_paths, relevant_paths):
    """F1 over retrieved vs relevant - the HotpotQA supporting-fact metric. 1.0 when both sets are empty."""
    retrieved = set(retrieved_paths)
    relevant = set(relevant_paths)
    if not retrieved and not relevant:
        return 1.0
    if not retrieved or not relevant:
        return 0.0
    tp = len(retrieved & relevant)
    if tp == 0:
        return 0.0
    precision = tp / len(retrieved)
    recall = tp / len(relevant)
    return 2 * precision * recall / (precision + recall)


VALID_LINK_TYPES = ("related", "contradicts", "extends", "supersedes", "cites")
VALID_LINK_ORIGINS = ("manual", "auto-vs", "auto-title", "auto-cite")


def add_link_sql(source_page_id, target_page_id, link_type="related",
                 confidence=1.0, origin="manual"):
    """Return SQL to add a cross-reference link (idempotent via MERGE).

    Args:
        source_page_id: Source page ID.
        target_page_id: Target page ID.
        link_type: One of related, contradicts, extends, supersedes, cites.
        confidence: [0.0, 1.0] — 1.0 for manual edges, similarity score for auto.
        origin: manual, auto-vs (VS nearest-neighbor), auto-title (exact title match),
                auto-cite (promote_answer citation).
    """
    if link_type not in VALID_LINK_TYPES:
        raise ValueError(f"Invalid link type: {link_type}")
    if origin not in VALID_LINK_ORIGINS:
        raise ValueError(f"Invalid link origin: {origin}")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError(f"confidence must be in [0, 1], got {confidence}")

    return f"""
    MERGE INTO {LINKS_TABLE} AS t
    USING (SELECT '{source_page_id}' AS src, '{target_page_id}' AS tgt,
                  '{link_type}' AS lt, CAST({float(confidence)} AS FLOAT) AS conf,
                  '{origin}' AS org) AS s
    ON t.source_page_id = s.src AND t.target_page_id = s.tgt AND t.link_type = s.lt
    WHEN MATCHED THEN UPDATE SET confidence = s.conf, origin = s.org
    WHEN NOT MATCHED THEN INSERT (source_page_id, target_page_id, link_type, confidence, origin)
    VALUES (s.src, s.tgt, s.lt, s.conf, s.org)
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


def stale_pages_sql(days: int = 90):
    """Return SQL for pages older than `days` with no recent retrieval hits."""
    return f"""
    SELECT p.page_id, p.path, p.title, p.updated_at
    FROM {PAGES_TABLE} p
    LEFT JOIN (
        SELECT DISTINCT get_json_object(details, '$.path') AS path
        FROM {LOG_TABLE}
        WHERE op_type = 'search'
          AND ts >= current_timestamp() - INTERVAL {days} DAYS
    ) hits ON hits.path = p.path
    WHERE p.updated_at < current_timestamp() - INTERVAL {days} DAYS
      AND hits.path IS NULL
      AND p.path NOT LIKE '_meta/%'
    ORDER BY p.updated_at ASC
    """


def duplicate_paths_sql():
    """Return SQL for paths that collide after lowercasing (casing dupes)."""
    return f"""
    SELECT LOWER(path) AS path_lower,
           COUNT(*)   AS n,
           collect_set(path) AS variants
    FROM {PAGES_TABLE}
    GROUP BY LOWER(path)
    HAVING COUNT(*) > 1
    ORDER BY n DESC
    """


def broken_links_sql():
    """Return SQL for link rows whose target_page_id no longer exists in pages."""
    return f"""
    SELECT l.source_page_id, l.target_page_id, l.link_type
    FROM {LINKS_TABLE} l
    LEFT JOIN {PAGES_TABLE} p ON p.page_id = l.target_page_id
    WHERE p.page_id IS NULL
    """


def delete_broken_links_sql():
    """Return SQL to delete link rows whose target_page_id no longer exists in pages."""
    return f"""
    DELETE FROM {LINKS_TABLE}
    WHERE target_page_id NOT IN (SELECT page_id FROM {PAGES_TABLE})
       OR source_page_id NOT IN (SELECT page_id FROM {PAGES_TABLE})
    """


def graph_neighbors_sql(page_id, depth=1, link_types=None):
    """Return SQL for outgoing link neighbors of a page up to `depth` hops.

    Depth-limited BFS implemented as UNION ALL over depth levels (up to 3).
    Returns: source_page_id, target_page_id, target_path, target_title, link_type,
             confidence, origin, hop.
    """
    if depth < 1 or depth > 3:
        raise ValueError(f"depth must be in [1, 3], got {depth}")

    type_filter = ""
    if link_types:
        types_csv = ",".join(f"'{lt}'" for lt in link_types)
        type_filter = f" AND l.link_type IN ({types_csv})"

    def _hop(level, source_expr):
        return f"""
        SELECT l.source_page_id, l.target_page_id,
               p.path AS target_path, p.title AS target_title,
               l.link_type, l.confidence, l.origin, {level} AS hop
        FROM {LINKS_TABLE} l
        JOIN {PAGES_TABLE} p ON p.page_id = l.target_page_id
        WHERE l.source_page_id IN ({source_expr}){type_filter}
        """

    seed = f"'{page_id}'"
    parts = [_hop(1, seed)]
    if depth >= 2:
        parts.append(_hop(2, f"SELECT target_page_id FROM ({parts[0]})"))
    if depth >= 3:
        parts.append(_hop(3, f"SELECT target_page_id FROM ({parts[1]})"))

    return " UNION ALL ".join(parts)
