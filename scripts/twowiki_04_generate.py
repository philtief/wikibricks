"""2WikiMultiHopQA — step 4: LLM answer/sp/evidence generation per query.

Reads retrieved_{mode}.jsonl + queries.jsonl. For each query:
  - Take top-K retrieved pages, pull original sentence lists from seed pages.jsonl
  - Prompt Claude with paragraphs indexed by [passage_num, sent_idx]
  - Parse strict JSON {answer, sp, evidence}
  - Write predictions_{mode}.json in the exact schema the official eval v1.1 expects:
        {"answer":   {qid: str},
         "sp":       {qid: [[title, sent_idx], ...]},
         "evidence": {qid: [[subj, pred, obj], ...]}}

Parallelized with ThreadPoolExecutor. Retries once on malformed JSON.

Env:
  TWOWIKI_SAMPLE=N        (0 = all queries in retrieval file)
  TWOWIKI_MODES=ANN,...   (default: all three)
  TWOWIKI_CONTEXT_K=5     (# of passages shown to the model)
  TWOWIKI_MODEL=databricks-claude-sonnet-4-6
  TWOWIKI_WORKERS=10
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fe-vm-agent-marketplace")

QUERIES_PATH = Path("src/wikibricks/seeds/twowiki/queries.jsonl")
PAGES_PATH = Path("src/wikibricks/seeds/twowiki/pages.jsonl")
IN_DIR = Path("data/twowiki")
OUT_DIR = Path("data/twowiki")

CATALOG = "agent_marketplace_catalog"
SCHEMA = "wiki_2wiki"
WAREHOUSE_ID = "41754a8563a43a49"
CHECKPOINT_TABLE = f"{CATALOG}.{SCHEMA}.predictions_checkpoints"

DEFAULT_MODES = ["HYBRID", "ANN", "FULL_TEXT"]
MODES = [m.strip() for m in os.environ.get("TWOWIKI_MODES", ",".join(DEFAULT_MODES)).split(",") if m.strip()]
CONTEXT_K = int(os.environ.get("TWOWIKI_CONTEXT_K", "5"))
MODEL = os.environ.get("TWOWIKI_MODEL", "databricks-claude-sonnet-4-6")
WORKERS = int(os.environ.get("TWOWIKI_WORKERS", "10"))
SAMPLE = int(os.environ.get("TWOWIKI_SAMPLE", "0"))
BATCH_SIZE = int(os.environ.get("TWOWIKI_BATCH_SIZE", "0"))  # 0 = no cap
CHECKPOINT_EVERY = int(os.environ.get("TWOWIKI_CHECKPOINT_EVERY",
                                      "50" if BATCH_SIZE else "1000"))

w = WorkspaceClient()


def _sql_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "''")


def ensure_checkpoint_table() -> None:
    w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"""
            CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
                qid STRING,
                mode STRING,
                answer STRING,
                sp STRING,
                evidence STRING,
                created_at TIMESTAMP
            ) USING DELTA
        """,
        wait_timeout="30s",
    )


def load_checkpoints(mode: str) -> dict[str, dict]:
    """Return {qid: {answer, sp, evidence}} for already-done qids in this mode."""
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"SELECT qid, answer, sp, evidence FROM {CHECKPOINT_TABLE} "
                  f"WHERE mode = '{_sql_escape(mode)}'",
        wait_timeout="50s",
    )
    out: dict[str, dict] = {}
    if not r.result or not r.result.data_array:
        return out
    for row in r.result.data_array:
        qid, ans, sp, ev = row
        try:
            out[qid] = {
                "answer": ans or "noanswer",
                "sp": json.loads(sp) if sp else [],
                "evidence": json.loads(ev) if ev else [],
            }
        except json.JSONDecodeError:
            out[qid] = {"answer": ans or "noanswer", "sp": [], "evidence": []}
    return out


def flush_checkpoint(mode: str, rows: list[dict]) -> None:
    """MERGE a batch of predictions into the checkpoint table."""
    if not rows:
        return
    values = []
    for r in rows:
        qid = _sql_escape(str(r["qid"]))
        ans = _sql_escape(str(r["answer"]))
        sp = _sql_escape(json.dumps(r["sp"]))
        ev = _sql_escape(json.dumps(r["evidence"]))
        values.append(f"('{qid}', '{ans}', '{sp}', '{ev}')")
    sql = f"""
        MERGE INTO {CHECKPOINT_TABLE} t
        USING (SELECT col1 AS qid, col2 AS answer, col3 AS sp, col4 AS evidence
               FROM (VALUES {','.join(values)})) s
        ON t.qid = s.qid AND t.mode = '{_sql_escape(mode)}'
        WHEN MATCHED THEN UPDATE SET
            answer = s.answer, sp = s.sp, evidence = s.evidence,
            created_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT (qid, mode, answer, sp, evidence, created_at)
            VALUES (s.qid, '{_sql_escape(mode)}', s.answer, s.sp, s.evidence, current_timestamp())
    """
    w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=sql, wait_timeout="50s",
    )


def load_pages_sentences() -> dict[str, tuple[str, list[str]]]:
    """path -> (title, sentences)."""
    out = {}
    with open(PAGES_PATH) as f:
        for line in f:
            p = json.loads(line)
            sents = [s["text"] for s in p["content"]["paragraphs"]]
            out[p["path"]] = (p["title"], sents)
    return out


def load_queries() -> dict[str, dict]:
    out = {}
    with open(QUERIES_PATH) as f:
        for line in f:
            q = json.loads(line)
            out[q["id"]] = q
    return out


def load_retrieved(mode: str) -> dict[str, list[dict]]:
    path = IN_DIR / f"retrieved_{mode}.jsonl"
    out = {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            out[r["id"]] = r["retrieved"]
    return out


PROMPT = """You answer multi-hop questions using the provided Wikipedia paragraphs.

