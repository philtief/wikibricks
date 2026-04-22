"""Inspect `agent_traces` to tell whether real traffic is ready to promote.

The promote pipeline assumes queries cluster into recurring intents with
distinct sessions. That assumption held on the synthetic fixture (perfect
5x5 clusters). With real agent traffic it may not hold -- paraphrase drift,
long tail of one-offs, single power-users dominating a session.

This script reports what an operator needs to know before trusting the next
scheduled promote run:

  - trace count + distinct sessions in the last `window_days`
  - p50/p95/p99 query length
  - distribution of retrieved_paths list length
  - top-10 most-repeated queries (exact match) and their distinct-session count
  - how many would survive `filter_eligible_clusters` defaults at cluster_threshold=1.0
    (exact-match lower bound on eligible cluster count)
  - wiki_log event counts by action for the same window (promote_reject,
    promote_parse_fail, vs_sync, vs_sync_fail)

Run::

    DATABRICKS_CONFIG_PROFILE=fe-vm-agent-marketplace \\
        python scripts/diagnose_traces.py --window-days 7
"""

from __future__ import annotations

import argparse
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", "41754a8563a43a49")
TRACES_TABLE = os.environ.get("TRACES_TABLE", "agent_marketplace_catalog.wiki.agent_traces")
LOG_TABLE = os.environ.get("LOG_TABLE", "agent_marketplace_catalog.wiki.wiki_log")


def _run(ws: WorkspaceClient, sql: str) -> list[list]:
    resp = ws.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=sql.strip(), wait_timeout="30s",
    )
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL failed: {resp.status.error}")
    return resp.result.data_array or []


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=7)
    args = ap.parse_args()
    window = f"current_timestamp() - INTERVAL {args.window_days} DAYS"

    ws = WorkspaceClient()

    _section(f"volume — last {args.window_days}d")
    rows = _run(ws, f"""
        SELECT COUNT(*) AS n_traces,
               COUNT(DISTINCT session_id) AS n_sessions,
               MIN(timestamp) AS first_ts,
               MAX(timestamp) AS last_ts
        FROM {TRACES_TABLE}
        WHERE timestamp > {window}
    """)
    if rows and rows[0][0] and int(rows[0][0]) > 0:
        n, s, first_ts, last_ts = rows[0]
        print(f"  traces:          {n}")
        print(f"  distinct sessions: {s}")
        print(f"  window:          {first_ts} -> {last_ts}")
    else:
        print(f"  no traces in last {args.window_days}d — fixture seed or enable tracing")
        return

    _section("query length (chars)")
    rows = _run(ws, f"""
        SELECT
            percentile_approx(LENGTH(user_query), 0.50) AS p50,
            percentile_approx(LENGTH(user_query), 0.95) AS p95,
            percentile_approx(LENGTH(user_query), 0.99) AS p99,
            MAX(LENGTH(user_query)) AS pmax
        FROM {TRACES_TABLE}
        WHERE timestamp > {window}
    """)
    p50, p95, p99, pmax = rows[0]
    print(f"  p50={p50}  p95={p95}  p99={p99}  max={pmax}")

    _section("retrieved_paths list length")
    rows = _run(ws, f"""
        SELECT
            percentile_approx(SIZE(retrieved_paths), 0.50) AS p50,
            percentile_approx(SIZE(retrieved_paths), 0.95) AS p95,
            MAX(SIZE(retrieved_paths)) AS pmax,
            SUM(CASE WHEN retrieved_paths IS NULL OR SIZE(retrieved_paths) = 0
                     THEN 1 ELSE 0 END) AS n_empty
        FROM {TRACES_TABLE}
        WHERE timestamp > {window}
    """)
    p50, p95, pmax, n_empty = rows[0]
    print(f"  p50={p50}  p95={p95}  max={pmax}  empty={n_empty}")

    _section("top-10 exact-match repeats")
    rows = _run(ws, f"""
        SELECT user_query,
               COUNT(*) AS n_hits,
               COUNT(DISTINCT session_id) AS n_sessions
        FROM {TRACES_TABLE}
        WHERE timestamp > {window}
        GROUP BY user_query
        ORDER BY n_hits DESC
        LIMIT 10
    """)
    if not rows:
        print("  (none)")
    for q, n_hits, n_sessions in rows:
        print(f"  hits={n_hits:>3}  sessions={n_sessions:>3}  {q[:80]!r}")

    _section("exact-match eligibility (min_members=5, min_distinct_sessions=3)")
    rows = _run(ws, f"""
        SELECT COUNT(*) AS n_eligible_exact_clusters
        FROM (
            SELECT user_query
            FROM {TRACES_TABLE}
            WHERE timestamp > {window}
            GROUP BY user_query
            HAVING COUNT(*) >= 5 AND COUNT(DISTINCT session_id) >= 3
        )
    """)
    n_exact = rows[0][0]
    print(f"  exact-match eligible clusters: {n_exact}")
    print("  (lower bound — cosine clustering will group paraphrases and find more)")

    _section("wiki_log events in same window")
    rows = _run(ws, f"""
        SELECT action, COUNT(*) AS n
        FROM {LOG_TABLE}
        WHERE created_at > {window}
          AND action IN ('promote', 'promote_reject', 'promote_parse_fail',
                         'vs_sync', 'vs_sync_fail')
        GROUP BY action
        ORDER BY n DESC
    """)
    if not rows:
        print("  (no promote/vs_sync events)")
    for action, n in rows:
        print(f"  {action:<22} {n}")

    _section("verdict")
    if int(n_exact) == 0 and args.window_days <= 7:
        print("  exact-match lower bound is 0. Either (a) run with --window-days 30")
        print("  to widen the window, (b) lower JUDGE_THRESHOLD, or (c) wait for more")
        print("  traffic. Paraphrase clustering via bge-large may still produce hits.")
    else:
        print(f"  looks healthy — expect at least {n_exact} promote candidate(s)")
        print("  per run at default thresholds.")


if __name__ == "__main__":
    main()
