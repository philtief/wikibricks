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
INTENT_TAIL_PREFIX = "eval/summary_first/intent_tail"
K_LIST = (1, 3, 5, 10)
MAX_K = max(K_LIST)
SYNC_WAIT_SECONDS = 90
INTENT_TAIL_MAX_CHARS = 2000

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
    parser.add_argument(
        "--mode",
        choices=("HYBRID", "ANN", "BOTH"),
        default="BOTH",
        help="Retrieval mode for the query phase. BOTH runs HYBRID and ANN "
             "in sequence and emits both to the report.",
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

            # Treatment arm B: dense summary IS content.summary AND
            # content_text_override (the pure 0.7.8 path).
            client.write_page(
                f"{SUMMARY_PREFIX}/{slug}",
                title=f"[eval/summary] {slug}",
                content_json=content,
                tags=["eval", "summary_first", "arm:summary"],
                content_text_override=summary,
            )

            # Treatment arm C: dense summary + raw first_prompt tail.
            # Closes the content_text length gap that v1 left open —
            # keyword-leg of HYBRID gets the original user intent text
            # appended, while the semantic leg still benefits from the
            # dense summary's structured framing.
            fp = state.get("first_prompt", "")[:INTENT_TAIL_MAX_CHARS]
            tail_override = summary + "\n\n## Raw intent\n" + fp
            client.write_page(
                f"{INTENT_TAIL_PREFIX}/{slug}",
                title=f"[eval/intent_tail] {slug}",
                content_json=content,
                tags=["eval", "summary_first", "arm:intent_tail"],
                content_text_override=tail_override,
            )
            print(f"[eval] {slug}: wrote 3 arms "
                  f"(summary={len(summary)}c tail={len(tail_override)}c)")

        print("[eval] triggering VS sync...")
        client.sync_index()
        if not args.no_sleep:
            print(f"[eval] sleeping {SYNC_WAIT_SECONDS}s for VS to ingest...")
            time.sleep(SYNC_WAIT_SECONDS)

    # ---- QUERY PHASE -------------------------------------------------------

    modes = ["HYBRID", "ANN"] if args.mode == "BOTH" else [args.mode]
    rows: list[dict] = []
    for mode in modes:
        for topic in TOPICS:
            slug = topic["slug"]
            concat_path = f"{CONCAT_PREFIX}/{slug}"
            summary_path = f"{SUMMARY_PREFIX}/{slug}"
            intent_tail_path = f"{INTENT_TAIL_PREFIX}/{slug}"
            for q_i, query in enumerate(topic["queries"], start=1):
                hits = client.search(
                    query,
                    mode=mode,
                    num_results=MAX_K,
                    rerank_with_pagerank=False,
                    rerank_by_citations=False,
                    include_ephemeral=True,
                )
                rank_a = _rank_of(hits, concat_path)
                rank_b = _rank_of(hits, summary_path)
                rank_c = _rank_of(hits, intent_tail_path)
                # Winner: lowest rank wins (1 is best). Ties → "tie".
                arms_ranked = sorted(
                    [("concat", rank_a), ("summary", rank_b), ("intent_tail", rank_c)],
                    key=lambda x: x[1],
                )
                winner = (
                    "tie"
                    if arms_ranked[0][1] == arms_ranked[1][1]
                    else arms_ranked[0][0]
                )
                rows.append({
                    "mode": mode,
                    "topic": slug,
                    "query_id": q_i,
                    "query": query,
                    "rank_concat": rank_a,
                    "rank_summary": rank_b,
                    "rank_intent_tail": rank_c,
                    "winner": winner,
                })
                print(f"[eval] {mode} {slug} q{q_i}: "
                      f"concat={rank_a:>2} summary={rank_b:>2} "
                      f"intent_tail={rank_c:>2}  → {winner}")

    # ---- METRICS -----------------------------------------------------------

    metrics_by_mode = {
        mode: _compute_metrics([r for r in rows if r["mode"] == mode])
        for mode in modes
    }
    for mode in modes:
        print(f"\n[eval] === Results ({mode}) ===")
        _print_metrics(metrics_by_mode[mode],
                       [r for r in rows if r["mode"] == mode])
    _write_csv(rows, args.output + ".csv")
    _write_markdown(rows, metrics_by_mode, summaries, args.output + ".md")
    print(f"[eval] wrote {args.output}.csv and {args.output}.md")
    return 0


def _recall_at_k(ranks: list[int], k: int) -> float:
    return sum(1 for r in ranks if r <= k) / len(ranks) if ranks else 0.0


ARMS = ("concat", "summary", "intent_tail")


def _compute_metrics(rows: list[dict]) -> dict:
    ranks = {arm: [r[f"rank_{arm}"] for r in rows] for arm in ARMS}
    n = len(rows)
    per_arm_wins = {arm: sum(1 for r in rows if r["winner"] == arm) for arm in ARMS}
    ties = sum(1 for r in rows if r["winner"] == "tie")
    out: dict = {
        "n_queries": n,
        "ties": ties,
        "wins": per_arm_wins,
    }
    for arm in ARMS:
        out[arm] = {f"recall@{k}": _recall_at_k(ranks[arm], k) for k in K_LIST}
        out[arm]["mean_rank"] = (
            statistics.mean(ranks[arm]) if ranks[arm] else 0
        )
        out[arm]["win_rate"] = per_arm_wins[arm] / n if n else 0
    return out


def _print_metrics(metrics: dict, rows: list[dict]) -> None:
    n = metrics["n_queries"]
    print(f"[eval] {n} queries across {len({r['topic'] for r in rows})} topics")
    print(f"[eval] wins: concat={metrics['wins']['concat']:>2}  "
          f"summary={metrics['wins']['summary']:>2}  "
          f"intent_tail={metrics['wins']['intent_tail']:>2}  "
          f"ties={metrics['ties']:>2}")
    header = f"[eval] {'metric':<12}" + "".join(f" {arm:>12}" for arm in ARMS)
    print(header)
    for k in K_LIST:
        row = f"[eval] {'recall@' + str(k):<12}"
        for arm in ARMS:
            row += f" {metrics[arm][f'recall@{k}']:>11.0%} "
        print(row)
    row = f"[eval] {'mean_rank':<12}"
    for arm in ARMS:
        row += f" {metrics[arm]['mean_rank']:>11.2f} "
    print(row)


def _write_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        if not rows:
            return
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(
    rows: list[dict], metrics_by_mode: dict[str, dict],
    summaries: dict[str, str], path: str,
) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    modes = list(metrics_by_mode.keys())

    def _best_summary_lift(mode: str) -> tuple[str, float]:
        """Best (arm, recall@5 lift over concat) for non-concat arms."""
        m = metrics_by_mode[mode]
        concat_at5 = m["concat"]["recall@5"]
        best = max(
            ((arm, m[arm]["recall@5"] - concat_at5) for arm in ("summary", "intent_tail")),
            key=lambda x: x[1],
        )
        return best

    decisions = []
    for mode in modes:
        arm, lift = _best_summary_lift(mode)
        m = metrics_by_mode[mode]
        win_rate = m[arm]["win_rate"]
        ok = lift >= 0.10 and win_rate >= 0.70
        verdict = "FLIP" if ok else "keep opt-in"
        decisions.append(
            f"- **{mode}**: best non-concat arm = `{arm}` "
            f"(recall@5 lift {lift:+.0%}, win_rate {win_rate:.0%}) → **{verdict}**"
        )

    lines = [
        "# A/B/C retrieval eval — 0.7.8 summary-first (v2 prompt)",
        "",
        f"**Date:** {datetime.now(timezone.utc):%Y-%m-%d}",
        f"**Topics:** {len({r['topic'] for r in rows})}  "
        f"**Queries per mode:** {metrics_by_mode[modes[0]]['n_queries']}  "
        f"**Modes:** {', '.join(modes)}  "
        f"**Arms:** concat (control), summary (v2 prompt), "
        f"intent_tail (v2 prompt + raw first_prompt)",
        "",
        "## Decisions",
        "",
        *decisions,
        "",
    ]
    for mode in modes:
        m = metrics_by_mode[mode]
        lines += [
            f"## Aggregate — {mode}",
            "",
            "| Metric | concat | summary | intent_tail |",
            "|---|---|---|---|",
        ]
        for k in K_LIST:
            lines.append(
                f"| recall@{k} | {m['concat'][f'recall@{k}']:.0%} | "
                f"{m['summary'][f'recall@{k}']:.0%} | "
                f"{m['intent_tail'][f'recall@{k}']:.0%} |"
            )
        lines.append(
            f"| mean_rank | {m['concat']['mean_rank']:.2f} | "
            f"{m['summary']['mean_rank']:.2f} | "
            f"{m['intent_tail']['mean_rank']:.2f} |"
        )
        lines.append(
            f"| wins | {m['wins']['concat']} | "
            f"{m['wins']['summary']} | {m['wins']['intent_tail']} |"
        )
        lines += ["", f"Ties: {m['ties']}", ""]

    lines += ["## Per-query ranks", ""]
    for mode in modes:
        lines += [
            f"### {mode}",
            "",
            "| Topic | Q | concat | summary | intent_tail | winner |",
            "|---|---|---|---|---|---|",
        ]
        for r in rows:
            if r["mode"] != mode:
                continue
            lines.append(
                f"| {r['topic']} | {r['query_id']} | {r['rank_concat']} | "
                f"{r['rank_summary']} | {r['rank_intent_tail']} | {r['winner']} |"
            )
        lines.append("")

    if summaries:
        lines += ["## Generated summaries (one per topic, v2 prompt)", ""]
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
