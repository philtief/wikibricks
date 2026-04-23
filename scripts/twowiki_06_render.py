"""2WikiMultiHopQA - step 6: render metrics.json → twowiki_results.html."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from databricks.sdk import WorkspaceClient

CATALOG = os.environ.get("WIKIBRICKS_CATALOG", "main")
SCHEMA = "wiki_2wiki"
WAREHOUSE_ID = os.environ.get("WIKIBRICKS_WAREHOUSE_ID") or sys.exit(
    "WIKIBRICKS_WAREHOUSE_ID env var required"
)
IN_PATH = Path("data/twowiki/metrics.json")
OUT_PATH = Path("twowiki_results.html")


def corpus_size() -> dict:
    w = WorkspaceClient()
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"""
            SELECT
              (SELECT count(*) FROM {CATALOG}.{SCHEMA}.pages) AS pages,
              (SELECT count(*) FROM {CATALOG}.{SCHEMA}.links) AS links,
              (SELECT count(DISTINCT link_type) FROM {CATALOG}.{SCHEMA}.links) AS types
        """,
        wait_timeout="30s",
    )
    row = r.result.data_array[0]
    return {"pages": int(row[0]), "links": int(row[1]), "link_types": int(row[2])}


def fmt(v) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else str(v)


def render(data: dict, corpus: dict) -> str:
    modes = data["modes"]
    ts = data.get("timestamp", "")
    cfg = data.get("config", {})

    mode_rows = []
    for name, m in modes.items():
        if "error" in m:
            mode_rows.append(f"<tr><td><b>{name}</b></td><td colspan='8' style='color:#cf222e'>{m['error']}</td></tr>")
            continue
        mode_rows.append(f"""<tr>
          <td><b>{name}</b></td>
          <td>{fmt(m.get('em', 0))}</td>
          <td>{fmt(m.get('f1', 0))}</td>
          <td>{fmt(m.get('sp_em', 0))}</td>
          <td>{fmt(m.get('sp_f1', 0))}</td>
          <td>{fmt(m.get('evi_em', 0))}</td>
          <td>{fmt(m.get('evi_f1', 0))}</td>
          <td><b>{fmt(m.get('joint_em', 0))}</b></td>
          <td><b>{fmt(m.get('joint_f1', 0))}</b></td>
        </tr>""")
    mode_rows_html = "\n".join(mode_rows)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>WikiBricks - 2WikiMultiHopQA Benchmark</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 980px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ border-bottom: 2px solid #FF3621; padding-bottom: 0.3rem; }}
  h2 {{ margin-top: 2rem; color: #1B3139; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ padding: 0.5rem 0.6rem; border-bottom: 1px solid #ddd; text-align: left; }}
  th {{ background: #f5f5f5; font-size: 0.88rem; }}
  .num {{ font-variant-numeric: tabular-nums; }}
  .meta {{ color: #666; font-size: 0.9rem; }}
  code {{ background: #f6f8fa; padding: 0.1rem 0.3rem; border-radius: 3px; }}
</style></head><body>

<h1>WikiBricks - 2WikiMultiHopQA Benchmark</h1>
<p class="meta">Run at {ts} · dev corpus: {corpus.get('pages', '?'):,} pages,
   {corpus.get('links', '?'):,} links across {corpus.get('link_types', '?'):,} relation types
   · retrieve K={cfg.get('retrieve_k', '?')}, context K={cfg.get('context_k', '?')}
   · model <code>{cfg.get('model', '?')}</code></p>

<p>2WikiMultiHopQA<sup>[<a href="https://aclanthology.org/2020.coling-main.580/">Ho et&nbsp;al. 2020</a>]</sup>
is a Wikidata-grounded multi-hop QA dataset whose questions require reasoning over two
Wikipedia paragraphs and a small set of Wikidata relation triples. Metrics below are
produced by the <b>official v1.1 evaluator</b> against the full 12,576-question dev set.
All three per-task metrics (Answer, Supporting&nbsp;Facts, Evidence) and their joint
version are reported.</p>

<h2>Official metrics by retrieval mode</h2>
<table class="num">
  <thead><tr>
    <th>Mode</th>
    <th>Ans EM</th><th>Ans F1</th>
    <th>Sup EM</th><th>Sup F1</th>
    <th>Evi EM</th><th>Evi F1</th>
    <th>Joint EM</th><th>Joint F1</th>
  </tr></thead>
  <tbody>
{mode_rows_html}
  </tbody>
</table>

<p class="meta">Values are percentages as printed by the official eval script
(<code>2wikimultihop_evaluate_v1.1.py</code>). Joint = Answer × Supporting × Evidence.</p>

<h2>Setup</h2>
<ul>
  <li>Corpus: {corpus.get('pages', '?'):,} unique Wikipedia paragraphs (union across dev contexts), one Delta table + one DELTA_SYNC VS index.</li>
  <li>Embedding: <code>databricks-bge-large-en</code> on <code>content_text</code> (title + body).</li>
  <li>Link graph: {corpus.get('links', '?'):,} typed edges across {corpus.get('link_types', '?'):,} Wikidata relations (<code>director</code>, <code>mother</code>, <code>spouse</code>, …), derived from <code>evidences_id</code> triples, resolved via <code>id_aliases.json</code>.</li>
  <li>Answer generation: Claude reads top-K retrieved paragraphs, emits strict JSON <code>{{"answer", "sp", "evidence"}}</code> per the official prediction schema.</li>
</ul>

<p class="meta">Generated {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} by
<code>scripts/twowiki_06_render.py</code>. Full reproduction:
<a href="examples/twowiki.md">examples/twowiki.md</a>. Analysis:
<a href="docs/twowiki_evaluation.md">docs/twowiki_evaluation.md</a>.</p>

</body></html>
"""


def main() -> None:
    with open(IN_PATH) as f:
        data = json.load(f)
    corpus = corpus_size()
    html = render(data, corpus)
    with open(OUT_PATH, "w") as f:
        f.write(html)
    print(f"wrote {OUT_PATH} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
