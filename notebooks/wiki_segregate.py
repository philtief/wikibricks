# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Segregate Oversize Pages (LLM-driven, opt-in)
# MAGIC
# MAGIC Reads pages flagged `health_status = 'oversize'` by the curate Phase 4
# MAGIC health check and splits each one into a parent (summary + ToC) plus N
# MAGIC chunk children, joined by `parent_id` and ordered by `chunk_index`.
# MAGIC
# MAGIC Deterministic pieces (chunking, child paths/titles, ToC body) live in
# MAGIC `src/wikibricks/segregate_logic.py` and are unit-tested. The LLM is only
# MAGIC asked for two things per page: a short summary and one title per chunk.
# MAGIC The library core stays LLM-free (AGENTS.md hard rule) — this notebook is
# MAGIC the only place LLM calls land for the segregate flow.

# COMMAND ----------

# MAGIC %pip install /Volumes/<catalog>/<schema>/wheels/wikibricks-0.1.4-py3-none-any.whl
# MAGIC # ^ Update path to where the wheel lives in your workspace.
# MAGIC %restart_python

# COMMAND ----------

import json

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

from wikibricks import WikiClient
from wikibricks.ops import PAGES_TABLE
from wikibricks.segregate_logic import (
    build_parent_body,
    child_path,
    child_title,
    chunk_at_boundaries,
)


def _param(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        val = default
    return val or default


WAREHOUSE_ID = _param("warehouse_id", "")
CHAT_ENDPOINT = _param("chat_endpoint", "databricks-claude-sonnet-4-5")
MAX_CHARS_PER_CHUNK = int(_param("max_chars_per_chunk", "8000"))
MAX_PAGES_PER_RUN = int(_param("max_pages_per_run", "20"))

w = WorkspaceClient()
wiki = WikiClient(warehouse_id=WAREHOUSE_ID, workspace_client=w)


def run_sql(sql: str) -> list[dict]:
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql,
        wait_timeout="60s",
    )
    rows = resp.result.data_array or []
    if not rows:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, r)) for r in rows]


# COMMAND ----------

# MAGIC %md ## Pull pages flagged 'oversize' by the curate health check

# COMMAND ----------

oversize = run_sql(
    f"SELECT page_id, path, title, page_type, "
    f"content:body::STRING AS body, tags "
    f"FROM {PAGES_TABLE} "
    f"WHERE health_status = 'oversize' "
    f"  AND parent_id IS NULL "  # don't recursively split chunks
    f"ORDER BY updated_at ASC "
    f"LIMIT {MAX_PAGES_PER_RUN}"
)
print(f"oversize pages to segregate: {len(oversize)}")

if not oversize:
    dbutils.notebook.exit("no oversize pages")  # noqa: F821

# COMMAND ----------

# MAGIC %md ## Split + summarize each
# MAGIC
# MAGIC For each page: deterministic chunker → ask Claude for a summary + one
# MAGIC title per chunk → write children with `parent_id` + `chunk_index` →
# MAGIC rewrite the parent body to summary + ToC.

# COMMAND ----------

def llm_summary_and_titles(title: str, body: str, num_chunks: int) -> tuple[str, list[str]]:
    """Ask the chat endpoint for a 1-2 sentence summary and `num_chunks` titles.

    Returns `(summary, titles)`. Falls back to generic titles if parsing fails.
    """
    schema_hint = json.dumps({"summary": "string", "titles": ["string"] * num_chunks})
    prompt = (
        f"You are organizing a long wiki page titled {title!r} for a knowledge base. "
        f"It has been split into {num_chunks} chunks in order. "
        f"Return a JSON object with keys `summary` (1-2 sentences capturing the "
        f"whole page) and `titles` (a list of exactly {num_chunks} short titles, "
        f"one per chunk, in order). Reply with ONLY the JSON object, no prose. "
        f"Schema: {schema_hint}\n\nPage body:\n{body[:30_000]}"
    )
    resp = w.serving_endpoints.query(
        name=CHAT_ENDPOINT,
        messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(raw)
        summary = (parsed.get("summary") or "").strip()
        titles = list(parsed.get("titles") or [])
    except Exception:
        summary, titles = "", []
    while len(titles) < num_chunks:
        titles.append(f"Part {len(titles) + 1}")
    return summary or f"Long document split into {num_chunks} parts.", titles[:num_chunks]


segregated = 0
skipped = 0

for page in oversize:
    body = page.get("body") or ""
    chunks = chunk_at_boundaries(body, max_chars=MAX_CHARS_PER_CHUNK)
    if len(chunks) <= 1:
        # Nothing this notebook can do — paragraph itself is oversize.
        wiki._log(  # noqa: SLF001
            "segregate_skip", path=page["path"],
            details=f"chunks={len(chunks)} reason=single-paragraph",
        )
        skipped += 1
        continue

    summary, titles = llm_summary_and_titles(page["title"], body, len(chunks))

    toc = []
    for idx, (chunk_body, chunk_t) in enumerate(zip(chunks, titles), start=1):
        cp = child_path(page["path"], idx)
        ct = child_title(page["title"], chunk_t)
        wiki.write_page(
            path=cp,
            title=ct,
            content_json={"summary": chunk_t, "body": chunk_body},
            page_type="chunk",
            created_by="segregate",
            tags=["chunk"],
            parent_id=page["page_id"],
            chunk_index=idx,
        )
        toc.append({"path": cp, "title": ct})

    parent_body = build_parent_body(summary=summary, toc=toc)
    wiki.write_page(
        path=page["path"],
        title=page["title"],
        content_json={"summary": summary, "body": parent_body},
        page_type=page.get("page_type") or "concept",
        created_by="segregate",
        tags=list(page.get("tags") or []),
    )

    # Mark the parent healthy so curate doesn't keep re-flagging it.
    w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(
            f"UPDATE {PAGES_TABLE} "
            f"SET health_status = 'ok', health_score = 1.0, "
            f"last_health_check = current_timestamp() "
            f"WHERE page_id = '{page['page_id']}'"
        ),
        wait_timeout="30s",
    )

    wiki._log(  # noqa: SLF001
        "segregate", path=page["path"],
        details=f"chunks={len(chunks)} titles={titles!r}",
    )
    segregated += 1

print(f"segregated: {segregated}, skipped: {skipped}")

# COMMAND ----------

# MAGIC %md ## Sync the index so the new chunk children are searchable

# COMMAND ----------

if segregated > 0:
    wiki.sync_index()
    print("triggered VS index sync")