Return STRICT JSON with exactly these keys, no prose:
{{
  "answer": "<shortest span that exactly answers the question, or 'yes'/'no'>",
  "sp":     [["<paragraph_title>", <sentence_index>], ...],
  "evidence": [["<subject_surface_form>", "<relation>", "<object_surface_form>"], ...]
}}

Rules:
- answer must be the minimal entity or span, or literally "yes" / "no" for yes/no questions.
- sp lists only the sentences you actually used, by (exact title, sentence index shown below).
- evidence lists Wikidata-style triples you relied on. Relations are concise English
  predicates like "director", "mother", "country_of_citizenship", "spouse",
  "place_of_birth", "publication_date", "inception", "date_of_death".
- Use only facts present in the paragraphs. Return [] if unsure for sp / evidence.

Paragraphs:
{paragraphs}

Question: {question}

JSON:"""


def render_paragraphs(passages: list[dict], sent_lookup) -> str:
    lines = []
    for idx, p in enumerate(passages, 1):
        path = p.get("path")
        title, sents = sent_lookup.get(path, (p.get("title", "?"), []))
        if not sents:
            # Fallback: split content_text on sentences (rare — when path missing from lookup).
            ct = p.get("content_text") or ""
            sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", ct) if s.strip()]
        lines.append(f"[{idx}] Title: {title}")
        for s_idx, s in enumerate(sents):
            lines.append(f"  [{s_idx}] {s}")
    return "\n".join(lines)


def parse_json_lenient(text: str) -> dict | None:
    """Strip code fences etc. and try json.loads."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Extract first {...} block if extra prose.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def call_llm(prompt: str, retry_hint: str = "") -> str:
    msgs = [ChatMessage(role=ChatMessageRole.USER, content=prompt + retry_hint)]
    resp = w.serving_endpoints.query(
        name=MODEL,
        messages=msgs,
        max_tokens=800,
        temperature=0,
    )
    return resp.choices[0].message.content or ""


def coerce_sp(sp) -> list[list]:
    out = []
    if not isinstance(sp, list):
        return out
    for item in sp:
        if isinstance(item, list) and len(item) >= 2:
            try:
                out.append([str(item[0]), int(item[1])])
            except (ValueError, TypeError):
                continue
    return out


def coerce_evidence(ev) -> list[list]:
    out = []
    if not isinstance(ev, list):
        return out
    for item in ev:
        if isinstance(item, list) and len(item) >= 3:
            out.append([str(item[0]), str(item[1]), str(item[2])])
    return out


