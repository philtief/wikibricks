# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Nightly Wiki Lint
# MAGIC
# MAGIC Runs four lint checks against the wiki store and writes one `wiki_log` row per
# MAGIC issue with `op_type='lint'`:
# MAGIC
# MAGIC - orphan pages (no incoming links)
# MAGIC - stale pages (older than N days, zero retrieval hits)
# MAGIC - duplicate paths (casing collisions)
# MAGIC - broken links (target_page_id not in pages)

# COMMAND ----------

# MAGIC %pip install /Volumes/agent_marketplace_catalog/ai_agent/raw_data/wikibricks-0.1.0-py3-none-any.whl
# MAGIC %restart_python

# COMMAND ----------

import json
import uuid

from databricks.sdk import WorkspaceClient

from wikibricks.ops import (
    LOG_TABLE,
    broken_links_sql,
    duplicate_paths_sql,
    orphan_pages_sql,
    stale_pages_sql,
)


def _param(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        val = default
    return val or default


WAREHOUSE_ID = _param("warehouse_id", "41754a8563a43a49")
STALE_DAYS = int(_param("stale_days", "90"))

w = WorkspaceClient()


def run_sql(sql: str) -> list[dict]:
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql,
        wait_timeout="30s",
    )
    cols = [c.name for c in resp.manifest.schema.columns]
    rows = resp.result.data_array or []
    return [dict(zip(cols, r)) for r in rows]


# COMMAND ----------

checks = [
    ("orphan", orphan_pages_sql()),
    ("stale", stale_pages_sql(days=STALE_DAYS)),
    ("duplicate_path", duplicate_paths_sql()),
    ("broken_link", broken_links_sql()),
]

issues = []
for check_name, sql in checks:
    for row in run_sql(sql):
        issues.append({"check": check_name, "row": row})

print(f"found {len(issues)} lint issues")

# COMMAND ----------

if issues:
    values = []
    for issue in issues:
        log_id = str(uuid.uuid4())
        details = json.dumps(issue).replace("'", "''")
        values.append(
            f"('{log_id}', current_timestamp(), 'lint', NULL, '{issue['check']}', "
            f"'{details}', NULL, NULL)"
        )
    w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(
            f"INSERT INTO {LOG_TABLE} "
            f"(log_id, ts, op_type, actor, query, details, score, latency_ms) "
            f"VALUES {', '.join(values)}"
        ),
        wait_timeout="30s",
    )

print(f"logged {len(issues)} issues to {LOG_TABLE}")
