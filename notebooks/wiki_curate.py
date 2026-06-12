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

# MAGIC %md
# MAGIC The `wikibricks` wheel is installed via the task-level serverless
# MAGIC environment in `resources/wiki_curate_job.yml`. No in-notebook
# MAGIC `%pip install` here — the bundle artifact path is substituted at
# MAGIC deploy time.

# COMMAND ----------

import json
import os
import uuid
from datetime import datetime, timedelta, timezone


def _read_widget(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
        return val or default
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        return default


# wikibricks.ops reads CATALOG/SCHEMA from os.environ at module import time —
# resolve the job's `catalog` / `schema` widgets into the env BEFORE importing.
os.environ["WIKIBRICKS_CATALOG"] = _read_widget("catalog", "main")
os.environ["WIKIBRICKS_SCHEMA"] = _read_widget("schema", "wiki")

from databricks.sdk import WorkspaceClient

from wikibricks import WikiClient
from wikibricks.curate_logic import (
    build_curate_summary,
    build_health_summary,
    classify_page_health,
    find_duplicate_paths,
    run_connect_phase,
)
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


WAREHOUSE_ID = _param("warehouse_id", "")
STALE_DAYS = int(_param("stale_days", "90"))
CONNECT_LOOKBACK_HOURS = int(_param("connect_lookback_hours", "48"))
AUTO_COMMIT_THRESHOLD = float(_param("auto_commit_threshold", "0.85"))
MIN_SIMILARITY = float(_param("min_similarity", "0.70"))
MAX_PAGES_PER_RUN = int(_param("max_pages_per_run", "500"))
PROPOSE_CONCURRENCY = int(_param("propose_concurrency", "8"))
REPAIR_BROKEN_LINKS = _param("repair_broken_links", "true").lower() == "true"

w = WorkspaceClient()
wiki = WikiClient(warehouse_id=WAREHOUSE_ID, workspace_client=w)


def run_sql(sql: str) -> list[dict]:
    # Delegate to the library executor (WikiClient._exec), which polls a cold
    # serverless warehouse to a terminal state before returning. The previous
    # inline execute_statement(wait_timeout="30s") crashed with
    # `AttributeError: 'NoneType' object has no attribute 'data_array'` whenever
    # the 04:00-UTC run hit a stopped warehouse and the statement was still
    # PENDING when the inline wait elapsed (result=None).
    resp = wiki._exec(sql)
    rows = resp.result.data_array if resp.result else []
    if not rows:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, r)) for r in rows]


# COMMAND ----------

# MAGIC %md ## Phase 1: Connect — propose edges for recently-written pages

# COMMAND ----------

since = (datetime.now(timezone.utc) - timedelta(hours=CONNECT_LOOKBACK_HOURS)).isoformat()
# Filter to user/agent-authored top-level pages. Segregate-produced chunks
# (parent_id IS NOT NULL, created_by='segregate') and promote-produced
# answers (created_by='promote') already have their links established and
# would otherwise dominate the lookback window after a big segregate run —
# turning a daily curate into a 21-min loop over ~100 stale candidates.
recent = run_sql(
    f"SELECT path FROM {PAGES_TABLE} "
    f"WHERE updated_at >= '{since}' "
    f"  AND parent_id IS NULL "
    f"  AND (created_by IS NULL OR created_by NOT IN ('segregate', 'promote')) "
    f"  AND path NOT LIKE '_meta/%' "
    f"ORDER BY updated_at DESC LIMIT {MAX_PAGES_PER_RUN}"
)
paths = [r["path"] for r in recent]
print(f"connect: {len(paths)} agent pages updated in the last {CONNECT_LOOKBACK_HOURS}h")

# Pre-fetch once instead of once-per-page inside propose_edges. With ~1k
# pages and ~100 candidates per run, this collapses 100 list_pages SQL
# round-trips (≈4-5s each) into 1.
all_pages = wiki.list_pages()

