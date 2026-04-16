# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Run AutoEval
# MAGIC
# MAGIC Measures search quality of the wiki pages_index using Vector Search AutoEval.
# MAGIC Generates synthetic queries, runs them against the index, and scores results
# MAGIC using LLM-based relevance judgements. Output: recall, precision, NDCG, MRR.

# COMMAND ----------

import sys

sys.path.insert(0, "/Workspace/Shared/wikibricks/src")

from databricks.sdk import WorkspaceClient

from wiki_ops import VS_INDEX, autoeval_config

w = WorkspaceClient()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load AutoEval Configuration

# COMMAND ----------

config = autoeval_config()
print(f"Index: {config['index_name']}")
print(f"Synthetic queries: {config['num_queries']}")
print(f"Metrics: {list(config['metrics'].keys())}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run AutoEval
# MAGIC
# MAGIC Uses `vector_search_indexes.evaluate_index` to run AutoEval on the wiki index.
# MAGIC This generates synthetic queries from the indexed content, executes them,
# MAGIC and scores results using an LLM judge.

# COMMAND ----------

# Run AutoEval on the wiki pages_index
eval_result = w.vector_search_indexes.evaluate_index(
    index_name=VS_INDEX,
    num_queries=config["num_queries"],
)
print(f"AutoEval completed. Run ID: {eval_result}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Display Results

# COMMAND ----------

# Parse and display metrics
print("=" * 60)
print("WikiBricks AutoEval Results")
print("=" * 60)
print(f"Index: {VS_INDEX}")
print()

# The eval_result contains metrics like recall@k, ndcg@k, precision@k, mrr@k
# Display them in a formatted table
if hasattr(eval_result, "metrics"):
    for metric_name, metric_value in eval_result.metrics.items():
        print(f"  {metric_name}: {metric_value:.4f}")

print()
print("Target: recall@5 > 0.8, NDCG@5 > 0.8")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Health Check
# MAGIC
# MAGIC Compare against baseline thresholds. A drop in NDCG after content updates
# MAGIC signals overlapping or contradictory wiki pages.

# COMMAND ----------

THRESHOLDS = {
    "recall@5": 0.8,
    "ndcg@5": 0.8,
}

if hasattr(eval_result, "metrics"):
    for metric, threshold in THRESHOLDS.items():
        value = eval_result.metrics.get(metric, 0)
        status = "PASS" if value >= threshold else "WARN"
        print(f"  [{status}] {metric}: {value:.4f} (threshold: {threshold})")

# COMMAND ----------

print("AutoEval complete.")
