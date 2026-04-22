"""HotpotQA benchmark - step 4: render benchmark_results.json → hotpotqa_results.html."""

import json
import os
from datetime import datetime

from databricks.sdk import WorkspaceClient

os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fe-vm-agent-marketplace")

CATALOG = "agent_marketplace_catalog"
SCHEMA = "wiki_hotpot"
WAREHOUSE_ID = "41754a8563a43a49"
IN_PATH = "benchmark_results.json"
OUT_PATH = "hotpotqa_results.html"


def fetch_corpus_size() -> dict:
    w = WorkspaceClient()
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"""
            SELECT
              (SELECT count(*) FROM {CATALOG}.{SCHEMA}.pages) AS pages,
              (SELECT count(*) FROM {CATALOG}.{SCHEMA}.links) AS links,
              (SELECT count(*) FROM {CATALOG}.{SCHEMA}.links WHERE link_type='supports') AS supports
        """,
        wait_timeout="30s",
    )
    row = r.result.data_array[0]
    return {"pages": int(row[0]), "links": int(row[1]), "supports": int(row[2])}


def fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def render(data: dict) -> str:
    modes = data["modes"]
    ablation_loo = data["link_graph_ablation_loo"]
    ablation_oracle = data["link_graph_ablation_oracle"]
    base_mode = data["ablation_base_mode"]
    corpus = data.get("corpus", {})
    n = data["n_queries"]
    k = data.get("retrieve_k", 20)
    ts = data.get("timestamp", "")

    mode_rows = "\n".join(
        f"""        <tr>
          <td><b>{m}</b></td>
          <td>{fmt_pct(s['recall@2'])}</td>
          <td>{fmt_pct(s['recall@10'])}</td>
          <td>{s['mrr']:.3f}</td>
          <td>{s['supporting_fact_f1']:.3f}</td>
          <td style="color:#666">{s['elapsed_s']:.0f}s</td>
        </tr>"""
        for m, s in modes.items()
    )

    base = modes[base_mode]
    loo_up_2 = ablation_loo["recall@2"] - base["recall@2"]
    loo_up_10 = ablation_loo["recall@10"] - base["recall@10"]
    or_up_2 = ablation_oracle["recall@2"] - base["recall@2"]
    or_up_10 = ablation_oracle["recall@10"] - base["recall@10"]

    def cell(v, pct=True):
        return f"{'+' if v >= 0 else ''}{v * 100:.1f} pp" if pct else f"{v:+.3f}"

    def cls(v):
        return "uplift-pos" if v > 0 else ("uplift-neg" if v < 0 else "")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>WikiBricks - HotpotQA Benchmark</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 880px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ border-bottom: 2px solid #FF3621; padding-bottom: 0.3rem; }}
  h2 {{ margin-top: 2rem; color: #1B3139; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; text-align: left; }}
  th {{ background: #f5f5f5; }}
  .num {{ font-variant-numeric: tabular-nums; }}
  .meta {{ color: #666; font-size: 0.9rem; }}
  .uplift-pos {{ color: #1a7f37; font-weight: 600; }}
  .uplift-neg {{ color: #cf222e; font-weight: 600; }}
  code {{ background: #f6f8fa; padding: 0.1rem 0.3rem; border-radius: 3px; }}
</style></head><body>

<h1>WikiBricks - HotpotQA Retrieval Benchmark</h1>
<p class="meta">Run at {ts} · {n:,} dev queries · corpus: {corpus.get('pages', '?'):,} pages,
   {corpus.get('links', '?'):,} links ({corpus.get('supports', '?'):,} <code>supports</code>)</p>

<p>HotpotQA is a multi-hop QA benchmark: every question requires evidence from <b>two</b>
Wikipedia pages. We use this to measure how well WikiBricks retrieves both supporting
pages - a task where cross-reference structure matters. Ground truth per query is the
set of relevant page paths; metrics follow the HotpotQA retrieval setup.</p>

<h2>Retrieval by query mode</h2>
<table class="num">
  <thead><tr>
    <th>Mode</th><th>recall@2</th><th>recall@10</th><th>MRR</th>
    <th>Supporting-fact F1</th><th>Wall time</th>
  </tr></thead>
  <tbody>
{mode_rows}
  </tbody>
</table>

<h2>Link-graph ablation</h2>
<p>Method: retrieve top-{k} with <b>{base_mode}</b>, expand each retrieved page with its
<code>link_type='supports'</code> neighbors, assign expansion candidates a score of
<code>parent_score × 0.9</code>, re-sort by score, truncate to top-10.</p>

<p><b>Two variants</b>:</p>
<ul>
  <li><b>LOO-masked</b> (honest): for query <i>q</i>, drop the single supports edge
      connecting <i>q</i>'s two own gold pages. Simulates a graph built without
      seeing <i>q</i> - measures what the graph contributes from <i>other</i> queries.</li>
  <li><b>Oracle</b> (upper bound): use the full dev-derived supports graph without
      masking. Overestimates uplift because the graph was built from the same labels
      we evaluate against.</li>
</ul>

<table class="num">
  <thead><tr>
    <th>Metric</th><th>{base_mode} alone</th>
    <th>+ supports (LOO)</th><th>Uplift</th>
    <th>+ supports (oracle)</th><th>Uplift</th>
  </tr></thead>
  <tbody>
    <tr><td>recall@2</td>
        <td>{fmt_pct(base['recall@2'])}</td>
        <td>{fmt_pct(ablation_loo['recall@2'])}</td>
        <td class="{cls(loo_up_2)}">{cell(loo_up_2)}</td>
        <td>{fmt_pct(ablation_oracle['recall@2'])}</td>
        <td class="{cls(or_up_2)}">{cell(or_up_2)}</td></tr>
    <tr><td>recall@10</td>
        <td>{fmt_pct(base['recall@10'])}</td>
        <td>{fmt_pct(ablation_loo['recall@10'])}</td>
        <td class="{cls(loo_up_10)}">{cell(loo_up_10)}</td>
        <td>{fmt_pct(ablation_oracle['recall@10'])}</td>
        <td class="{cls(or_up_10)}">{cell(or_up_10)}</td></tr>
  </tbody>
</table>

<p class="meta">Reading: LOO uplift near zero means the <code>supports</code> graph is
only valuable when it contains the test query's own edge. The graph is dev-label-derived
(each edge links two pages shown together in some dev question), so it does not generalize
across queries. A cross-reference graph built from Wikipedia hyperlinks or co-occurrence
would be expected to help more.</p>

<h2>Setup</h2>
<ul>
  <li>Embedding: <code>databricks-bge-large-en</code> on <code>content_text</code> (page summary + body)</li>
  <li>Vector Search endpoint: <code>wiki-vs-endpoint</code> · index: <code>{CATALOG}.{SCHEMA}.pages_index</code></li>
  <li>Query: 20 parallel SDK calls · <code>num_results=10</code> · K ∈ {{2, 10}}</li>
</ul>

<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} by <code>scripts/hotpot_04_render.py</code>.</p>

</body></html>
"""


def main():
    with open(IN_PATH) as f:
        data = json.load(f)
    data["corpus"] = fetch_corpus_size()
    html = render(data)
    with open(OUT_PATH, "w") as f:
        f.write(html)
    print(f"wrote {OUT_PATH} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