# COMMAND ----------

def _propose_one(path: str) -> list[dict]:
    return wiki.propose_edges(
        path, min_similarity=MIN_SIMILARITY, other_pages=all_pages,
    )


connect = run_connect_phase(
    paths=paths,
    propose_fn=_propose_one,
    commit_fn=wiki.commit_edges,
    auto_commit_threshold=AUTO_COMMIT_THRESHOLD,
    max_workers=PROPOSE_CONCURRENCY,
)
proposed_total = connect["edges_proposed"]
committed_total = connect["edges_committed"]
deferred_low_confidence = connect["deferred_low_confidence"]

if connect["failed_paths"]:
    failed = connect["failed_paths"]
    preview = failed[:5]
    suffix = "..." if len(failed) > 5 else ""
    print(f"connect: {len(failed)} paths failed: {preview}{suffix}")
print(f"connect: proposed={proposed_total} committed={committed_total} "
      f"deferred_for_agent={len(deferred_low_confidence)} "
      f"workers={PROPOSE_CONCURRENCY}")

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

# MAGIC %md ## Phase 4: Health — classify pages and write back status
# MAGIC
# MAGIC Deterministic checks only (size, empty, duplicates). LLM-based coherence
# MAGIC and segregation lives in a v2 phase the agent can opt into. Pages flagged
# MAGIC `oversize` are candidates for the future split-into-parent+chunks step.

# COMMAND ----------

# Alias page_id AS id and content_text AS body to match classify_page_health
# / find_duplicate_paths which read those keys (the actual table columns are
# page_id and content_text — see ops.py and the deploy notebook's CREATE TABLE).
pages_for_health = run_sql(
    f"SELECT page_id AS id, path, content_text AS body FROM {PAGES_TABLE} "
    f"WHERE path NOT LIKE '_meta/%' "
    f"ORDER BY updated_at DESC LIMIT {MAX_PAGES_PER_RUN}"
)
health_by_status: dict[str, list[str]] = {}
for row in pages_for_health:
    status, score = classify_page_health(row)
    health_by_status.setdefault(status, []).append(row["id"])

duplicates = find_duplicate_paths(pages_for_health)

# Batch one UPDATE per status bucket — far fewer round-trips than per-page.
for status, ids in health_by_status.items():
    if not ids:
        continue
    score = {"ok": 1.0, "oversize": 0.3, "empty": 0.0}.get(status, 0.5)
    id_list = ", ".join(f"'{i}'" for i in ids)
    w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(
            f"UPDATE {PAGES_TABLE} "
            f"SET health_status = '{status}', health_score = {score}, "
            f"last_health_check = current_timestamp() "
            f"WHERE page_id IN ({id_list})"
        ),
        wait_timeout="30s",
    )

by_status_count = {s: len(ids) for s, ids in health_by_status.items()}
print(f"health: checked={len(pages_for_health)} by_status={by_status_count} "
      f"duplicates={len(duplicates)}")

# COMMAND ----------

# MAGIC %md ## Summary

# COMMAND ----------

health = build_health_summary(
    pages_checked=len(pages_for_health),
    by_status=by_status_count,
    duplicates=len(duplicates),
)
summary = build_curate_summary(
    paths_scanned=len(paths),
    edges_proposed=proposed_total,
    edges_committed=committed_total,
    deferred_low_confidence=len(deferred_low_confidence),
    auto_commit_threshold=AUTO_COMMIT_THRESHOLD,
    lint_issues=issues,
    broken_links_deleted=deleted if REPAIR_BROKEN_LINKS else None,
    health=health,
)
summary["timestamp"] = datetime.now(timezone.utc).isoformat()
print(json.dumps(summary, indent=2))

# Persist the summary for cross-run auditing. Queryable as:
#   SELECT created_at, details FROM wiki_log WHERE op_type='curate_run'
wiki._log("curate_run", details=json.dumps(summary))  # noqa: SLF001