def answer_one(qid: str, question: str, passages: list[dict],
               sent_lookup) -> dict:
    if not passages:
        return {"answer": "noanswer", "sp": [], "evidence": []}
    para = render_paragraphs(passages[:CONTEXT_K], sent_lookup)
    prompt = PROMPT.format(paragraphs=para, question=question)
    try:
        raw = call_llm(prompt)
    except Exception as e:
        print(f"  llm error {qid}: {e}", file=sys.stderr)
        return {"answer": "noanswer", "sp": [], "evidence": []}

    parsed = parse_json_lenient(raw)
    if parsed is None:
        try:
            raw = call_llm(prompt, retry_hint="\n\nReturn ONLY the JSON object. No prose, no code fences.")
            parsed = parse_json_lenient(raw)
        except Exception:
            parsed = None

    if parsed is None:
        return {"answer": "noanswer", "sp": [], "evidence": []}

    return {
        "answer": str(parsed.get("answer", "noanswer")).strip() or "noanswer",
        "sp": coerce_sp(parsed.get("sp", [])),
        "evidence": coerce_evidence(parsed.get("evidence", [])),
    }


def run_mode(mode: str, queries: dict[str, dict], sent_lookup) -> None:
    retrieved = load_retrieved(mode)
    qids = list(retrieved.keys())
    if SAMPLE and SAMPLE < len(qids):
        import random
        random.seed(42)
        qids = random.sample(qids, SAMPLE)
        print(f"  sampled {SAMPLE} qids (seed=42)")

    # Resume: load already-checkpointed predictions; skip those qids.
    resumed = load_checkpoints(mode)
    predictions = {"answer": {}, "sp": {}, "evidence": {}}
    for qid, pred in resumed.items():
        predictions["answer"][qid] = pred["answer"]
        predictions["sp"][qid] = pred["sp"]
        predictions["evidence"][qid] = pred["evidence"]
    pending_qids = [q for q in qids if q in queries and q not in resumed]
    total_pending = len(pending_qids)
    if BATCH_SIZE and BATCH_SIZE < total_pending:
        pending_qids = pending_qids[:BATCH_SIZE]

    print(f"\n=== generating: {mode} ({len(pending_qids):,} to do this batch, "
          f"{total_pending:,} total pending, "
          f"{len(resumed):,} resumed, K={CONTEXT_K}, "
          f"model={MODEL}, workers={WORKERS}) ===", flush=True)

    t0 = time.time()
    buffer: list[dict] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {
            ex.submit(answer_one, qid, queries[qid]["question"],
                      retrieved[qid], sent_lookup): qid
            for qid in pending_qids
        }
        done = 0
        for fut in as_completed(futures):
            qid = futures[fut]
            pred = fut.result()
            predictions["answer"][qid] = pred["answer"]
            predictions["sp"][qid] = pred["sp"]
            predictions["evidence"][qid] = pred["evidence"]
            buffer.append({"qid": qid, **pred})
            done += 1
            if done % CHECKPOINT_EVERY == 0:
                try:
                    flush_checkpoint(mode, buffer)
                    buffer = []
                except Exception as e:
                    print(f"  checkpoint flush error: {e}", file=sys.stderr)
            if done % 100 == 0:
                dt = time.time() - t0
                rate = done / dt
                eta = (len(pending_qids) - done) / rate
                ck = " (ckpt)" if done % CHECKPOINT_EVERY == 0 else ""
                print(f"  {done:,}/{len(pending_qids):,}  {rate:.1f} q/s  eta {int(eta)}s{ck}", flush=True)

    if buffer:
        try:
            flush_checkpoint(mode, buffer)
        except Exception as e:
            print(f"  final checkpoint flush error: {e}", file=sys.stderr)

    out_path = OUT_DIR / f"predictions_{mode}.json"
    with open(out_path, "w") as f:
        json.dump(predictions, f, indent=2, ensure_ascii=False)
    dt = time.time() - t0
    print(f"  wrote {out_path}  ({dt:.0f}s)", flush=True)


def main() -> None:
    print("Ensuring checkpoint table exists...", flush=True)
    ensure_checkpoint_table()

    print("Loading pages (for sentence indices)...", flush=True)
    sent_lookup = load_pages_sentences()
    print(f"  {len(sent_lookup):,} pages", flush=True)

    print("Loading queries...", flush=True)
    queries = load_queries()
    print(f"  {len(queries):,} queries", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for mode in MODES:
        run_mode(mode, queries, sent_lookup)

    print("\n=== generation done ===", flush=True)


if __name__ == "__main__":
    main()
