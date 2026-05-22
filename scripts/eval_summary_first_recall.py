"""A/B retrieval-quality eval for the 0.7.8 summary-first write path.

Writes 10 paired pages to a workspace:
  eval/summary_first/concat/<slug>   — control: content_text = concat(summary, body)
  eval/summary_first/summary/<slug>  — treatment: content_text = dense LLM summary

Both pages carry the SAME `content.body` (raw transcript). They differ only
in what Vector Search embeds — content_text. Two handcrafted paraphrased
queries per topic (one intent-focused, one artifact-focused) are run, and
the rank of each arm's page is recorded.

Metrics:
  - recall@1, @3, @5, @10 per arm
  - mean rank per arm (page-not-in-top-K counts as K+1)
  - wins / ties / losses per topic (Arm B beats Arm A on rank)

Run (all four env vars REQUIRED):

    DATABRICKS_CONFIG_PROFILE=<profile> \
      WIKIBRICKS_CATALOG=<catalog> \
      WIKIBRICKS_SCHEMA=<schema> \
      WIKIBRICKS_WAREHOUSE_ID=<warehouse_id> \
      uv run python scripts/eval_summary_first_recall.py \
        --output docs/research/2026-05-22-summary-first-eval-results

Outputs `<output>.csv` (one row per topic+query) and `<output>.md`
(human-readable writeup with aggregate metrics).
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone

# Local source on path so we can import from this repo without install.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from databricks.sdk import WorkspaceClient  # noqa: E402

from wikibricks.client import WikiClient  # noqa: E402
from wikibricks_recorder import auto_summary, page_builder  # noqa: E402

CONCAT_PREFIX = "eval/summary_first/concat"
SUMMARY_PREFIX = "eval/summary_first/summary"
K_LIST = (1, 3, 5, 10)
MAX_K = max(K_LIST)
SYNC_WAIT_SECONDS = 90

TOPICS = [
    {
        "slug": "stripe-webhooks",
        "first_prompt": (
            "Refactor the payments module so it uses the new Stripe webhook "
            "signature verification. The current code in payments/webhook.py "
            "uses a custom HMAC implementation that doesn't match Stripe's "
            "expected signing scheme. Replace it with stripe.Webhook.construct_event "
            "and verify against STRIPE_WEBHOOK_SECRET from environment. Add a "
            "unit test that covers replay attacks (same timestamp, same payload) "
            "and a regression test for the legacy v1 payload shape we still "
            "receive from grandfathered customers. After the refactor, make "
            "sure the existing webhook_logged_at metric still increments. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Read", "Edit", "Bash"],
        "follow_ups": ["now ship the change", "did you remember the metric?"],
        "queries": [
            "how did we verify Stripe webhook signatures?",
            "which file did we change for Stripe webhook auth?",
        ],
    },
    {
        "slug": "lakeflow-backfill",
        "first_prompt": (
            "Add a Lakeflow Job that backfills Q1 invoices into the "
            "finance.bronze.invoices_raw table. The source is an S3 bucket "
            "s3://acme-finance-archive/q1/ with date-partitioned JSON files. "
            "The job should be idempotent (re-runs don't duplicate rows) and "
            "use Auto Loader with schema evolution. Schedule it as a one-shot "
            "and tag the run with backfill=q1_2026 so the platform team can "
            "find it. Make sure the bundle is in dabs/finance/. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Read", "Edit", "Edit", "Bash"],
        "follow_ups": ["validate the bundle once", "deploy to dev"],
        "queries": [
            "how do we backfill Q1 invoices with Lakeflow?",
            "where does the Auto Loader job read invoices from?",
        ],
    },
    {
        "slug": "react-rerender",
        "first_prompt": (
            "Fix the React hook in apps/dashboard/src/hooks/useFilter.tsx that "
            "re-renders on every keystroke in the search box. The hook currently "
            "spreads the entire filter state into a useEffect dependency array, "
            "so any debounced state change retriggers the underlying GraphQL "
            "query. Refactor to use useMemo for the derived filter and "
            "useDeferredValue for the search term. Confirm the network panel "
            "shows one query per debounce window, not per keystroke. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Edit", "Bash"],
        "follow_ups": ["also add a test"],
        "queries": [
            "how did we fix the React hook re-render on every keystroke?",
            "which hook caused excessive GraphQL queries from the search box?",
        ],
    },
    {
        "slug": "databricks-obo",
        "first_prompt": (
            "Set up Databricks On-Behalf-Of (OBO) auth for a FastAPI app "
            "deployed on Databricks Apps. The app needs to call the SQL "
            "warehouse and Unity Catalog as the end user, not as the app's "
            "service principal. Wire up the OBO token resolution in "
            "app/auth.py — read X-Forwarded-Access-Token from the proxy, "
            "validate the audience claim, and pass the token through to the "
            "Databricks SDK WorkspaceClient. Document the workspace config "
            "(workspace setting + app resource role) needed for OBO to work. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Edit", "Edit"],
        "follow_ups": ["confirm it works locally"],
        "queries": [
            "how do we set up Databricks OBO auth in a FastAPI app?",
            "which header carries the on-behalf-of token through the proxy?",
        ],
    },
    {
        "slug": "slow-orders-query",
        "first_prompt": (
            "Investigate slow queries on the orders fact table at "
            "warehouse.gold.orders_fact. A specific aggregation rolling up "
            "monthly revenue by region takes 90 seconds despite the table "
            "being only 12M rows. EXPLAIN shows a full table scan + a "
            "broadcast hash join with the customer dim. Recommend Z-ORDER "
            "BY order_date, region_id and OPTIMIZE the table; consider "
            "adding a materialized view for the monthly aggregate. Measure "
            "before/after with %sql ANALYZE TABLE compute statistics. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Bash", "Bash"],
        "follow_ups": ["run the OPTIMIZE on dev first"],
        "queries": [
            "how did we speed up the monthly revenue rollup on orders_fact?",
            "what Z-ORDER columns did we pick for the orders table?",
        ],
    },
    {
        "slug": "snowflake-uc-migration",
        "first_prompt": (
            "Migrate the SP_CALCULATE_RISK Snowflake stored procedure to a "
            "Unity Catalog SQL function. The proc takes a portfolio_id and "
            "an as_of_date and returns a struct with var_99, cvar_99, and "
            "max_drawdown. The SQL body is straightforward CTEs over the "
            "positions and price_history tables. Rewrite as a UC SQL UDF in "
            "the risk.functions schema, then update the three downstream "
            "dashboards that call SP_CALCULATE_RISK to use the new function. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Edit", "Edit", "Bash"],
        "follow_ups": ["validate one dashboard end-to-end"],
        "queries": [
            "how did we migrate the SP_CALCULATE_RISK proc to Unity Catalog?",
            "what does the new risk UC function return?",
        ],
    },
    {
        "slug": "vs-hybrid-notebook",
        "first_prompt": (
            "Wire up Vector Search HYBRID retrieval in the support_kb notebook. "
            "The notebook currently uses pure ANN over the support_articles "
            "Delta table. Switch to HYBRID via vector_search() TVF with "
            "query_type='HYBRID' so keyword matches on product names ('Snowsight', "
            "'Genie Space') aren't drowned by semantic neighbours. After the "
            "switch, validate that the top result for 'how do I share a Genie "
            "Space' actually links to the sharing doc, not to a generic "
            "permissions article. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Edit", "Bash"],
        "follow_ups": [],
        "queries": [
            "how did we switch Vector Search to HYBRID mode?",
            "which notebook now uses HYBRID retrieval for the support KB?",
        ],
    },
    {
        "slug": "uv-wheel-publish",
        "first_prompt": (
            "Package and publish the latest version of the acme-analytics "
            "Python wheel from a uv project. Bump pyproject.toml from 1.4.2 "
            "to 1.5.0, run uv build, smoke-test the wheel in a fresh venv, "
            "and publish to the internal PyPI at pypi.acme.internal via "
            "uv publish. Update the install instructions in README.md to "
            "reference the new version, and tag v1.5.0 in git. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Edit", "Bash", "Bash"],
        "follow_ups": ["any breaking changes?"],
        "queries": [
            "how do we publish the acme-analytics wheel with uv?",
            "what version did acme-analytics bump to?",
        ],
    },
    {
        "slug": "lakebase-pool-exhaustion",
        "first_prompt": (
            "Diagnose why the Streamlit Lakebase dashboard runs out of "
            "psycopg2 connections under load. The app uses a single global "
            "connection pool with max_size=5, but every Streamlit script "
            "rerun grabs a fresh connection without releasing the prior one, "
            "so a single user clicking around exhausts the pool. Switch to a "
            "scoped connection pattern via contextlib.contextmanager and "
            "ensure connections are released on rerun. Validate by stress-"
            "testing with 50 concurrent sessions. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Read", "Edit", "Bash"],
        "follow_ups": ["add a connection-leak test"],
        "queries": [
            "why did the Lakebase Streamlit app run out of psycopg2 connections?",
            "how did we scope the Lakebase pool to avoid leaks on rerun?",
        ],
    },
    {
        "slug": "genie-streamlit-dashboard",
        "first_prompt": (
            "Build a Streamlit dashboard backed by a Genie Space for the "
            "sales-ops team. The dashboard should let sales reps ask natural-"
            "language questions about the pipeline (e.g. 'which deals slipped "
            "from Q1 to Q2?') and render the Genie response inline. Use the "
            "/api/2.0/genie/spaces/<space-id>/conversations endpoint, manage "
            "the conversation_id across reruns, and render any returned SQL "
            "+ table results with st.dataframe. Deploy as a Databricks App. "
            + ("x" * 1800)
        ),
        "tool_events": ["Read", "Edit", "Edit", "Bash"],
        "follow_ups": ["wire OBO in too"],
        "queries": [
            "how do we wire a Streamlit dashboard to a Genie Space?",
            "which Genie API endpoint did we call for natural-language questions?",
        ],
    },
]


def _build_state(topic: dict) -> dict:
    sid = f"eval-{topic['slug']}-{uuid.uuid4().hex[:6]}"
    started = datetime.now(timezone.utc).isoformat()
    events: list[dict] = [
        {"kind": "prompt", "ts": started, "prompt": topic["first_prompt"]},
    ]
    for tool in topic["tool_events"]:
        events.append({"kind": "tool", "ts": started, "tool_name": tool})
    for fp in topic["follow_ups"]:
        events.append({"kind": "prompt", "ts": started, "prompt": fp})
    return {
        "session_id": sid,
        "first_prompt": topic["first_prompt"],
        "events": events,
        "started_at": started,
        "cwd": f"/eval/summary_first/{topic['slug']}",
        "model": "claude-opus-4-7",
    }


def _rank_of(hits: list[dict], target_path: str) -> int:
    """1-based rank, or MAX_K + 1 if not in the top MAX_K."""
    for i, h in enumerate(hits, start=1):
        if h.get("path") == target_path:
            return i
    return MAX_K + 1


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"missing required env var: {name}")
    return v


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/research/2026-05-22-summary-first-eval-results",
        help="Output path without extension; .csv and .md will be appended.",
    )
    parser.add_argument(
        "--skip-writes",
        action="store_true",
        help="Skip the write+sync phase; assume pages already exist.",
    )
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Skip the VS sync sleep (only safe with --skip-writes).",
    )
    args = parser.parse_args()

    catalog = _require("WIKIBRICKS_CATALOG")
    schema = _require("WIKIBRICKS_SCHEMA")
    warehouse_id = _require("WIKIBRICKS_WAREHOUSE_ID")
    profile = _require("DATABRICKS_CONFIG_PROFILE")

    os.environ["WIKIBRICKS_CATALOG"] = catalog
    os.environ["WIKIBRICKS_SCHEMA"] = schema

    ws = WorkspaceClient(profile=profile)
    client = WikiClient(warehouse_id=warehouse_id, workspace_client=ws)

    print(f"[eval] {catalog}.{schema} via warehouse={warehouse_id}")
    print(f"[eval] {len(TOPICS)} topics, "
          f"{sum(len(t['queries']) for t in TOPICS)} queries")

    # ---- WRITE PHASE -------------------------------------------------------

    summaries: dict[str, str] = {}
    if not args.skip_writes:
        for topic in TOPICS:
            slug = topic["slug"]
            state = _build_state(topic)
            print(f"[eval] {slug}: generating summary via Haiku 4.5...")
            summary = auto_summary.generate_summary(
                state, {"enabled": True}, ws
            )
            if summary is None:
                print(f"[eval] {slug}: FAIL — generate_summary returned None")
                return 2
            summaries[slug] = summary

            content = page_builder.session_content(state, dense_summary=summary)

            # Control arm: write WITHOUT dense_summary so content.summary
            # falls back to truncated first_prompt; content_text becomes
            # concat(truncated_summary, body) — the pre-0.7.8 shape.
            legacy_content = page_builder.session_content(state)
            client.write_page(
                f"{CONCAT_PREFIX}/{slug}",
                title=f"[eval/concat] {slug}",
                content_json=legacy_content,
                tags=["eval", "summary_first", "arm:concat"],
            )

            # Treatment arm: dense summary IS content.summary AND
            # content_text_override (the 0.7.8 path).
            client.write_page(
                f"{SUMMARY_PREFIX}/{slug}",
                title=f"[eval/summary] {slug}",
                content_json=content,
                tags=["eval", "summary_first", "arm:summary"],
                content_text_override=summary,
            )
            print(f"[eval] {slug}: wrote both arms (summary={len(summary)}c)")

        print("[eval] triggering VS sync...")
        client.sync_index()
        if not args.no_sleep:
            print(f"[eval] sleeping {SYNC_WAIT_SECONDS}s for VS to ingest...")
            time.sleep(SYNC_WAIT_SECONDS)

    # ---- QUERY PHASE -------------------------------------------------------

    rows: list[dict] = []
    for topic in TOPICS:
        slug = topic["slug"]
        concat_path = f"{CONCAT_PREFIX}/{slug}"
        summary_path = f"{SUMMARY_PREFIX}/{slug}"
        for q_i, query in enumerate(topic["queries"], start=1):
            hits = client.search(
                query,
                mode="HYBRID",
                num_results=MAX_K,
                rerank_with_pagerank=False,
                rerank_by_citations=False,
                include_ephemeral=True,
            )
            rank_a = _rank_of(hits, concat_path)
            rank_b = _rank_of(hits, summary_path)
            rows.append({
                "topic": slug,
                "query_id": q_i,
                "query": query,
                "rank_concat": rank_a,
                "rank_summary": rank_b,
                "winner": (
                    "summary" if rank_b < rank_a
                    else "concat" if rank_a < rank_b
                    else "tie"
                ),
            })
            print(f"[eval] {slug} q{q_i}: rank_concat={rank_a:>3} "
                  f"rank_summary={rank_b:>3}")

    # ---- METRICS -----------------------------------------------------------

    metrics = _compute_metrics(rows)
    _print_metrics(metrics, rows)
    _write_csv(rows, args.output + ".csv")
    _write_markdown(rows, metrics, summaries, args.output + ".md")
    print(f"[eval] wrote {args.output}.csv and {args.output}.md")
    return 0


def _recall_at_k(ranks: list[int], k: int) -> float:
    return sum(1 for r in ranks if r <= k) / len(ranks) if ranks else 0.0


def _compute_metrics(rows: list[dict]) -> dict:
    concat_ranks = [r["rank_concat"] for r in rows]
    summary_ranks = [r["rank_summary"] for r in rows]
    n = len(rows)
    wins = sum(1 for r in rows if r["winner"] == "summary")
    ties = sum(1 for r in rows if r["winner"] == "tie")
    losses = sum(1 for r in rows if r["winner"] == "concat")
    return {
        "n_queries": n,
        "concat": {
            f"recall@{k}": _recall_at_k(concat_ranks, k) for k in K_LIST
        } | {"mean_rank": statistics.mean(concat_ranks) if concat_ranks else 0},
        "summary": {
            f"recall@{k}": _recall_at_k(summary_ranks, k) for k in K_LIST
        } | {"mean_rank": statistics.mean(summary_ranks) if summary_ranks else 0},
        "delta": {
            f"recall@{k}": _recall_at_k(summary_ranks, k) - _recall_at_k(concat_ranks, k)
            for k in K_LIST
        },
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "win_rate": wins / n if n else 0,
    }


def _print_metrics(metrics: dict, rows: list[dict]) -> None:
    print("\n[eval] === Results ===")
    print(f"[eval] {metrics['n_queries']} queries across {len({r['topic'] for r in rows})} topics")
    print(f"[eval] wins:   {metrics['wins']:>3}  "
          f"ties: {metrics['ties']:>3}  "
          f"losses: {metrics['losses']:>3}  "
          f"win_rate: {metrics['win_rate']:.0%}")
    for k in K_LIST:
        a = metrics['concat'][f'recall@{k}']
        b = metrics['summary'][f'recall@{k}']
        d = metrics['delta'][f'recall@{k}']
        sign = "+" if d >= 0 else ""
        print(f"[eval] recall@{k}: concat={a:.0%}  summary={b:.0%}  delta={sign}{d:+.0%}")
    print(f"[eval] mean rank: concat={metrics['concat']['mean_rank']:.2f}  "
          f"summary={metrics['summary']['mean_rank']:.2f}")


def _write_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(
    rows: list[dict], metrics: dict, summaries: dict[str, str], path: str
) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    delta_at_5 = metrics['delta']['recall@5']
    decision = (
        "FLIP DEFAULT TO ON in v0.7.9"
        if delta_at_5 >= 0.10 and metrics["win_rate"] >= 0.70
        else "KEEP OPT-IN, iterate on prompt"
    )
    lines = [
        "# A/B retrieval eval — 0.7.8 summary-first",
        "",
        f"**Date:** {datetime.now(timezone.utc):%Y-%m-%d}",
        f"**Topics:** {len({r['topic'] for r in rows})}  "
        f"**Queries:** {metrics['n_queries']}  "
        f"**Decision:** **{decision}**",
        "",
        "## Aggregate",
        "",
        "| Metric | concat (control) | summary (treatment) | Δ |",
        "|---|---|---|---|",
    ]
    for k in K_LIST:
        a = metrics['concat'][f'recall@{k}']
        b = metrics['summary'][f'recall@{k}']
        d = metrics['delta'][f'recall@{k}']
        lines.append(
            f"| recall@{k} | {a:.0%} | {b:.0%} | {d:+.0%} |"
        )
    lines.append(
        f"| mean rank | {metrics['concat']['mean_rank']:.2f} | "
        f"{metrics['summary']['mean_rank']:.2f} | "
        f"{metrics['summary']['mean_rank'] - metrics['concat']['mean_rank']:+.2f} |"
    )
    lines += [
        "",
        f"**Wins / Ties / Losses:** {metrics['wins']} / {metrics['ties']} / "
        f"{metrics['losses']}  (win_rate {metrics['win_rate']:.0%})",
        "",
        "## Per-query ranks",
        "",
        "| Topic | Q | rank_concat | rank_summary | winner |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['topic']} | {r['query_id']} | {r['rank_concat']} | "
            f"{r['rank_summary']} | {r['winner']} |"
        )
    lines += [
        "",
        "## Decision rule",
        "",
        "Per `2026-05-22-summary-first-eval-plan.md`: flip the default to ON",
        "in v0.7.9 if recall@5 lifts ≥ 10pp AND win_rate ≥ 70%. Otherwise",
        "leave opt-in and iterate on the system prompt.",
        "",
    ]
    if summaries:
        lines += ["## Generated summaries (one per topic)", ""]
        for slug, summary in summaries.items():
            lines.append(f"### {slug}")
            lines.append("")
            lines.append("```markdown")
            lines.append(summary)
            lines.append("```")
            lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
