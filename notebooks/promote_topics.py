# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Promote Topics (cross-session synthesis)
# MAGIC
# MAGIC Cluster session pages by Leiden community (computed nightly by the
# MAGIC `graph_analytics` task), then for each eligible community synthesise one
# MAGIC curated page at `topics/<slug>`. Score with a judge; write only if the
# MAGIC synthesis clears the threshold.
# MAGIC
# MAGIC ## Activation guard
# MAGIC
# MAGIC The corpus must contain at least `min_corpus_size` session pages before
# MAGIC synthesis fires. Below the threshold, clustering produces too many
# MAGIC tiny "topics" and the LLM call wastes tokens. Default 80.
# MAGIC
# MAGIC ## Idempotency
# MAGIC
# MAGIC The topic page path is `topics/<slug>` where slug is derived
# MAGIC deterministically from member-page titles. Re-running over the same
# MAGIC community updates the existing page via `WikiClient.write_page`'s MERGE.

# COMMAND ----------

# MAGIC %md
# MAGIC The `wikibricks` wheel is installed via the task-level serverless
# MAGIC environment in `resources/wiki_curate_job.yml`. No in-notebook
# MAGIC `%pip install` here — the bundle artifact path is substituted at
# MAGIC deploy time.

# COMMAND ----------

import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

from wikibricks import WikiClient
from wikibricks.topic_clustering import (
    cluster_pages_by_community,
    topic_slug_from_titles,
)


def _param(name: str, default: str) -> str:
    try:
        return dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        return default


catalog            = _param("catalog", os.environ.get("WIKIBRICKS_CATALOG", "main"))
schema             = _param("schema", os.environ.get("WIKIBRICKS_SCHEMA", "wikibricks"))
warehouse_id       = _param("warehouse_id", os.environ.get("WIKIBRICKS_WAREHOUSE_ID", ""))
synth_endpoint     = _param("synth_endpoint", "databricks-claude-sonnet-4-5")
judge_endpoint     = _param("judge_endpoint", "databricks-claude-haiku-4-5")
min_corpus_size    = int(_param("min_corpus_size", "80"))
min_cluster_size   = int(_param("min_cluster_size", "3"))
max_topics_per_run = int(_param("max_topics_per_run", "20"))
judge_threshold    = float(_param("judge_threshold", "4.0"))
max_pages_in_synth = int(_param("max_pages_in_synth", "8"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — Load session pages and check the corpus-size guard

# COMMAND ----------

ws = WorkspaceClient()
wiki = WikiClient(warehouse_id=warehouse_id, workspace_client=ws)

# Pull pages with community_id directly (avoids a second round trip).
resp = wiki._exec(
    f"SELECT page_id, path, title, community_id, hub_score "
    f"FROM {catalog}.{schema}.pages "
    f"WHERE community_id IS NOT NULL "
    f"  AND path LIKE 'sessions/%' "
    f"  AND NOT array_contains(COALESCE(tags, array()), 'ephemeral:stub')"
)
rows = resp.result.data_array or []
cols = [c.name for c in resp.manifest.columns]
pages = [
    {"page_id": r[0], "path": r[1], "title": r[2],
     "community_id": int(r[3]) if r[3] is not None else None,
     "hub_score": float(r[4]) if r[4] is not None else 0.0}
    for r in rows
]
print(f"Scored session pages in corpus: {len(pages)}")

if len(pages) < min_corpus_size:
    print(f"Corpus below activation threshold ({min_corpus_size}). Skipping.")
    dbutils.notebook.exit("skipped:corpus-too-small")  # noqa: F821

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — Cluster by Leiden community

# COMMAND ----------

clusters = cluster_pages_by_community(pages, min_cluster_size=min_cluster_size)
print(f"Found {len(clusters)} eligible communities (≥ {min_cluster_size} pages):\n")
for cid, members in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
    top_titles = " · ".join(p["title"][:40] for p in members[:3])
    print(f"  community {cid}: {len(members)} pages    [{top_titles}]")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — Synthesise + judge + write
# MAGIC
# MAGIC For each eligible community, pull the top-K hub_score pages, send their
# MAGIC content to the synth model, judge the result, write if it clears the
# MAGIC threshold. Bounded by `max_topics_per_run`.

# COMMAND ----------

_SYNTH_SYSTEM = (
    "You are a senior technical editor. Read the supplied wiki-session "
    "excerpts and write ONE concise canonical reference page (markdown, "
    "≤500 words). Lead with a one-sentence definition. Cover the most "
    "important facts, decisions, and references mentioned across multiple "
    "excerpts (signal: a fact mentioned in 2+ excerpts matters more). "
    "Skip per-session noise (timestamps, tool calls, error traces). End "
    "with a `## Sources` list of the wiki paths that contributed. Do not "
    "invent facts not in the excerpts."
)

_JUDGE_SYSTEM = (
    "You are evaluating a synthesised wiki topic page against the source "
    "excerpts it was built from. Score 1-5 (integer) where 5 = excellent "
    "(accurate, complete, well-organised, no fabrications), 4 = good "
    "(minor issues), 3 = mixed (some useful content but problems), 2 = "
    "weak (mostly noise), 1 = unusable. Output ONLY the integer score "
    "on its own line, nothing else."
)


def _synth_topic(cluster_pages: list[dict]) -> tuple[str, list[str]]:
    """Return (markdown_body, [source_paths]) for a cluster."""
    # Pull body excerpts; the WikiClient.read_page returns content_text already.
    sources: list[str] = []
    excerpts: list[str] = []
    for page in cluster_pages[:max_pages_in_synth]:
        try:
            row = wiki.read_page(page["path"])
            body = (row.get("content_text") if row else "") or ""
            if not body.strip():
                continue
            sources.append(page["path"])
            excerpts.append(f"### {page['title']}\n\n{body[:4000]}")
        except Exception:
            continue
    if not excerpts:
        return "", []
    user_prompt = "Source excerpts:\n\n" + "\n\n---\n\n".join(excerpts)
    resp = ws.serving_endpoints.query(
        name=synth_endpoint,
        messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM, content=_SYNTH_SYSTEM),
            ChatMessage(role=ChatMessageRole.USER, content=user_prompt),
        ],
        max_tokens=1500,
    )
    return resp.choices[0].message.content, sources


