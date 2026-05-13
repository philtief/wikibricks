# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Deploy Wiki Store
# MAGIC
# MAGIC Creates the wiki schema, tables, Vector Search index, UC functions,
# MAGIC and seeds test data. Run once per workspace (idempotent — re-running is safe).

# COMMAND ----------

# MAGIC %pip install /Volumes/<catalog>/<schema>/wheels/wikibricks-0.3.4-py3-none-any.whl
# MAGIC # ^ Update path to where the wheel lives in your workspace.
# MAGIC %restart_python

# COMMAND ----------

import json
import os
import time


def _param(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821 - provided by Databricks runtime
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        val = default
    return val or default


# Resolve catalog/schema BEFORE importing wikibricks so its module-level
# CATALOG/SCHEMA constants point at the deploy target, not the defaults.
os.environ["WIKIBRICKS_CATALOG"] = _param("catalog", "main")
os.environ["WIKIBRICKS_SCHEMA"] = _param("schema", "wiki")
WAREHOUSE_ID = _param("warehouse_id", "")
SEED_DOMAIN = _param("seed_domain", "sample")
# Comma-separated UC function names to deploy. Empty = all 8.
# Use to expose a subset via managed MCP, e.g.:
#   "fn_wiki_search,fn_wiki_read_full,fn_wiki_index"
ENABLED_UC_FUNCTIONS = _param("enabled_uc_functions", "")

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.vectorsearch import EndpointType  # noqa: E402

from wikibricks.ops import (  # noqa: E402
    VS_ENDPOINT,
    create_index_view_sql,
    create_schema_sql,
    create_tables_sql,
    create_uc_functions_sql,
    create_vs_index_spec,
    drop_uc_functions_sql,
    seed_pages,
    write_page_sql,
)

w = WorkspaceClient()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1: Create Schema

# COMMAND ----------

result = w.statement_execution.execute_statement(
    warehouse_id=WAREHOUSE_ID,
    statement=create_schema_sql(),
)
print(f"Schema: {result.status.state}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2: Create Tables (pages, pages_history, links, sources, wiki_log)

# COMMAND ----------

table_names = ["pages", "pages_history", "links", "sources", "wiki_log",
               "pages_vs_source", "promote_checkpoint"]
for i, stmt in enumerate(create_tables_sql()):
    result = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=stmt,
    )
    print(f"Table {table_names[i]}: {result.status.state}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2b: Create wiki_index materialized view

# COMMAND ----------

result = w.statement_execution.execute_statement(
    warehouse_id=WAREHOUSE_ID,
    statement=create_index_view_sql(),
)
print(f"View wiki_index: {result.status.state}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3: Create Vector Search Endpoint + Index

# COMMAND ----------

# Create endpoint if it doesn't exist
try:
    endpoint = w.vector_search_endpoints.get_endpoint(VS_ENDPOINT)
    print(f"Endpoint {VS_ENDPOINT} already exists: {endpoint.endpoint_status.state}")
except Exception:
    endpoint = w.vector_search_endpoints.create_endpoint(
        name=VS_ENDPOINT,
        endpoint_type=EndpointType.STANDARD,
    )
    print(f"Created endpoint {VS_ENDPOINT}")

# COMMAND ----------

# Create index
spec = create_vs_index_spec()
try:
    index = w.vector_search_indexes.get_index(spec["name"])
    print(f"Index {spec['name']} already exists: {index.status.ready}")
except Exception:
    index = w.vector_search_indexes.create_index(**spec)
    print(f"Created index {spec['name']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4: Create UC Functions

# COMMAND ----------

_enabled = [n.strip() for n in ENABLED_UC_FUNCTIONS.split(",") if n.strip()] or None
print(f"UC functions to deploy: {_enabled or 'all 8'}")
# Drop functions NOT in the enabled set so managed MCP only exposes the
# requested subset. No-op when _enabled is None (keep all deployed).
for stmt in drop_uc_functions_sql(enabled=_enabled):
    result = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=stmt,
    )
    print(f"Drop UC function: {result.status.state} :: {stmt.strip().split()[-1]}")
for stmt in create_uc_functions_sql(WAREHOUSE_ID, enabled=_enabled):
    result = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=stmt,
    )
    print(f"UC function: {result.status.state}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5: Seed Test Data

# COMMAND ----------

for page in seed_pages(domain=SEED_DOMAIN):
    content_json = json.dumps(page["content"])
    stmts = write_page_sql(
        path=page["path"],
        title=page["title"],
        page_type=page["page_type"],
        content_json=content_json,
        created_by=page["created_by"],
        tags=page["tags"],
    )
    for stmt in stmts:
        w.statement_execution.execute_statement(
            warehouse_id=WAREHOUSE_ID,
            statement=stmt,
        )
    print(f"Seeded: {page['path']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6: Trigger VS Sync + Verify

# COMMAND ----------

spec = create_vs_index_spec()
# sync_index has a built-in 5-min wait that can timeout during initial provisioning.
# Use a manual polling loop with a longer timeout instead.
try:
    w.vector_search_indexes.sync_index(index_name=spec["name"])
    print("Triggered VS index sync.")
except Exception as e:
    print(f"sync_index returned: {e} - polling for readiness.")

# Poll until index is ready (up to 15 minutes for initial provisioning + sync)
print("Waiting for index to be ready...")
for i in range(90):
    index = w.vector_search_indexes.get_index(spec["name"])
    if index.status.ready:
        row_count = getattr(index.status, "index_row_count", None)
        print(f"Index ready. Row count: {row_count}")
        break
    if i % 6 == 0:
        detail = getattr(index.status, "detailed_state", None) or getattr(index.status, "message", "")
        print(f"  [{i * 10}s] status: {detail}")
    time.sleep(10)
else:
    print("Warning: index not ready after 15 minutes")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 7: Test Search Queries

# COMMAND ----------

# Test keyword search
results = w.vector_search_indexes.query_index(
    index_name=spec["name"],
    columns=["page_id", "path", "title"],
    query_text="total loss",
    query_type="FULL_TEXT",
    num_results=3,
)
print("FULL_TEXT search for 'total loss':")
for r in results.result.data_array:
    print(f"  {r}")

# COMMAND ----------

# Test semantic search
results = w.vector_search_indexes.query_index(
    index_name=spec["name"],
    columns=["page_id", "path", "title"],
    query_text="how to detect fraud in insurance claims",
    num_results=3,
)
print("ANN search for 'how to detect fraud in insurance claims':")
for r in results.result.data_array:
    print(f"  {r}")

# COMMAND ----------

# Test hybrid search
results = w.vector_search_indexes.query_index(
    index_name=spec["name"],
    columns=["page_id", "path", "title"],
    query_text="hail surge claim handling",
    query_type="HYBRID",
    num_results=3,
)
print("HYBRID search for 'hail surge claim handling':")
for r in results.result.data_array:
    print(f"  {r}")

# COMMAND ----------

print("WikiBricks deployment complete.")
