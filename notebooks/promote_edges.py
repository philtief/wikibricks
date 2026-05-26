# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Promote Staged Edges to the `links` Table
# MAGIC
# MAGIC Nightly deterministic promoter — no LLM judge in v0.7.10. Reads rows
# MAGIC from `edges_proposed WHERE status='pending'` and auto-confirms an edge
# MAGIC iff:
# MAGIC
# MAGIC - `target_path` exists in `pages`
# MAGIC - `evidence` is non-empty
# MAGIC - no identical `(source_path, target_path, link_type)` edge already in
# MAGIC   `links` (resolved by joining `pages` twice to compare `page_id`s)
# MAGIC
# MAGIC Passing rows are INSERTed into `links` with the original `confidence`
# MAGIC and `origin='auto_summary_envelope'`. The staged row is marked
# MAGIC `status='confirmed'`. Failing rows are marked `status='rejected'`
# MAGIC with a reason appended to `evidence`. Rejection is terminal for
# MAGIC v0.7.10 — there is no retry.
# MAGIC
# MAGIC More sophisticated judging (LLM scoring, path normalization, dedup
# MAGIC across alternate paths) is deferred to v0.7.12+.

# COMMAND ----------
# MAGIC %pip install /Volumes/<catalog>/<schema>/wheels/wikibricks-0.7.10-py3-none-any.whl

# COMMAND ----------
import json

from databricks.sdk import WorkspaceClient

from wikibricks.client import WikiClient

ws = WorkspaceClient()
warehouse_id = dbutils.widgets.get("warehouse_id")  # noqa: F821
catalog = dbutils.widgets.get("catalog")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
client = WikiClient(warehouse_id=warehouse_id, workspace_client=ws)

# COMMAND ----------
# Pull all pending rows
pending_sql = f"""
SELECT proposal_id, source_path, target_path, link_type, evidence, confidence
FROM {catalog}.{schema}.edges_proposed
WHERE status = 'pending'
ORDER BY created_at ASC
LIMIT 1000
"""
resp = ws.statement_execution.execute_statement(
    warehouse_id=warehouse_id, statement=pending_sql, wait_timeout="30s"
)
rows = resp.result.data_array if resp.result else []
print(f"pending edges: {len(rows)}")

# COMMAND ----------
# Validation queries — fast, no LLM
confirmed_ids: list[str] = []
rejected: list[tuple[str, str]] = []
for proposal_id, source_path, target_path, link_type, evidence, confidence in rows:
    if not evidence or not evidence.strip():
        rejected.append((proposal_id, "empty_evidence"))
        continue
    # Target exists?
    target_escaped = target_path.replace(chr(39), chr(39) + chr(39))
    check_sql = (
        f"SELECT 1 FROM {catalog}.{schema}.pages "
        f"WHERE path = '{target_escaped}' LIMIT 1"
    )
    r = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=check_sql, wait_timeout="30s"
    )
    if not r.result or not r.result.data_array:
        rejected.append((proposal_id, "target_missing"))
        continue
    # Duplicate in links? Resolve source_path + target_path → page_ids via
    # a double-JOIN on pages — the links table is keyed by page_id, not path.
    source_escaped = source_path.replace(chr(39), chr(39) + chr(39))
    dup_sql = (
        f"SELECT 1 FROM {catalog}.{schema}.links l "
        f"JOIN {catalog}.{schema}.pages src ON src.page_id = l.source_page_id "
        f"JOIN {catalog}.{schema}.pages tgt ON tgt.page_id = l.target_page_id "
        f"WHERE src.path = '{source_escaped}' "
        f"AND tgt.path = '{target_escaped}' "
        f"AND l.link_type = '{link_type}' LIMIT 1"
    )
    r = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=dup_sql, wait_timeout="30s"
    )
    if r.result and r.result.data_array:
        rejected.append((proposal_id, "duplicate"))
        continue
    confirmed_ids.append(proposal_id)

print(f"confirmed: {len(confirmed_ids)}  rejected: {len(rejected)}")

# COMMAND ----------
# Update statuses + insert into links
if confirmed_ids:
    ids_list = ",".join(f"'{i}'" for i in confirmed_ids)
    # Update status FIRST so the INSERT below sees the same rowset — but
    # we filter the INSERT by proposal_id IN (...) anyway, so order is safe.
    ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=f"""
            UPDATE {catalog}.{schema}.edges_proposed
            SET status = 'confirmed'
            WHERE proposal_id IN ({ids_list})
        """,
        wait_timeout="30s",
    )
    # Insert into links — the links table is keyed by source_page_id /
    # target_page_id (see src/wikibricks/ops.py create_tables_sql). The
    # staged rows carry paths, so we JOIN edges_proposed → pages twice
    # to resolve both endpoints into page_ids before the INSERT.
    ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=f"""
            INSERT INTO {catalog}.{schema}.links
            (source_page_id, target_page_id, link_type, confidence, origin, created_at)
            SELECT
                src.page_id AS source_page_id,
                tgt.page_id AS target_page_id,
                ep.link_type,
                ep.confidence,
                'auto_summary_envelope' AS origin,
                current_timestamp() AS created_at
            FROM {catalog}.{schema}.edges_proposed ep
            JOIN {catalog}.{schema}.pages src ON src.path = ep.source_path
            JOIN {catalog}.{schema}.pages tgt ON tgt.path = ep.target_path
            WHERE ep.proposal_id IN ({ids_list})
        """,
        wait_timeout="30s",
    )

if rejected:
    for proposal_id, reason in rejected:
        ws.statement_execution.execute_statement(
            warehouse_id=warehouse_id,
            statement=(
                f"UPDATE {catalog}.{schema}.edges_proposed "
                f"SET status = 'rejected', "
                f"evidence = concat(coalesce(evidence, ''), ' [rejected: {reason}]') "
                f"WHERE proposal_id = '{proposal_id}'"
            ),
            wait_timeout="30s",
        )

# COMMAND ----------
# Telemetry — one summary row per run.
client._log(
    "promote_edge",
    details=json.dumps({
        "confirmed": len(confirmed_ids),
        "rejected": len(rejected),
        "rejected_reasons": {
            r: sum(1 for _, rr in rejected if rr == r)
            for r in {"empty_evidence", "target_missing", "duplicate"}
        },
    }),
)
print("DONE")