def _judge_topic(body: str, source_excerpts: str) -> float:
    """Return a 1–5 judge score, NaN on parse failure."""
    resp = ws.serving_endpoints.query(
        name=judge_endpoint,
        messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM, content=_JUDGE_SYSTEM),
            ChatMessage(role=ChatMessageRole.USER, content=(
                "Source excerpts (first 4000 chars):\n\n" + source_excerpts[:4000]
                + "\n\n---\n\nSynthesised topic page:\n\n" + body[:4000]
            )),
        ],
        max_tokens=10,
    )
    raw = resp.choices[0].message.content.strip()
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return float("nan")


# Iterate clusters in descending size order so the biggest communities
# get the synthesis budget first.
sorted_clusters = sorted(clusters.items(), key=lambda kv: -len(kv[1]))[:max_topics_per_run]

written: list[str] = []
rejected: list[tuple[str, str]] = []
for cid, members in sorted_clusters:
    slug = topic_slug_from_titles([p["title"] for p in members], community_id=cid)
    print(f"\n→ community {cid} (n={len(members)}) → topics/{slug}")
    body, sources = _synth_topic(members)
    if not body.strip():
        rejected.append((slug, "empty-synth"))
        wiki._log("promote_topic_reject", path=f"topics/{slug}",
                  details=f'{{"reason":"empty-synth","community_id":{cid}}}')
        continue
    excerpt_blob = "\n\n".join(p.get("title", "") for p in members)
    score = _judge_topic(body, excerpt_blob)
    print(f"   judge score: {score}")
    if score != score or score < judge_threshold:  # NaN check + threshold
        rejected.append((slug, f"score={score}"))
        wiki._log("promote_topic_reject", path=f"topics/{slug}",
                  details=f'{{"score":{score!r},"community_id":{cid}}}')
        continue
    title = f"Topic: {slug.replace('-', ' ').title()}"
    wiki.write_page(
        path=f"topics/{slug}",
        title=title,
        content_json={"summary": body[:200], "body": body},
        page_type="concept",
        tags=["topic", "synthesised", f"community:{cid}"],
    )
    wiki._log("promote_topic", path=f"topics/{slug}",
              details=f'{{"score":{score!r},"community_id":{cid},"n_pages":{len(members)}}}')
    written.append(slug)

# COMMAND ----------

print(f"\n=== Summary ===")
print(f"Wrote {len(written)} topic pages:")
for s in written:
    print(f"  topics/{s}")
print(f"Rejected {len(rejected)}:")
for slug, reason in rejected:
    print(f"  topics/{slug}    [{reason}]")
