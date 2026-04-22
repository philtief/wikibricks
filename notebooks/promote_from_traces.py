# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Batch Promotion Pipeline
# MAGIC
# MAGIC Daily job. Mines agent session traces for recurring questions, clusters them,
# MAGIC has an LLM synthesise a canonical answer per cluster, judges it, and promotes
# MAGIC clusters that score ≥ 4.5 via `WikiClient.promote_answer`.

# COMMAND ----------

# MAGIC %pip install /Volumes/agent_marketplace_catalog/ai_agent/raw_data/wikibricks-0.1.3-py3-none-any.whl
# MAGIC %restart_python

# COMMAND ----------

from databricks.sdk import WorkspaceClient

from wikibricks import WikiClient
from wikibricks.promote_logic import (
    cluster_by_cosine,
    filter_eligible_clusters,
    is_duplicate_hit,
    parse_judge_score,
)


def _param(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        val = default
    return val or default


WAREHOUSE_ID = _param("warehouse_id", "41754a8563a43a49")
TRACES_TABLE = _param("traces_table", "agent_marketplace_catalog.default.agent_traces")
JUDGE_ENDPOINT = _param("judge_endpoint", "databricks-claude-sonnet-4-5")
MIN_CLUSTER_MEMBERS = int(_param("min_cluster_members", "5"))
MIN_DISTINCT_SESSIONS = int(_param("min_distinct_sessions", "3"))
JUDGE_THRESHOLD = float(_param("judge_threshold", "4.5"))
MAX_CLUSTERS_PER_RUN = int(_param("max_clusters_per_run", "50"))

wiki = WikiClient(warehouse_id=WAREHOUSE_ID)
ws = WorkspaceClient()

# COMMAND ----------

# MAGIC %md ## Silver: per-session aggregated traces

# COMMAND ----------

try:
    silver = spark.sql(  # noqa: F821
        f"""
        SELECT session_id,
               first(user_query) AS query,
               first(model_response) AS answer,
               collect_set(retrieved_paths) AS sources
        FROM {TRACES_TABLE}
        WHERE DATE(timestamp) = current_date() - INTERVAL 1 DAY
        GROUP BY session_id
        """
    ).collect()
except Exception as e:
    # Promote is opt-in: if the workspace hasn't created a traces table yet, the
    # job should stay green rather than fail the whole curate pipeline.
    print(f"silver: no traces table ({type(e).__name__}: {e}); skipping promote")
    silver = []

print(f"silver rows: {len(silver)}")

if not silver:
    dbutils.notebook.exit("no traces to promote")  # noqa: F821

# COMMAND ----------

# MAGIC %md ## Gold: cluster by representative query
# MAGIC
# MAGIC Threshold-agglomerative via embedding cosine (simpler than HDBSCAN for <10 k rows).

# COMMAND ----------

from databricks.sdk.service.serving import ChatMessage, ChatMessageRole  # noqa: E402


def embed(text: str) -> list[float]:
    resp = ws.serving_endpoints.query(
        name="databricks-bge-large-en",
        input=[text],
    )
    return resp.data[0]["embedding"]


rows = [dict(r.asDict()) for r in silver]
for r in rows:
    r["embedding"] = embed(r["query"])

THRESHOLD = 0.88
clusters = cluster_by_cosine(rows, THRESHOLD)
eligible = filter_eligible_clusters(
    clusters,
    min_members=MIN_CLUSTER_MEMBERS,
    min_distinct_sessions=MIN_DISTINCT_SESSIONS,
    max_clusters=MAX_CLUSTERS_PER_RUN,
)
print(f"eligible clusters: {len(eligible)}")

# COMMAND ----------

# MAGIC %md ## Synthesis + judge + promote

# COMMAND ----------

promoted_count = 0
rejected_count = 0

for cluster in eligible:
    representative = cluster[0]
    query = representative["query"]

    # Synthesise a canonical answer.
    answers_text = "\n---\n".join(m["answer"] for m in cluster[:5])
    synth_resp = ws.serving_endpoints.query(
        name=JUDGE_ENDPOINT,
        messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM,
                        content="Synthesise a single canonical answer from the following "
                                "agent responses to equivalent questions. Remove redundancy; "
                                "keep citations and technical specifics."),
            ChatMessage(role=ChatMessageRole.USER, content=f"Q: {query}\n\n{answers_text}"),
        ],
    )
    canonical = synth_resp.choices[0].message.content

    # Judge.
    judge_resp = ws.serving_endpoints.query(
        name=JUDGE_ENDPOINT,
        messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM,
                        content="Rate this answer on a scale of 1-5 for quality, accuracy, "
                                "and usefulness. Reply with ONLY a single digit (1-5)."),
            ChatMessage(role=ChatMessageRole.USER, content=f"Q: {query}\n\nA: {canonical}"),
        ],
    )
    score = parse_judge_score(judge_resp.choices[0].message.content)

    if score < JUDGE_THRESHOLD:
        wiki._log("promote_reject", query=query,  # noqa: SLF001
                  details=f"cluster_size={len(cluster)} score={score}")
        rejected_count += 1
        continue

    # Dedup: search existing promoted pages.
    existing = wiki.search(query, mode="HYBRID", num_results=1)
    if is_duplicate_hit(existing[0] if existing else None):
        # MERGE refreshes the page via the same path.
        wiki.write_page(
            path=existing[0]["path"],
            title=query[:120],
            content_json={"summary": query, "body": canonical},
            page_type="synthesis",
            created_by="batch-promote",
            tags=["promoted", "batch"],
        )
    else:
        source_pages = []
        for member in cluster:
            for path in member.get("sources") or []:
                page = wiki.read_page(path)
                if page:
                    source_pages.append(page)
        wiki.promote_answer(query, canonical, source_pages, created_by="batch-promote")

    promoted_count += 1

print(f"promoted: {promoted_count}, rejected: {rejected_count}")
