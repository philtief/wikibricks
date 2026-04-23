# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Batch Promotion Pipeline
# MAGIC
# MAGIC Daily job. Mines agent session traces for recurring questions, clusters them,
# MAGIC has an LLM synthesise a canonical answer per cluster, judges it, and promotes
# MAGIC clusters that score ≥ JUDGE_THRESHOLD (default 4.0 on the 1-5 integer
# MAGIC judge scale) via `WikiClient.promote_answer`.

# COMMAND ----------

# MAGIC %pip install /Volumes/<catalog>/<schema>/wheels/wikibricks-0.1.4-py3-none-any.whl
# MAGIC # ^ Update path to where the wheel lives in your workspace. `databricks
# MAGIC #   bundle deploy` builds the wheel locally and syncs it to the bundle
# MAGIC #   workspace path; for interactive runs, upload dist/wikibricks-*.whl
# MAGIC #   to a Volume and edit the path above.
# MAGIC %restart_python

# COMMAND ----------

from datetime import timezone

from databricks.sdk import WorkspaceClient

from wikibricks import PROMOTE_CHECKPOINT_TABLE, WikiClient
from wikibricks.promote_logic import (
    cluster_by_cosine,
    filter_eligible_clusters,
    get_promote_window,
    is_duplicate_hit,
    judge_response_is_numeric,
    now_utc,
    parse_judge_score,
)


def _param(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        val = default
    return val or default


WAREHOUSE_ID = _param("warehouse_id", "")
TRACES_TABLE = _param("traces_table", "<catalog>.<schema>.agent_traces")
JUDGE_ENDPOINT = _param("judge_endpoint", "databricks-claude-sonnet-4-5")
EMBED_ENDPOINT = _param("embed_endpoint", "databricks-bge-large-en")
MIN_CLUSTER_MEMBERS = int(_param("min_cluster_members", "5"))
MIN_DISTINCT_SESSIONS = int(_param("min_distinct_sessions", "3"))
JUDGE_THRESHOLD = float(_param("judge_threshold", "4.0"))
MAX_CLUSTERS_PER_RUN = int(_param("max_clusters_per_run", "50"))
CLUSTER_THRESHOLD = float(_param("cluster_threshold", "0.80"))

wiki = WikiClient(warehouse_id=WAREHOUSE_ID)
ws = WorkspaceClient()

# COMMAND ----------

# MAGIC %md ## Silver: per-session aggregated traces

# COMMAND ----------

# Read the checkpoint so we only process traces we haven't seen before.
try:
    cp_rows = spark.sql(  # noqa: F821
        f"SELECT last_watermark_ts FROM {PROMOTE_CHECKPOINT_TABLE} "
        f"WHERE checkpoint_id = 'promote'"
    ).collect()
    last_watermark = cp_rows[0]["last_watermark_ts"].replace(tzinfo=timezone.utc) if cp_rows else None
except Exception as e:
    print(f"checkpoint: first run or checkpoint table missing ({type(e).__name__}: {e})")
    last_watermark = None

window_start, window_end = get_promote_window(last_watermark, now_utc())
print(f"window: ({window_start.isoformat()}, {window_end.isoformat()}]")

try:
    silver = spark.sql(  # noqa: F821
        f"""
        SELECT session_id,
               first(user_query) AS query,
               first(model_response) AS answer,
               array_distinct(flatten(collect_set(retrieved_paths))) AS sources
        FROM {TRACES_TABLE}
        WHERE timestamp > '{window_start.isoformat()}'
          AND timestamp <= '{window_end.isoformat()}'
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
        name=EMBED_ENDPOINT,
        input=[text],
    )
    return resp.data[0].embedding


rows = [dict(r.asDict()) for r in silver]
for r in rows:
    r["embedding"] = embed(r["query"])

clusters = cluster_by_cosine(rows, CLUSTER_THRESHOLD)
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
    raw_judge = judge_resp.choices[0].message.content
    score = parse_judge_score(raw_judge)

    # Discriminate 'judge returned gibberish' from 'legitimate low score'
    # so an operator querying wiki_log can spot prompt drift early.
    if not judge_response_is_numeric(raw_judge):
        wiki._log("promote_parse_fail", query=query,  # noqa: SLF001
                  details=f"cluster_size={len(cluster)} raw={(raw_judge or '')[:80]!r}")
        rejected_count += 1
        continue

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

# Trigger the VS index so just-promoted pages are immediately searchable.
# The DELTA_SYNC index runs in TRIGGERED mode; without this, new pages stay
# invisible to `WikiClient.search` until the next external sync and the
# `is_duplicate_hit` dedup check on the next run can miss them.
if promoted_count > 0:
    wiki.sync_index()
    print("triggered VS index sync")

# COMMAND ----------

# MAGIC %md ## Advance the watermark
# MAGIC Only runs if the whole promote phase completed; failure earlier exits the
# MAGIC notebook and leaves the checkpoint untouched so the next run retries.

# COMMAND ----------

spark.sql(  # noqa: F821
    f"""
    MERGE INTO {PROMOTE_CHECKPOINT_TABLE} t
    USING (SELECT 'promote' AS checkpoint_id,
                  TIMESTAMP '{window_end.isoformat()}' AS last_watermark_ts,
                  current_timestamp() AS updated_at) s
    ON t.checkpoint_id = s.checkpoint_id
    WHEN MATCHED THEN UPDATE SET
        last_watermark_ts = s.last_watermark_ts,
        updated_at        = s.updated_at
    WHEN NOT MATCHED THEN INSERT *
    """
)
print(f"checkpoint advanced to {window_end.isoformat()}")
