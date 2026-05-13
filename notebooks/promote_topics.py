# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Promote Topics (cross-session synthesis)
# MAGIC
# MAGIC Cluster session pages by topic keyword, then for each cluster with enough
# MAGIC density synthesise one curated page at `topics/<slug>`. The agent (not the
# MAGIC library) does the LLM synthesis — this notebook orchestrates.
# MAGIC
# MAGIC ## Activation guard
# MAGIC
# MAGIC The corpus must contain at least `min_corpus_size` session pages before
# MAGIC synthesis fires. Below the threshold, clustering produces too many
# MAGIC single-page "topics" and the LLM call wastes tokens. Default 80.
# MAGIC
# MAGIC ## Status
# MAGIC
# MAGIC **Scaffolding** — clustering + corpus guard + dry-run report ship today.
# MAGIC The LLM synthesis + judge + write step is left as a clearly-marked TODO so
# MAGIC the operator can enable it when their corpus is dense enough. No bundle
# MAGIC resource entry is added by default; this notebook is opt-in.

# COMMAND ----------

# MAGIC %pip install /Volumes/<catalog>/<schema>/wheels/wikibricks-0.4.1-py3-none-any.whl
# MAGIC %restart_python

# COMMAND ----------

import os

from databricks.sdk import WorkspaceClient

from wikibricks import WikiClient
from wikibricks.topic_clustering import UNCATEGORISED, cluster_pages_by_keyword


def _param(name: str, default: str) -> str:
    try:
        return dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        return default


catalog = _param("catalog", os.environ.get("WIKIBRICKS_CATALOG", "main"))
schema = _param("schema", os.environ.get("WIKIBRICKS_SCHEMA", "wikibricks"))
warehouse_id = _param("warehouse_id", os.environ.get("WIKIBRICKS_WAREHOUSE_ID", ""))
min_corpus_size = int(_param("min_corpus_size", "80"))
min_cluster_size = int(_param("min_cluster_size", "3"))

# Topic keyword map. Insertion order encodes priority: when a page title
# matches multiple topics, the first one wins. Keep terms specific enough
# to avoid false positives (use lowercase; matching is case-insensitive).
KEYWORDS = {
    "solvd": ["solvd", "controlexpert", "control expert"],
    "allianz-italy": ["allianz italy", "az italy", "azitaly"],
    "allianz-suisse": ["allianz suisse", "az ch ", "azch", "allianz schweiz"],
    "allianz-re": ["allianz re ", "azre", "az re", "reinsurance"],
    "agcs": ["agcs", "allianz commercial"],
    "gafs": ["gafs", "allianz france"],
    "ai-tribe": ["ai tribe", "ash ", "allianz services"],
    "agi": ["allianz global investors", "agi "],
    "simplifi": ["simplifi", "simpli-fi"],
    "h6": [" h6 ", "global hr", "allianz hr"],
    "wikibricks": ["wikibricks", "wiki brick"],
    "topgenie": ["topgenie", "top genie", "genie-conversation-app"],
}

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Load session pages and check the corpus-size guard

# COMMAND ----------

wiki = WikiClient(warehouse_id=warehouse_id, workspace_client=WorkspaceClient())
pages = wiki.list_pages(path_prefix="sessions/")
print(f"Session pages in corpus: {len(pages)}")

if len(pages) < min_corpus_size:
    print(f"Corpus below activation threshold ({min_corpus_size}). Skipping.")
    dbutils.notebook.exit("skipped:corpus-too-small")  # noqa: F821

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Cluster pages by topic

# COMMAND ----------

buckets = cluster_pages_by_keyword(pages, KEYWORDS)
print(f"Found {len(buckets)} non-empty topics:\n")
for slug, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
    marker = " (uncategorised)" if slug == UNCATEGORISED else ""
    print(f"  {slug}: {len(items)} pages{marker}")

eligible = {
    slug: items
    for slug, items in buckets.items()
    if slug != UNCATEGORISED and len(items) >= min_cluster_size
}
print(f"\n{len(eligible)} topics meet min_cluster_size={min_cluster_size}.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Synthesise + judge + write (TODO, opt-in)
# MAGIC
# MAGIC For each eligible topic, the operator should:
# MAGIC
# MAGIC 1. Fetch top-10 page bodies via `wiki.read_page` per item.
# MAGIC 2. LLM-synthesise a curated topic page (provider of choice; the recorder
# MAGIC    repo does not pin one). Keep the prompt deterministic so re-runs
# MAGIC    produce comparable outputs.
# MAGIC 3. Score the synthesis with the same judge used by
# MAGIC    `promote_from_traces.py` (`mlflow.evaluate` with a 1–5 rubric).
# MAGIC 4. If score >= 4, call `wiki.write_page(path=f"topics/{slug}", title=...,
# MAGIC    content_json=..., tags=["topic", "synthesised"])`. Log
# MAGIC    `op_type='promote_topic'` to `wiki_log`. Otherwise log
# MAGIC    `op_type='promote_topic_reject'` with the score and rationale.
# MAGIC
# MAGIC Leave this step disabled until the corpus produces stable clusters
# MAGIC across two consecutive weeks. Validate the synthesis output manually
# MAGIC on 2–3 topics before letting the job run unattended.

# COMMAND ----------

print("DRY-RUN: no topic pages written. Implement Step 3 when corpus is ready.")
