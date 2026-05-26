# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Search Quality Baseline
# MAGIC
# MAGIC Measures retrieval quality of the `pages_index` Vector Search index using a
# MAGIC hand-labeled evaluation set of natural-language queries → expected page paths.
# MAGIC Reports recall@k, precision@k, and MRR for each search mode (HYBRID, ANN, FULL_TEXT).

# COMMAND ----------

# MAGIC %pip install /Volumes/<catalog>/<schema>/wheels/wikibricks-0.7.13-py3-none-any.whl
# MAGIC # ^ Update path to where the wheel lives in your workspace.
# MAGIC %restart_python

# COMMAND ----------

from databricks.sdk import WorkspaceClient

from wikibricks.ops import (
    VS_INDEX,
    eval_mrr,
    eval_precision_at_k,
    eval_queries,
    eval_recall_at_k,
)

w = WorkspaceClient()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run queries and collect retrieved paths

# COMMAND ----------


def run_eval(mode):
    """Run every labeled query in a given search mode, return per-query metrics."""
    results = []
    for case in eval_queries():
        kwargs = {
            "index_name": VS_INDEX,
            "columns": ["page_id", "path", "title"],
            "query_text": case["query"],
            "num_results": 10,
        }
        if mode != "ANN":
            kwargs["query_type"] = mode
        resp = w.vector_search_indexes.query_index(**kwargs)
        rows = resp.result.data_array or []
        path_idx = next(
            i for i, c in enumerate(resp.manifest.columns) if c.name == "path"
        )
        retrieved = [row[path_idx] for row in rows]
        results.append({
            "query": case["query"],
            "relevant": case["relevant_paths"],
            "retrieved": retrieved,
        })
    return results


# COMMAND ----------

# MAGIC %md
# MAGIC ## Aggregate metrics

# COMMAND ----------


def aggregate(results):
    n = len(results)
    return {
        "recall@3": sum(eval_recall_at_k(r["retrieved"], r["relevant"], 3) for r in results) / n,
        "recall@5": sum(eval_recall_at_k(r["retrieved"], r["relevant"], 5) for r in results) / n,
        "recall@10": sum(eval_recall_at_k(r["retrieved"], r["relevant"], 10) for r in results) / n,
        "precision@3": sum(eval_precision_at_k(r["retrieved"], r["relevant"], 3) for r in results) / n,
        "precision@5": sum(eval_precision_at_k(r["retrieved"], r["relevant"], 5) for r in results) / n,
        "mrr": sum(eval_mrr(r["retrieved"], r["relevant"]) for r in results) / n,
    }


# COMMAND ----------

# MAGIC %md
# MAGIC ## Report

# COMMAND ----------

print("=" * 72)
print(f"WikiBricks retrieval baseline - index: {VS_INDEX}")
print(f"Eval set: {len(eval_queries())} labeled queries")
print("=" * 72)

all_metrics = {}
for mode in ("HYBRID", "ANN", "FULL_TEXT"):
    results = run_eval(mode)
    metrics = aggregate(results)
    all_metrics[mode] = metrics
    print(f"\n[{mode}]")
    for name, value in metrics.items():
        print(f"  {name:14s} {value:.4f}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Health check
# MAGIC
# MAGIC Targets: recall@5 ≥ 0.8 and MRR ≥ 0.7 on HYBRID.

# COMMAND ----------

THRESHOLDS = {"recall@5": 0.8, "mrr": 0.7}
hybrid = all_metrics["HYBRID"]
for metric, threshold in THRESHOLDS.items():
    value = hybrid.get(metric, 0.0)
    status = "PASS" if value >= threshold else "WARN"
    print(f"  [{status}] HYBRID {metric}: {value:.4f} (threshold: {threshold})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Per-query diagnosis (HYBRID)

# COMMAND ----------

hybrid_results = run_eval("HYBRID")
for r in hybrid_results:
    hit = r["relevant"][0] in r["retrieved"][:5]
    rank = (r["retrieved"].index(r["relevant"][0]) + 1
            if r["relevant"][0] in r["retrieved"] else "miss")
    print(f"  [{'HIT' if hit else 'miss'}] rank={rank}  q={r['query'][:60]}")

# COMMAND ----------

print("Baseline complete.")
