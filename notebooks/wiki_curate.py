# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Default Curate Flow (LLM-free)
# MAGIC
# MAGIC The shipped default maintenance job. Two phases, both deterministic:
# MAGIC
# MAGIC 1. **Connect** — for pages updated since the last run, call
# MAGIC    `WikiClient.propose_edges` (VS nearest-neighbor + exact-title match) and
# MAGIC    auto-commit edges whose `confidence >= auto_commit_threshold`. Lower-
# MAGIC    confidence edges are returned but not written — the agent decides.
# MAGIC 2. **Lint** — run the four detective SQL checks (orphan / stale / duplicate /
# MAGIC    broken) and log each issue to `wiki_log`. Optionally deletes broken-link
# MAGIC    rows whose endpoint pages no longer exist.
# MAGIC
# MAGIC No LLM calls. WikiBricks is the MCP server; the calling agent is the LLM.

# COMMAND ----------

# MAGIC %pip install /Volumes/agent_marketplace_catalog/ai_agent/raw_data/wikibricks-0.1.3-py3-none-any.whl
# MAGIC %restart_python

# COMMAND ----------

import json
import uuid
from datetime import datetime, timedelta, timezone

from databricks.sdk import WorkspaceClient

from wikibricks import WikiClient
from wikibricks.ops import (
    LOG_TABLE,
    PAGES_TABLE,
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
CONNECT_LOOKBACK_HOURS = int(_param("connect_lookback_hours", "48"))
AUTO_COMMIT_THRESHOLD = float(_param("auto_commit_threshold", "0.85"))
MIN_SIMILARITY = float(_param("min_similarity", "0.70"))
MAX_PAGES_PER_RUN = int(_param("max_pages_per_run", "500"))
REPAIR_BROKEN_LINKS = _param("repair_broken_links", "true").lower() == "true"

w = WorkspaceClient()
wiki = WikiClient(warehouse_id=WAREHOUSE_ID, workspace_client=w)


def run_sql(sql: str) -> list[dict]:
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql,
        wait_timeout="30s",
    )
    rows = resp.result.data_array or []
    if not rows:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, r)) for r in rows]


# COMMAND ----------

# MAGIC %md ## Phase 1: Connect — propose edges for recently-written pages

# COMMAND ----------

since = (datetime.now(timezone.utc) - timedelta(hours=CONNECT_LOOKBACK_HOURS)).isoformat()
recent = run_sql(
    f"SELECT path FROM {PAGES_TABLE} "
    f"WHERE updated_at >= '{since}' AND path NOT LIKE '_meta/%' "
    f"ORDER BY updated_at DESC LIMIT {MAX_PAGES_PER_RUN}"
)
paths = [r["path"] for r in recent]
print(f"connect: {len(paths)} pages updated in the last {CONNECT_LOOKBACK_HOURS}h")

# COMMAND ----------

committed_total = 0
proposed_total = 0
deferred_low_confidence = []

for path in paths:
    try:
        edges = wiki.propose_edges(path, min_similarity=MIN_SIMILARITY)
    except Exception as e:
        print(f"propose_edges failed for {path}: {e}")
        continue
    proposed_total += len(edges)
    high = [e for e in edges if e["confidence"] >= AUTO_COMMIT_THRESHOLD]
    low = [e for e in edges if e["confidence"] < AUTO_COMMIT_THRESHOLD]
    if high:
        committed_total += wiki.commit_edges(high)
    deferred_low_confidence.extend(
        {"path": path, **e} for e in low
    )

print(f"connect: proposed={proposed_total} committed={committed_total} "
      f"deferred_for_agent={len(deferred_low_confidence)}")

# COMMAND ----------

# MAGIC %md ## Phase 2: Lint — detect + log issues

# COMMAND ----------

checks = [
    ("orphan", orphan_pages_sql()),
    ("stale", stale_pages_sql(days=STALE_DAYS)),
    ("duplicate_path", duplicate_paths_sql()),
    ("broken_link", broken_links_sql()),
]

issues = []
for check_name, sql in checks:
    try:
        rows = run_sql(sql)
    except Exception as e:
        print(f"lint check {check_name} failed: {e}")
        continue
    for row in rows:
        issues.append({"check": check_name, "row": row})

print(f"lint: {len(issues)} issues found")

# COMMAND ----------

if issues:
    values = []
    for issue in issues:
        log_id = str(uuid.uuid4())
        details = json.dumps(issue, default=str).replace("'", "''")
        check = issue["check"]
        values.append(
            f"('{log_id}', 'lint', NULL, '{check}', '{details}', 'wiki_curate')"
        )
    w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(
            f"INSERT INTO {LOG_TABLE} "
            f"(log_id, op_type, path, query, details, created_by) "
            f"VALUES {', '.join(values)}"
        ),
        wait_timeout="30s",
    )
    print(f"lint: logged {len(issues)} issues to {LOG_TABLE}")

# COMMAND ----------

# MAGIC %md ## Phase 3: Optional deterministic repair

# COMMAND ----------

if REPAIR_BROKEN_LINKS:
    deleted = wiki.fix_broken_links()
    print(f"repair: deleted {deleted} broken-link rows")
else:
    print("repair: skipped (repair_broken_links=false)")

# COMMAND ----------

# MAGIC %md ## Summary

# COMMAND ----------

summary = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "connect": {
        "pages_scanned": len(paths),
        "edges_proposed": proposed_total,
        "edges_committed": committed_total,
        "deferred_low_confidence": len(deferred_low_confidence),
        "auto_commit_threshold": AUTO_COMMIT_THRESHOLD,
    },
    "lint": {
        "issues": len(issues),
        "by_check": {
            c: sum(1 for i in issues if i["check"] == c)
            for c in ("orphan", "stale", "duplicate_path", "broken_link")
        },
    },
    "repair": {"broken_links_deleted": deleted if REPAIR_BROKEN_LINKS else None},
}
print(json.dumps(summary, indent=2))
