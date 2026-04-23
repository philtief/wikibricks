"""2WikiMultiHopQA - step 1: schema + tables + ingest pages/links.

Uploads src/wikibricks/seeds/twowiki/*.jsonl to a Databricks volume, creates
<catalog>.wiki_2wiki.*, then MERGEs pages and links.

Configure via env vars (or set DATABRICKS_CONFIG_PROFILE):

    WIKIBRICKS_CATALOG         default: main
    WIKIBRICKS_WAREHOUSE_ID    required
    WIKIBRICKS_TWOWIKI_VOL     default: /Volumes/<catalog>/default/twowiki

Idempotent - re-run safe.
"""

import os
import sys
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient

CATALOG = os.environ.get("WIKIBRICKS_CATALOG", "main")
SCHEMA = "wiki_2wiki"
WAREHOUSE_ID = os.environ.get("WIKIBRICKS_WAREHOUSE_ID") or sys.exit(
    "WIKIBRICKS_WAREHOUSE_ID env var required"
)
VOL = os.environ.get("WIKIBRICKS_TWOWIKI_VOL", f"/Volumes/{CATALOG}/default/twowiki")
SEED_DIR = Path("src/wikibricks/seeds/twowiki")

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


def scalar(stmt: str) -> str:
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=stmt, wait_timeout="30s")
    return r.result.data_array[0][0]


def upload(local: Path, remote: str) -> None:
    with open(local, "rb") as f:
        w.files.upload(remote, f, overwrite=True)
    size = local.stat().st_size
    print(f"  uploaded {local} → {remote} ({size:,} bytes)")


print(f"=== volume: {VOL} ===")
# Ensure the volume exists (create under existing schema ai_agent).
sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.ai_agent.raw_data", label="volume")

print("=== upload seeds ===")
upload(SEED_DIR / "pages.jsonl", f"{VOL}/pages.jsonl")
upload(SEED_DIR / "links.jsonl", f"{VOL}/links.jsonl")

print(f"\n=== schema: {CATALOG}.{SCHEMA} ===")
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
CREATE TABLE IF NOT EXISTS {CATALOG}.{SCHEMA}.links (
    source_page_id STRING NOT NULL,
    target_page_id STRING NOT NULL,
    link_type STRING NOT NULL DEFAULT 'related',
    created_at TIMESTAMP DEFAULT current_timestamp()
) USING DELTA TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
""", label="links")

print("=== pages: MERGE from JSONL ===")
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
            coalesce(raw.title, ''), ' ',
            coalesce(raw.content.body, '')
        ) AS content_text,
        coalesce(raw.tags, array()) AS tags,
        array() AS source_ids,
        coalesce(raw.created_by, 'twowiki-import') AS created_by,
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

page_count = int(scalar(f"SELECT count(*) FROM {CATALOG}.{SCHEMA}.pages"))
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

link_count = int(scalar(f"SELECT count(*) FROM {CATALOG}.{SCHEMA}.links"))
print(f"  links in table: {link_count:,}")

# Typed links: show top 10 link_types by count.
r = w.statement_execution.execute_statement(
    warehouse_id=WAREHOUSE_ID,
    statement=(f"SELECT link_type, count(*) n FROM {CATALOG}.{SCHEMA}.links "
               "GROUP BY link_type ORDER BY n DESC LIMIT 10"),
    wait_timeout="30s",
)
print("  top link_types:")
for lt, n in r.result.data_array or []:
    print(f"    {lt}: {n}")

print(f"\n=== done: {page_count:,} pages, {link_count:,} links "
      f"in {CATALOG}.{SCHEMA} ===")
