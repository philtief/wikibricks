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
        mode: One of "ANN", "KEYWORD", "HYBRID".
        num_results: Max results to return.
    """
    if mode not in ("ANN", "KEYWORD", "HYBRID"):
        raise ValueError(f"Invalid search mode: {mode}. Must be ANN, KEYWORD, or HYBRID.")

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
    """Return SQL statements to create the four wiki UC functions.

    Returns [fn_wiki_search, fn_wiki_read, fn_wiki_write, fn_wiki_history].
    These are SQL UDFs that agents call as tools.
    """
    fn_search = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_search(
        question STRING COMMENT 'The search query text',
        mode STRING DEFAULT 'HYBRID' COMMENT 'Search mode: ANN (semantic), KEYWORD (exact), or HYBRID (both)'
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
        page_path STRING COMMENT 'The wiki page path, e.g. claims/fraud/patterns'
    )
    RETURNS STRING
    COMMENT 'Read a wiki page by path. Returns JSON with page content and cross-references.'
    RETURN (
        SELECT to_json(struct(
            p.page_id, p.path, p.title, p.page_type, p.content, p.tags,
            p.created_by, p.created_at, p.updated_at, p.version,
            collect_list(struct(l.target_page_id, l.link_type,
                               t.path AS target_path, t.title AS target_title)) AS links
        ))
        FROM {PAGES_TABLE} p
        LEFT JOIN {LINKS_TABLE} l ON p.page_id = l.source_page_id
        LEFT JOIN {PAGES_TABLE} t ON l.target_page_id = t.page_id
        WHERE p.path = page_path
        GROUP BY p.page_id, p.path, p.title, p.page_type, p.content, p.tags,
                 p.created_by, p.created_at, p.updated_at, p.version
    )
    """

    fn_write = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_write(
        page_path STRING COMMENT 'The wiki page path, e.g. claims/fraud/patterns',
        title STRING COMMENT 'Page title',
        page_type STRING DEFAULT 'concept' COMMENT 'Page type: entity, concept, synthesis, or comparison',
        content_json STRING COMMENT 'Page content as JSON with summary and body fields',
        created_by STRING DEFAULT 'agent' COMMENT 'Who created this version'
    )
    RETURNS STRING
    COMMENT 'Write or update a wiki page. Archives the previous version to history, then MERGE into current table.'
    LANGUAGE SQL
    NOT DETERMINISTIC
    BEGIN
        -- Archive current version to history
        INSERT INTO {HISTORY_TABLE}
        (page_id, path, title, page_type, content, content_text, tags, created_by, created_at, version)
        SELECT page_id, path, title, page_type, content, content_text, tags, created_by, created_at, version
        FROM {PAGES_TABLE}
        WHERE path = page_path;

        -- MERGE into current table (upsert)
        MERGE INTO {PAGES_TABLE} AS target
        USING (SELECT page_path AS path) AS source
        ON target.path = source.path
        WHEN MATCHED THEN UPDATE SET
            title = title,
            page_type = page_type,
            content = PARSE_JSON(content_json),
            content_text = concat(PARSE_JSON(content_json):summary::STRING, ' ', PARSE_JSON(content_json):body::STRING),
            created_by = created_by,
            updated_at = current_timestamp(),
            version = target.version + 1
        WHEN NOT MATCHED THEN INSERT
            (page_id, path, title, page_type, content, content_text, created_by, version)
        VALUES (uuid(), page_path, title, page_type, PARSE_JSON(content_json),
                concat(PARSE_JSON(content_json):summary::STRING, ' ', PARSE_JSON(content_json):body::STRING),
                created_by, 1);

        RETURN concat('wrote ', page_path);
    END
    """

    fn_history = f"""
    CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.fn_wiki_history(
        page_path STRING COMMENT 'The wiki page path to get history for'
    )
    RETURNS STRING
    COMMENT 'Get version history for a wiki page. Returns JSON array of past versions.'
    RETURN (
        SELECT to_json(collect_list(struct(
            version, created_by, created_at,
            content:summary::STRING AS summary
        )))
        FROM {HISTORY_TABLE}
        WHERE path = page_path
        ORDER BY version DESC
    )
    """

    return [fn_search, fn_read, fn_write, fn_history]


def seed_pages():
    """Return a list of sample wiki pages for testing the wiki store."""
    return [
        {
            "path": "claims/fraud/patterns",
            "title": "Fraud Patterns in Collision Claims",
            "page_type": "concept",
            "content": {
                "summary": "Fraud patterns observed in collision claims Q1 2026.",
                "body": (
                    "Customers filing 3+ claims within 12 months show elevated fraud risk. "
                    "Key indicators: same repair shop across claims, inconsistent damage photos, "
                    "claims filed within 48 hours of policy changes. Three customers flagged by "
                    "automated scoring in March 2026."
                ),
            },
            "created_by": "promotion_pipeline",
            "tags": ["fraud", "claims", "collision", "patterns"],
        },
        {
            "path": "claims/fraud/repeat-claimants",
            "title": "Repeat Claimant Analysis",
            "page_type": "synthesis",
            "content": {
                "summary": "Analysis of customers with multiple claims in short timeframes.",
                "body": (
                    "Repeat claimants represent 2.3% of the portfolio but account for 14% of "
                    "claim payouts. The top decile by claim frequency has an average loss ratio "
                    "of 340%. Recommended action: mandatory adjuster review for any customer "
                    "with 2+ claims in 6 months."
                ),
            },
            "created_by": "promotion_pipeline",
            "tags": ["fraud", "claims", "repeat-claimants", "analysis"],
        },
        {
            "path": "customers/preferences/language",
            "title": "Customer Language Preferences",
            "page_type": "entity",
            "content": {
                "summary": "Recorded language preferences for customers in the portfolio.",
                "body": (
                    "Customer C-1005 prefers German for all communications. "
                    "Customer C-2091 prefers Italian. Customer C-3344 requested English-only "
                    "documentation despite being in the French market. These preferences were "
                    "confirmed during claim handling conversations."
                ),
            },
            "created_by": "agent",
            "tags": ["customers", "preferences", "language"],
        },
        {
            "path": "sops/motor/total-loss",
            "title": "Total Loss Assessment SOP",
            "page_type": "concept",
            "content": {
                "summary": "Standard operating procedure for motor total loss assessments.",
                "body": (
                    "When repair cost exceeds 70% of vehicle market value, initiate total loss "
                    "assessment. Steps: 1) Obtain independent valuation. 2) Compare against "
                    "repair estimate. 3) If total loss confirmed, offer settlement at market "
                    "value minus salvage. 4) Allow 14-day dispute window. Average processing "
                    "time: 8 business days."
                ),
            },
            "created_by": "promotion_pipeline",
            "tags": ["sop", "motor", "total-loss", "assessment"],
        },
        {
            "path": "claims/liability/comparative-negligence",
            "title": "Comparative Negligence in Liability Claims",
            "page_type": "concept",
            "content": {
                "summary": "How comparative negligence affects liability claim settlements.",
                "body": (
                    "In comparative negligence jurisdictions, each party bears liability "
                    "proportional to their fault. A 70/30 split means the insured pays 30% "
                    "of damages. Key precedent: if the insured is over 50% at fault in a "
                    "modified comparative negligence state, they recover nothing. Always verify "
                    "the jurisdiction's threshold before settling."
                ),
            },
            "created_by": "promotion_pipeline",
            "tags": ["claims", "liability", "negligence", "legal"],
        },
        {
            "path": "products/motor/coverage-tiers",
            "title": "Motor Insurance Coverage Tiers",
            "page_type": "comparison",
            "content": {
                "summary": "Comparison of Basic, Standard, and Premium motor coverage tiers.",
                "body": (
                    "Basic: third-party liability only. Standard: adds own-damage, theft, "
                    "windscreen. Premium: adds breakdown assistance, courtesy car, legal "
                    "expenses, new-for-old replacement under 12 months. Premium tier has "
                    "12% higher retention rate but 8% higher loss ratio than Standard."
                ),
            },
            "created_by": "promotion_pipeline",
            "tags": ["products", "motor", "coverage", "comparison"],
        },
        {
            "path": "claims/weather/hail-surge",
            "title": "Hail Surge Event Playbook",
            "page_type": "concept",
            "content": {
                "summary": "Operational playbook for handling hail surge claim events.",
                "body": (
                    "When hail events generate 50+ claims in 48 hours: 1) Activate surge "
                    "team. 2) Deploy mobile assessment units to affected region. 3) Pre-approve "
                    "PDR (paintless dent repair) for damage under threshold. 4) Extend "
                    "reporting deadline by 7 days. 5) Communicate proactively via SMS. "
                    "Last surge (March 2026, Munich): 340 claims, 92% resolved within 3 weeks."
                ),
            },
            "created_by": "promotion_pipeline",
            "tags": ["claims", "weather", "hail", "surge", "playbook"],
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
