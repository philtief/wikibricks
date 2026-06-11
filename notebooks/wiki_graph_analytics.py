# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Graph Analytics (PageRank + community detection)
# MAGIC
# MAGIC Reads pages + currently-valid links, builds an `igraph` graph, computes
# MAGIC PageRank (directional authority signal) + Leiden communities (on the
# MAGIC undirected projection), writes scores back to `pages.hub_score` and
# MAGIC `pages.community_id`.
# MAGIC
# MAGIC Hub scores feed the optional `WikiClient.search(rerank_with_pagerank=True)`
# MAGIC blend via Reciprocal Rank Fusion. Communities feed future cluster-aware
# MAGIC promote logic.
# MAGIC
# MAGIC Deterministic — pure-helper algorithms live in
# MAGIC `src/wikibricks/graph_logic.py` and are unit-tested. This notebook is a
# MAGIC thin wrapper that reads, computes, writes.

# COMMAND ----------

# The `wikibricks` wheel + `igraph>=0.11` are installed via the serverless
# environment `dependencies` in resources/wiki_curate_job.yml.

# COMMAND ----------

import os


def _read_widget(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
        return val or default
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        return default


os.environ["WIKIBRICKS_CATALOG"] = _read_widget("catalog", "main")
os.environ["WIKIBRICKS_SCHEMA"] = _read_widget("schema", "wiki")

from databricks.sdk import WorkspaceClient

from wikibricks import WikiClient
from wikibricks.graph_logic import (
    build_igraph,
    compute_communities,
    compute_pagerank,
)
from wikibricks.ops import LINKS_TABLE, PAGES_TABLE


def _param(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        val = default
    return val or default


WAREHOUSE_ID = _param("warehouse_id", "")
DAMPING = float(_param("damping", "0.85"))
COMMUNITY_MIN_NODES = int(_param("community_min_nodes", "5"))

w = WorkspaceClient()
wiki = WikiClient(warehouse_id=WAREHOUSE_ID, workspace_client=w)


def run_sql(sql: str) -> list[dict]:
    # Delegate to WikiClient._exec, which polls a cold/contended serverless
    # warehouse to a terminal state before returning. The previous inline
    # execute_statement(wait_timeout="30s") crashed with
    # `'NoneType' object has no attribute 'data_array'` when the statement was
    # still PENDING after the inline wait (result=None) — e.g. under warehouse
    # contention while the curate job's tasks run concurrently.
    resp = wiki._exec(sql)
    rows = resp.result.data_array if resp.result else []
    if not rows:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, r)) for r in rows]


# COMMAND ----------

# MAGIC %md ## Pull pages + currently-valid edges

# COMMAND ----------

# Hard filter `valid_until IS NULL` so PageRank reflects the currently-valid
# subgraph, not historical (closed) edges. Bi-temporal correctness.
pages = run_sql(f"SELECT page_id FROM {PAGES_TABLE}")
edges = run_sql(
    f"SELECT source_page_id, target_page_id "
    f"FROM {LINKS_TABLE} "
    f"WHERE valid_until IS NULL"
)
print(f"pages: {len(pages)}, currently-valid edges: {len(edges)}")

if not pages:
    dbutils.notebook.exit("no pages")  # noqa: F821

# COMMAND ----------

# MAGIC %md ## Build graph + compute scores

# COMMAND ----------

g = build_igraph(pages, edges)
print(f"graph: {g.vcount()} vertices, {g.ecount()} edges, directed={g.is_directed()}")

hub_scores = compute_pagerank(g, damping=DAMPING)
print(f"PageRank computed for {len(hub_scores)} pages")
print(f"  top 5 by hub_score: "
      f"{sorted(hub_scores.items(), key=lambda kv: -kv[1])[:5]}")

community_ids = compute_communities(g, min_nodes=COMMUNITY_MIN_NODES)
n_comms = len(set(community_ids.values())) if community_ids else 0
print(f"Leiden communities: {n_comms} (over {len(community_ids)} pages)")

# COMMAND ----------

# MAGIC %md ## Write scores back to pages

# COMMAND ----------

scores = []
for pid in hub_scores:
    scores.append({
        "page_id": pid,
        "hub_score": hub_scores.get(pid),
        "community_id": community_ids.get(pid),  # None when below min_nodes
    })

n = wiki.update_graph_scores(scores)
wiki._log(  # noqa: SLF001
    "graph_analytics",
    details=(
        f"{{\"pages_scored\": {n}, \"edges\": {g.ecount()}, "
        f"\"communities\": {n_comms}, \"damping\": {DAMPING}}}"
    ),
)
print(f"updated {n} pages with hub_score + community_id")
