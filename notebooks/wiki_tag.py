# Databricks notebook source
# MAGIC %md
# MAGIC # WikiBricks: Auto-Tag Pages (LLM-driven, opt-in)
# MAGIC
# MAGIC Reads recent top-level pages, asks the chat endpoint for 3-5 semantic
# MAGIC tags per page, MERGEs them into `wiki_vocabulary`, and appends the
# MAGIC `llm:`-prefixed tags to `pages.tags`. Logs one `auto_tag` event per
# MAGIC page to `wiki_log`.
# MAGIC
# MAGIC Deterministic bits (slug normalization, response parsing, vocab dedupe)
# MAGIC live in `src/wikibricks/tag_logic.py` and are unit-tested. The LLM is
# MAGIC only asked to propose free-form phrases; this notebook normalizes them
# MAGIC and decides what to commit. Library stays LLM-free.

# COMMAND ----------

# The `wikibricks` wheel is installed via task-level `libraries` in
# resources/wiki_curate_job.yml. No %pip install in this notebook.

# COMMAND ----------

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed


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
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

from wikibricks import WikiClient
from wikibricks.ops import PAGES_TABLE, VOCABULARY_TABLE
from wikibricks.tag_logic import (
    build_tag_event,
    dedupe_against_vocabulary,
    parse_tag_response,
    prefix_llm,
)


def _param(name: str, default: str) -> str:
    try:
        val = dbutils.widgets.get(name)  # noqa: F821
    except Exception:
        dbutils.widgets.text(name, default)  # noqa: F821
        val = default
    return val or default


WAREHOUSE_ID = _param("warehouse_id", "")
TAG_ENDPOINT = _param("tag_endpoint", "databricks-meta-llama-3-3-70b-instruct")
MAX_PAGES_PER_RUN = int(_param("max_pages_per_run", "20"))
TAG_CONCURRENCY = int(_param("tag_concurrency", "4"))
MAX_TAGS_PER_PAGE = int(_param("max_tags_per_page", "5"))

w = WorkspaceClient()
wiki = WikiClient(warehouse_id=WAREHOUSE_ID, workspace_client=w)


def run_sql(sql: str) -> list[dict]:
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql,
        wait_timeout="30s",
    )
    rows = resp.result.data_array or []
    if not rows:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, r)) for r in rows]


# COMMAND ----------

# MAGIC %md ## Pull recent un-tagged top-level pages

# COMMAND ----------

candidates = run_sql(
    f"SELECT page_id, path, title, content_text "
    f"FROM {PAGES_TABLE} "
    f"WHERE parent_id IS NULL "
    f"  AND created_at > current_timestamp() - INTERVAL 7 DAYS "
    f"  AND created_by NOT IN ('segregate', 'promote') "
    f"  AND NOT EXISTS(COALESCE(tags, array()), t -> t LIKE 'llm:%') "
    f"ORDER BY updated_at DESC "
    f"LIMIT {MAX_PAGES_PER_RUN}"
)
print(f"untagged pages to process: {len(candidates)}")

if not candidates:
    dbutils.notebook.exit("no untagged pages")  # noqa: F821

# COMMAND ----------

# MAGIC %md ## Pre-fetch existing vocabulary for dedupe

# COMMAND ----------

vocab_rows = run_sql(f"SELECT slug FROM {VOCABULARY_TABLE}")
existing_slugs = [r["slug"] for r in vocab_rows]
print(f"vocabulary terms in store: {len(existing_slugs)}")

# COMMAND ----------

# MAGIC %md ## LLM tag proposal (concurrent)

# COMMAND ----------


def llm_propose_tags(title: str, text: str) -> tuple[list[str], str]:
    """Return `(proposed_slugs, raw_response)` for one page.

    Falls back to `([], '')` on any error.
    """
    schema_hint = json.dumps({"tags": ["short-kebab-case-slug"] * MAX_TAGS_PER_PAGE})
    prompt = (
        f"You are tagging a wiki page titled {title!r} for retrieval. "
        f"Propose exactly {MAX_TAGS_PER_PAGE} short, specific tags that describe "
        f"the page's topics. Each tag is a short noun phrase (1-3 words). "
        f"Return ONLY a JSON object matching this schema: {schema_hint}\n\n"
        f"Page body (first 4000 chars):\n{(text or '')[:4000]}"
    )
    try:
        resp = w.serving_endpoints.query(
            name=TAG_ENDPOINT,
            messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        return ([], f"<llm_error: {type(e).__name__}: {e}>")
    return (parse_tag_response(raw), raw)


def process_page(page: dict) -> dict:
    proposed, raw = llm_propose_tags(page["title"], page.get("content_text") or "")
    proposed = proposed[:MAX_TAGS_PER_PAGE]
    deduped = [s for s in proposed if s in existing_slugs]
    committed = dedupe_against_vocabulary(proposed, existing_slugs)
    return {
        "page": page,
        "proposed": proposed,
        "committed": committed,
        "deduped": deduped,
        "raw": raw,
    }


with ThreadPoolExecutor(max_workers=TAG_CONCURRENCY) as ex:
    futures = {ex.submit(process_page, p): p for p in candidates}
    results = [f.result() for f in as_completed(futures)]

print(f"LLM proposed tags for {len(results)} pages")

# COMMAND ----------

# MAGIC %md ## Commit vocab + page tags + log

# COMMAND ----------

vocab_slugs: list[str] = []
for r in results:
    # Both new (committed) and seen-again (deduped) slugs are upserted so
    # existing vocab counts increment toward the promote threshold enforced
    # inside upsert_vocabulary_slugs (see _VOCAB_MIN_COUNT_FOR_ACTIVE).
    for slug in r["committed"] + r["deduped"]:
        vocab_slugs.append(slug)

if vocab_slugs:
    wiki.upsert_vocabulary_slugs(vocab_slugs, source="llm")

def append_page_tags(path: str, tags: list[str]) -> None:
    """Array-append `llm:`-prefixed tags onto a page without disturbing
    existing entries. WikiClient no longer ships a dedicated helper for
    this (tag preservation moved into the write_page/bulk_write_pages
    MERGE path in v0.7.4); the tag task touches `pages` directly via UPDATE.
    """
    tag_lits = ", ".join(f"'{t}'" for t in tags)
    path_esc = path.replace("'", "''")
    run_sql(
        f"UPDATE {PAGES_TABLE} "
        f"SET tags = array_distinct(concat(COALESCE(tags, array()), "
        f"                                  ARRAY({tag_lits}))) "
        f"WHERE path = '{path_esc}'"
    )


tagged_pages = 0
for r in results:
    all_slugs = r["committed"] + r["deduped"]
    if not all_slugs:
        continue
    append_page_tags(r["page"]["path"], prefix_llm(all_slugs))
    tagged_pages += 1
    event = build_tag_event(
        path=r["page"]["path"],
        proposed=r["proposed"],
        committed=r["committed"],
        deduped=r["deduped"],
        model=TAG_ENDPOINT,
        raw=r["raw"],
    )
    wiki._log("auto_tag", path=r["page"]["path"], details=json.dumps(event))  # noqa: SLF001

print(f"tagged: {tagged_pages}, vocab observations: {len(vocab_slugs)}")
