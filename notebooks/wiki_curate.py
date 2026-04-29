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

# MAGIC %pip install /Volumes/<catalog>/<schema>/wheels/wikibricks-0.1.5-py3-none-any.whl
# MAGIC # ^ Update path to where the wheel lives in your workspace.
# MAGIC %restart_python

# COMMAND ----------

import json
import uuid
from datetime import datetime, timedelta, timezone

from databricks.sdk import WorkspaceClient

from wikibricks import WikiClient
from wikibricks.curate_logic import (
    build_curate_summary,
    build_health_summary,
    classify_page_health,
    find_duplicate_paths,
    partition_by_confidence,
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
    high, low = partition_by_confidence(edges, AUTO_COMMIT_THRESHOLD)
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

# MAGIC %md ## Phase 4: Health — classify pages and write back status
# MAGIC
# MAGIC Deterministic checks only (size, empty, duplicates). LLM-based coherence
# MAGIC and segregation lives in a v2 phase the agent can opt into. Pages flagged
# MAGIC `oversize` are candidates for the future split-into-parent+chunks step.

# COMMAND ----------

pages_for_health = run_sql(
    f"SELECT id, path, body FROM {PAGES_TABLE} "
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
            f"WHERE id IN ({id_list})"
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
