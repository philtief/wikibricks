# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: HotpotQA Benchmark
# MAGIC
# MAGIC Runs the 7,405 HotpotQA dev questions against a deployed WikiBricks wiki,
# MAGIC computes recall@2, recall@10, MRR, and supporting-fact F1 per search mode.

# COMMAND ----------

# MAGIC %pip install /Volumes/agent_marketplace_catalog/ai_agent/raw_data/wikibricks-0.1.3-py3-none-any.whl
# MAGIC %restart_python

# COMMAND ----------

import json
from pathlib import Path

from wikibricks import WikiClient
from wikibricks.ops import (
    eval_mrr_multi,
    eval_recall_at_k_multi,
    eval_supporting_fact_f1,
)


def _param(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        val = default
    return val or default


WAREHOUSE_ID = _param("warehouse_id", "41754a8563a43a49")
QUERIES_PATH = _param("queries_path", "/Workspace/Shared/wikibricks/hotpot/queries.jsonl")
MODES = ["HYBRID", "ANN", "FULL_TEXT"]
K_VALUES = [2, 10]

wiki = WikiClient(warehouse_id=WAREHOUSE_ID)

# COMMAND ----------

with open(QUERIES_PATH) as f:
    queries = [json.loads(line) for line in f if line.strip()]
print(f"Loaded {len(queries):,} HotpotQA dev queries")

# COMMAND ----------

# MAGIC %md ## Run each mode

# COMMAND ----------

results_by_mode: dict[str, dict] = {}

for mode in MODES:
    print(f"\n=== Mode: {mode} ===")
    recall_at = {k: 0.0 for k in K_VALUES}
    mrr_sum = 0.0
    f1_sum = 0.0

    for i, q in enumerate(queries):
        hits = wiki.search(q["question"], mode=mode, num_results=max(K_VALUES))
        retrieved = [h.get("path") for h in hits]
        relevant = q["relevant_paths"]

        for k in K_VALUES:
            recall_at[k] += eval_recall_at_k_multi(retrieved, relevant, k)
        mrr_sum += eval_mrr_multi(retrieved, relevant)
        f1_sum += eval_supporting_fact_f1(retrieved[:max(K_VALUES)], relevant)

        if (i + 1) % 500 == 0:
            print(f"  {i + 1:,} queries processed")

    n = len(queries)
    results_by_mode[mode] = {
        **{f"recall@{k}": recall_at[k] / n for k in K_VALUES},
        "mrr": mrr_sum / n,
        "supporting_fact_f1": f1_sum / n,
    }
    print(results_by_mode[mode])

# COMMAND ----------

# MAGIC %md ## Link-graph ablation (HYBRID only)
# MAGIC
# MAGIC Follow `links.link_type='supports'` from the first retrieved page to measure
# MAGIC recall uplift from the cross-reference structure - the core WikiBricks story.

# COMMAND ----------

ablation = {k: 0.0 for k in K_VALUES}
for q in queries:
    hits = wiki.search(q["question"], mode="HYBRID", num_results=max(K_VALUES))
    retrieved = [h.get("path") for h in hits]

    if retrieved:
        page = wiki.read_page(retrieved[0])
        linked = page.get("links", []) if page else []
        for link in linked:
            if link.get("link_type") == "supports" and link.get("target_path") not in retrieved:
                retrieved.append(link["target_path"])

    for k in K_VALUES:
        ablation[k] += eval_recall_at_k_multi(retrieved, q["relevant_paths"], k)

n = len(queries)
ablation_summary = {f"recall@{k}_with_links": ablation[k] / n for k in K_VALUES}
print("Ablation:", ablation_summary)

# COMMAND ----------

# MAGIC %md ## Persist results

# COMMAND ----------

output = {
    "n_queries": len(queries),
    "modes": results_by_mode,
    "link_graph_ablation": ablation_summary,
}

out_path = Path("/Workspace/Shared/wikibricks/hotpot/benchmark_results.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(output, indent=2))
print(f"Wrote {out_path}")

print(json.dumps(output, indent=2))
