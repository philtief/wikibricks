# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Autonomous Maintenance
# MAGIC
# MAGIC Scheduled Lakeflow Job that performs autonomous wiki maintenance:
# MAGIC 1. Detect recent changes via Change Data Feed
# MAGIC 2. Find near-duplicate pages and suggest merges
# MAGIC 3. Suggest cross-reference links for related content
# MAGIC 4. Detect orphan pages (no incoming links)
# MAGIC 5. Materialize the wiki index at `_meta/index`
# MAGIC 6. Write a maintenance report to `_meta/maintenance-log`

# COMMAND ----------

# MAGIC %pip install /Volumes/agent_marketplace_catalog/ai_agent/raw_data/wikibricks-0.1.0-py3-none-any.whl
# MAGIC %restart_python

# COMMAND ----------

import json
from datetime import datetime, timedelta, timezone

from databricks.sdk import WorkspaceClient

from wikibricks.client import WikiClient
from wikibricks.ops import (
    CATALOG,
    LINKS_TABLE,
    LOG_TABLE,
    PAGES_TABLE,
    SCHEMA,
    VS_INDEX,
    cdf_since_sql,
    orphan_pages_sql,
)

w = WorkspaceClient()
WAREHOUSE_ID = dbutils.widgets.get("warehouse_id")  # noqa: F821
wiki = WikiClient(warehouse_id=WAREHOUSE_ID, workspace_client=w)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Detect recent changes

# COMMAND ----------

since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
try:
    cdf_sql = cdf_since_sql(PAGES_TABLE, since)
    resp = wiki._exec(cdf_sql)
    changed_rows = resp.result.data_array if resp.result else []
    cols = [c.name for c in resp.manifest.columns] if changed_rows else []
    changes = [dict(zip(cols, row)) for row in changed_rows]
    print(f"Found {len(changes)} changed pages since {since}")
except Exception as e:
    print(f"CDF query failed (may need enableChangeDataFeed): {e}")
    changes = []

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Find near-duplicate pages

# COMMAND ----------

duplicates_found = []
for change in changes:
    page_path = change.get("path", "")
    content_text = change.get("content_text", "")
    if not content_text or page_path.startswith("_meta/"):
        continue

    try:
        similar = wiki.search(content_text[:200], num_results=5)
        for s in similar:
            if s.get("path") != page_path and s.get("path", "").startswith(page_path.split("/")[0]):
                duplicates_found.append({
                    "page": page_path,
                    "similar_to": s.get("path"),
                    "title": s.get("title"),
                })
    except Exception as e:
        print(f"Similarity search failed for {page_path}: {e}")

print(f"Found {len(duplicates_found)} potential duplicates")
for d in duplicates_found:
    print(f"  {d['page']} ~ {d['similar_to']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Suggest cross-reference links

# COMMAND ----------

links_added = 0
for change in changes:
    page_id = change.get("page_id", "")
    page_path = change.get("path", "")
    content_text = change.get("content_text", "")
    if not content_text or page_path.startswith("_meta/"):
        continue

    try:
        related = wiki.search(content_text[:200], num_results=3)
        for r in related:
            r_id = r.get("page_id", "")
            if r_id and r_id != page_id:
                wiki._exec(
                    f"MERGE INTO {LINKS_TABLE} AS t "
                    f"USING (SELECT '{page_id}' AS src, '{r_id}' AS tgt, "
                    f"'related' AS lt) AS s "
                    f"ON t.source_page_id = s.src AND t.target_page_id = s.tgt "
                    f"AND t.link_type = s.lt "
                    f"WHEN NOT MATCHED THEN INSERT "
                    f"(source_page_id, target_page_id, link_type) "
                    f"VALUES (s.src, s.tgt, s.lt)"
                )
                links_added += 1
    except Exception as e:
        print(f"Cross-ref failed for {page_path}: {e}")

print(f"Added {links_added} cross-reference links")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Detect orphan pages

# COMMAND ----------

try:
    resp = wiki._exec(orphan_pages_sql())
    orphan_rows = resp.result.data_array if resp.result else []
    cols = [c.name for c in resp.manifest.columns] if orphan_rows else []
    orphans = [dict(zip(cols, row)) for row in orphan_rows]
    print(f"Found {len(orphans)} orphan pages (no incoming links)")
    for o in orphans:
        print(f"  {o.get('path')} — {o.get('title')}")
except Exception as e:
    print(f"Orphan detection failed: {e}")
    orphans = []

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5: Materialize wiki index

# COMMAND ----------

index_result = wiki.materialize_index()
print(index_result)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6: Write maintenance report

# COMMAND ----------

report_body = f"""Maintenance run: {datetime.now(timezone.utc).isoformat()}
Since: {since}

Changes detected: {len(changes)}
Potential duplicates: {len(duplicates_found)}
Cross-reference links added: {links_added}
Orphan pages: {len(orphans)}

Duplicates:
{json.dumps(duplicates_found, indent=2) if duplicates_found else "None"}

Orphans:
{json.dumps([o.get('path') for o in orphans], indent=2) if orphans else "None"}
"""

wiki.write_page(
    "_meta/maintenance-log",
    "Maintenance Log",
    {"summary": f"Maintenance run {datetime.now(timezone.utc).strftime('%Y-%m-%d')}", "body": report_body},
    page_type="synthesis",
    created_by="maintenance",
    tags=["meta", "maintenance", "auto-generated"],
)
print("Maintenance report written to _meta/maintenance-log")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC Schedule this notebook as a Lakeflow Job (daily or hourly) to keep the wiki healthy.
