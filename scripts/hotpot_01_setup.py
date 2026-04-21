"""HotpotQA benchmark — step 1: schema + tables + bulk ingest pages & links.

Reads pages.jsonl + links.jsonl from /Volumes/agent_marketplace_catalog/ai_agent/raw_data/hotpot/,
creates agent_marketplace_catalog.wiki_hotpot.*, bulk-MERGEs 66k pages in a single statement,
then resolves path→page_id and inserts 14k links.

Idempotent — re-run safe.
"""

import os
import sys
import time

from databricks.sdk import WorkspaceClient

os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fe-vm-agent-marketplace")

CATALOG = "agent_marketplace_catalog"
SCHEMA = "wiki_hotpot"
WAREHOUSE_ID = "41754a8563a43a49"
VOL = f"/Volumes/{CATALOG}/ai_agent/raw_data/hotpot"

w = WorkspaceClient()


def sql(stmt: str, wait: str = "50s", label: str = "") -> None:
    t0 = time.time()
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=stmt,
        wait_timeout=wait,
    )
    while r.status.state.value in ("PENDING", "RUNNING"):
        time.sleep(2)
        r = w.statement_execution.get_statement(r.statement_id)
    dt = time.time() - t0
    if r.status.state.value != "SUCCEEDED":
        err = r.status.error.message if r.status.error else r.status.state.value
        print(f"FAIL ({label}) [{dt:.1f}s]: {err}", file=sys.stderr)
        print(f"SQL: {stmt[:500]}", file=sys.stderr)
        sys.exit(1)
    print(f"  ok ({label}) [{dt:.1f}s]")


print(f"=== schema: {CATALOG}.{SCHEMA} ===")
sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}", label="schema")

print("=== tables ===")
sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.{SCHEMA}.pages (
    page_id      STRING      NOT NULL,
    path         STRING      NOT NULL,
    path_depth   INT         GENERATED ALWAYS AS (size(split(path, '/'))),
    title        STRING      NOT NULL,
    page_type    STRING      NOT NULL,
    content      VARIANT     NOT NULL,
    content_text STRING,
    tags         ARRAY<STRING>,
    source_ids   ARRAY<STRING>,
    created_by   STRING      NOT NULL,
    created_at   TIMESTAMP   DEFAULT current_timestamp(),
    updated_at   TIMESTAMP   DEFAULT current_timestamp(),
    version      INT         NOT NULL DEFAULT 1
)
USING DELTA
TBLPROPERTIES (
    'delta.enableChangeDataFeed' = 'true',
    'delta.feature.allowColumnDefaults' = 'supported'
)
""", label="pages")

sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.{SCHEMA}.pages_history (
    page_id STRING NOT NULL, path STRING NOT NULL, title STRING NOT NULL,
    page_type STRING NOT NULL, content VARIANT NOT NULL, content_text STRING,
    tags ARRAY<STRING>, created_by STRING NOT NULL, created_at TIMESTAMP NOT NULL,
    version INT NOT NULL, archived_at TIMESTAMP DEFAULT current_timestamp()
) USING DELTA TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
""", label="pages_history")

sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.{SCHEMA}.links (
    source_page_id STRING NOT NULL,
    target_page_id STRING NOT NULL,
    link_type STRING NOT NULL DEFAULT 'related',
    created_at TIMESTAMP DEFAULT current_timestamp()
) USING DELTA TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
""", label="links")

sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.{SCHEMA}.sources (
    source_id STRING NOT NULL, uri STRING NOT NULL, title STRING, content_text STRING,
    source_type STRING, ingested_at TIMESTAMP DEFAULT current_timestamp(), metadata VARIANT
) USING DELTA TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
""", label="sources")

sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.{SCHEMA}.wiki_log (
    log_id STRING NOT NULL, op_type STRING NOT NULL, path STRING, query STRING,
    details STRING, created_by STRING DEFAULT 'agent',
    created_at TIMESTAMP DEFAULT current_timestamp()
) USING DELTA TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
""", label="wiki_log")

print("=== pages: MERGE from JSONL (66,569 rows) ===")
sql(f"""
MERGE INTO {CATALOG}.{SCHEMA}.pages t
USING (
    SELECT
        uuid() AS page_id,
        raw.path AS path,
        raw.title AS title,
        coalesce(raw.page_type, 'entity') AS page_type,
        parse_json(to_json(raw.content)) AS content,
        concat(
            coalesce(raw.content.summary, ''), ' ',
            coalesce(raw.content.body, '')
        ) AS content_text,
        coalesce(raw.tags, array()) AS tags,
        array() AS source_ids,
        coalesce(raw.created_by, 'hotpot-import') AS created_by,
        current_timestamp() AS created_at,
        current_timestamp() AS updated_at,
        1 AS version
    FROM read_files(
        '{VOL}/pages.jsonl',
        format => 'json',
        multiLine => 'false'
    ) raw
) s
ON t.path = s.path
WHEN NOT MATCHED THEN INSERT (
    page_id, path, title, page_type, content, content_text,
    tags, source_ids, created_by, created_at, updated_at, version
) VALUES (
    s.page_id, s.path, s.title, s.page_type, s.content, s.content_text,
    s.tags, s.source_ids, s.created_by, s.created_at, s.updated_at, s.version
)
""", wait="50s", label="pages-merge")

sql(f"SELECT count(*) AS n FROM {CATALOG}.{SCHEMA}.pages", label="pages-count")
r = w.statement_execution.execute_statement(
    warehouse_id=WAREHOUSE_ID,
    statement=f"SELECT count(*) AS n FROM {CATALOG}.{SCHEMA}.pages",
    wait_timeout="30s",
)
page_count = int(r.result.data_array[0][0])
print(f"  pages in table: {page_count:,}")

print("=== links: resolve path→page_id, MERGE ===")
sql(f"""
MERGE INTO {CATALOG}.{SCHEMA}.links l
USING (
    SELECT DISTINCT src.page_id AS source_page_id,
                    tgt.page_id AS target_page_id,
                    raw.link_type AS link_type
    FROM read_files('{VOL}/links.jsonl', format => 'json') raw
    JOIN {CATALOG}.{SCHEMA}.pages src ON src.path = raw.source_path
    JOIN {CATALOG}.{SCHEMA}.pages tgt ON tgt.path = raw.target_path
) s
ON l.source_page_id = s.source_page_id
   AND l.target_page_id = s.target_page_id
   AND l.link_type = s.link_type
WHEN NOT MATCHED THEN INSERT (source_page_id, target_page_id, link_type, created_at)
VALUES (s.source_page_id, s.target_page_id, s.link_type, current_timestamp())
""", wait="50s", label="links-merge")

r = w.statement_execution.execute_statement(
    warehouse_id=WAREHOUSE_ID,
    statement=f"SELECT count(*) AS n FROM {CATALOG}.{SCHEMA}.links",
    wait_timeout="30s",
)
link_count = int(r.result.data_array[0][0])
print(f"  links in table: {link_count:,}")

print(f"\n=== done: {page_count:,} pages, {link_count:,} links in {CATALOG}.{SCHEMA} ===")
