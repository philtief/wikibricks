"""Seed a synthetic `agent_traces` table for end-to-end promote validation.

Creates 20 rows across 4 intent clusters (vector-search modes, delta merge,
unity catalog permissions, vs index sync). Each cluster has 5 members and 5
distinct session_ids so it passes `filter_eligible_clusters` defaults.

Also ensures `promote_checkpoint` exists and is empty, so the next promote run
reads the full fixture window.

Run:
    DATABRICKS_CONFIG_PROFILE=<your-profile> \
        WIKIBRICKS_WAREHOUSE_ID=<your-warehouse-id> \
        WIKIBRICKS_TRACES_TABLE=<catalog>.<schema>.agent_traces \
        python scripts/seed_traces_fixture.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

from databricks.sdk import WorkspaceClient

WAREHOUSE_ID = os.environ.get("WIKIBRICKS_WAREHOUSE_ID") or sys.exit(
    "WIKIBRICKS_WAREHOUSE_ID env var required"
)
TRACES_TABLE = os.environ.get("WIKIBRICKS_TRACES_TABLE") or sys.exit(
    "WIKIBRICKS_TRACES_TABLE env var required (e.g. <catalog>.<schema>.agent_traces)"
)
CHECKPOINT_TABLE = os.environ.get(
    "WIKIBRICKS_CHECKPOINT_TABLE",
    ".".join(TRACES_TABLE.split(".")[:-1] + ["promote_checkpoint"]),
)

CLUSTERS = [
    {
        "intent": "vs_modes",
        "queries": [
            "What search modes does Databricks Vector Search support?",
            "What search modes are available in Databricks Vector Search?",
            "What query modes does Databricks Vector Search support?",
            "What are the search modes in Databricks Vector Search?",
            "What modes can I use with Databricks Vector Search?",
        ],
        "answer": (
            "Databricks Vector Search exposes three modes on the same index: "
            "ANN (pure dense), FULL_TEXT (BM25 keyword), and HYBRID "
            "(reciprocal-rank fusion). Default to HYBRID; A/B against ANN when "
            "queries are paraphrases of corpus text."
        ),
        "sources": ["topics/vector-search", "comparisons/search-modes"],
    },
    {
        "intent": "delta_merge",
        "queries": [
            "How do I MERGE INTO a Delta table?",
            "How do I run a MERGE INTO on a Delta table?",
            "How do I write a MERGE INTO statement for a Delta table?",
            "How do I use MERGE INTO to update a Delta table?",
            "How do I execute MERGE INTO against a Delta table?",
        ],
        "answer": (
            "Use MERGE INTO <target> USING <source> ON <match-keys> WHEN "
            "MATCHED THEN UPDATE SET ... WHEN NOT MATCHED THEN INSERT ... "
            "MERGE is the idempotent ingest primitive; prefer it over INSERT "
            "OVERWRITE for incremental loads."
        ),
        "sources": ["topics/getting-started"],
    },
    {
        "intent": "uc_perms",
        "queries": [
            "How do I grant a service principal access to a Unity Catalog table?",
            "How do I give a service principal access to a Unity Catalog table?",
            "How do I grant a service principal permissions on a Unity Catalog table?",
            "How do I allow a service principal to read a Unity Catalog table?",
            "How do I authorize a service principal on a Unity Catalog table?",
        ],
        "answer": (
            "GRANT USE CATALOG, USE SCHEMA, then SELECT/MODIFY on the schema "
            "or table. Service principals also need CAN_USE on the SQL "
            "warehouse they run against since SPs aren't in the default "
            "`users` group."
        ),
        "sources": ["guides/setup"],
    },
    {
        "intent": "vs_sync",
        "queries": [
            "How do I sync a Delta table to a Vector Search index?",
            "How do I sync Delta data into a Vector Search index?",
            "How do I set up a Delta sync for a Vector Search index?",
            "How do I configure a Vector Search index to sync from Delta?",
            "How do I keep a Vector Search index in sync with a Delta table?",
        ],
        "answer": (
            "Create a DELTA_SYNC index with TRIGGERED pipeline type, primary "
            "key matching your source, and an embedding column spec. Time to "
            "READY is ~1 minute per 3k rows on `databricks-bge-large-en`."
        ),
        "sources": ["topics/architecture/overview"],
    },
]


def build_rows() -> list[dict]:
    # Use recent timestamps so the rows fall inside whatever silver window the
    # promote notebook computes on this run (checkpoint + 7 day catch-up cap).
    rows = []
    base_time = datetime.now(timezone.utc) - timedelta(minutes=2)
    for c_idx, cluster in enumerate(CLUSTERS):
        for q_idx, q in enumerate(cluster["queries"]):
            rows.append({
                "trace_id": str(uuid.uuid4()),
                "session_id": f"sess-{c_idx}-{q_idx}",  # one distinct session per query
                "user_query": q,
                "model_response": cluster["answer"],
                "retrieved_paths": cluster["sources"],
                "timestamp": (
                    base_time + timedelta(seconds=c_idx * 10 + q_idx)
                ).isoformat(),
            })
    return rows


def run_sql(w: WorkspaceClient, sql: str) -> None:
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql,
        wait_timeout="30s",
    )
    if resp.status and resp.status.state.value not in ("SUCCEEDED",):
        raise RuntimeError(f"SQL failed: {resp.status.error}\n{sql[:400]}")


def main() -> None:
    w = WorkspaceClient()

    print(f"creating {TRACES_TABLE}")
    run_sql(w, f"""
        CREATE TABLE IF NOT EXISTS {TRACES_TABLE} (
            trace_id         STRING    NOT NULL,
            session_id       STRING    NOT NULL,
            user_query       STRING    NOT NULL,
            model_response   STRING    NOT NULL,
            retrieved_paths  ARRAY<STRING>,
            timestamp        TIMESTAMP NOT NULL
        ) USING DELTA
    """)

    # Clean any previous fixture rows so re-running is idempotent.
    run_sql(w, f"DELETE FROM {TRACES_TABLE} WHERE session_id LIKE 'sess-%'")

    rows = build_rows()
    print(f"inserting {len(rows)} fixture rows")
    values_sql = ", ".join(
        "("
        f"'{r['trace_id']}', "
        f"'{r['session_id']}', "
        f"'{r['user_query'].replace(chr(39), chr(39) * 2)}', "
        f"'{r['model_response'].replace(chr(39), chr(39) * 2)}', "
        f"ARRAY({', '.join(repr(p) for p in r['retrieved_paths'])}), "
        f"TIMESTAMP '{r['timestamp']}'"
        ")"
        for r in rows
    )
    run_sql(w, f"INSERT INTO {TRACES_TABLE} VALUES {values_sql}")

    # Ensure the checkpoint table exists (the promote notebook needs it) and
    # reset it so the next run sees all fixture rows.
    run_sql(w, f"""
        CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
            checkpoint_id     STRING    NOT NULL,
            last_watermark_ts TIMESTAMP NOT NULL,
            updated_at        TIMESTAMP
        ) USING DELTA
    """)
    run_sql(w, f"""
        DELETE FROM {CHECKPOINT_TABLE}
        WHERE checkpoint_id = 'promote'
    """)

    print(json.dumps({
        "traces_table": TRACES_TABLE,
        "rows_inserted": len(rows),
        "clusters": len(CLUSTERS),
        "intent_labels": [c["intent"] for c in CLUSTERS],
    }, indent=2))


if __name__ == "__main__":
    main()
