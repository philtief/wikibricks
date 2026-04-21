"""2WikiMultiHopQA — run cheap-lever ablations over the same 250+ qids.

Uses the qids already predicted in data/twowiki/predictions_HYBRID.json as the fixed
evaluation set (same qids across variants → apples-to-apples). Variants:

  A_baseline   HYBRID  haiku-4-5   K=5   original prompt          (reuses predictions_HYBRID.json)
  B_k10        HYBRID  haiku-4-5   K=10  original prompt
  C_sonnet     HYBRID  sonnet-4-6  K=5   original prompt
  D_cevi       HYBRID  haiku-4-5   K=5   constrained-evidence prompt
  E_ann        ANN     haiku-4-5   K=5   original prompt

Each variant writes data/twowiki/variant_<name>.json (predictions in official schema)
and then scores via the vendored v1.1 evaluator on a gold subset matching the qids.

No checkpoint table writes. These are one-shot over a small set (~350 queries).
"""

import json
import os
import re
import subprocess
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
EVAL = Path("vendor/2wikimultihop_evaluate_v1.1.py")
GOLD = Path("data/twowiki/raw/dev.json")
ALIAS = Path("data/twowiki/raw/id_aliases.json")

WORKERS = int(os.environ.get("TWOWIKI_WORKERS", "20"))
w = WorkspaceClient()


PROMPT_BASE = """You answer multi-hop questions using the provided Wikipedia paragraphs.

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


PROMPT_CEVI = """You answer multi-hop questions using the provided Wikipedia paragraphs.

Return STRICT JSON with exactly these keys, no prose:
{{
  "answer": "<shortest span that exactly answers the question, or 'yes'/'no'>",
  "sp":     [["<paragraph_title>", <sentence_index>], ...],
  "evidence": [["<subject>", "<relation>", "<object>"], ...]
}}

Rules:
- answer must be the minimal entity or span, or literally "yes" / "no" for yes/no questions.
- sp lists only the sentences you actually used, by (exact title, sentence index).
- **evidence subject must be EXACTLY one of the paragraph titles listed below** — do not
  paraphrase, do not add "the", do not reorder. Copy it verbatim.
- evidence object must be the entity name as it appears in the paragraph text — a short
  noun phrase, year, or proper name. No sentences, no descriptions.
- relation is a concise snake_case predicate: "director", "mother", "father", "spouse",
  "country_of_citizenship", "place_of_birth", "place_of_death", "date_of_birth",
  "date_of_death", "publication_date", "inception", "composer", "screenwriter",
  "producer", "author", "performer", "owned_by".
- Use only facts present in the paragraphs. Return [] if unsure.

Paragraphs:
{paragraphs}

Question: {question}

JSON:"""


def load_pages_sentences() -> dict[str, tuple[str, list[str]]]:
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


def render_paragraphs(passages: list[dict], sent_lookup, k: int) -> str:
    lines = []
    for idx, p in enumerate(passages[:k], 1):
        path = p.get("path")
        title, sents = sent_lookup.get(path, (p.get("title", "?"), []))
        if not sents:
            ct = p.get("content_text") or ""
            sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", ct) if s.strip()]
        lines.append(f"[{idx}] Title: {title}")
        for s_idx, s in enumerate(sents):
            lines.append(f"  [{s_idx}] {s}")
    return "\n".join(lines)


def parse_json_lenient(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


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
               sent_lookup, model: str, k: int, prompt_template: str) -> dict:
    if not passages:
        return {"answer": "noanswer", "sp": [], "evidence": []}
    para = render_paragraphs(passages, sent_lookup, k)
    prompt = prompt_template.format(paragraphs=para, question=question)
    try:
        resp = w.serving_endpoints.query(
            name=model,
            messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
            max_tokens=800,
            temperature=0,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"  llm error {qid}: {e}", file=sys.stderr)
        return {"answer": "noanswer", "sp": [], "evidence": []}

    parsed = parse_json_lenient(raw)
    if parsed is None:
        try:
            resp = w.serving_endpoints.query(
                name=model,
                messages=[ChatMessage(role=ChatMessageRole.USER,
                                      content=prompt + "\n\nReturn ONLY the JSON object.")],
                max_tokens=800,
                temperature=0,
            )
            raw = resp.choices[0].message.content or ""
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


def run_variant(name: str, mode: str, model: str, k: int, prompt: str,
                qids: list[str], queries: dict, sent_lookup) -> Path:
    out_path = OUT_DIR / f"variant_{name}.json"
    if out_path.exists():
        print(f"  {name}: skip (already exists)")
        return out_path

    retrieved = load_retrieved(mode)
    predictions = {"answer": {}, "sp": {}, "evidence": {}}
    qids_to_run = [q for q in qids if q in retrieved and q in queries]
    t0 = time.time()
    print(f"\n=== {name}: mode={mode} model={model} k={k} "
          f"prompt={'cevi' if prompt is PROMPT_CEVI else 'base'} "
          f"({len(qids_to_run)} queries) ===", flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {
            ex.submit(answer_one, qid, queries[qid]["question"],
                      retrieved[qid], sent_lookup, model, k, prompt): qid
            for qid in qids_to_run
        }
        done = 0
        for fut in as_completed(futures):
            qid = futures[fut]
            pred = fut.result()
            predictions["answer"][qid] = pred["answer"]
            predictions["sp"][qid] = pred["sp"]
            predictions["evidence"][qid] = pred["evidence"]
            done += 1
            if done % 50 == 0:
                dt = time.time() - t0
                print(f"  {done}/{len(qids_to_run)}  {done/dt:.1f} q/s", flush=True)

    with open(out_path, "w") as f:
        json.dump(predictions, f, indent=2, ensure_ascii=False)
    dt = time.time() - t0
    print(f"  {name}: wrote {out_path} in {dt:.0f}s", flush=True)
    return out_path


def eval_variant(name: str, pred_path: Path, gold: list[dict]) -> dict:
    with open(pred_path) as f:
        pred = json.load(f)
    pred_qids = set(pred.get("answer", {}).keys())
    subset = [dp for dp in gold if dp["_id"] in pred_qids]
    subset_path = OUT_DIR / f"subset_{name}.json"
    with open(subset_path, "w") as f:
        json.dump(subset, f)

    r = subprocess.run(
        [sys.executable, str(EVAL), str(pred_path), str(subset_path), str(ALIAS)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"eval failed for {name}: {r.stderr[-500:]}", file=sys.stderr)
        return {"error": r.stderr[-200:]}

    m = re.search(r"\{[^{}]*\"em\".*?\}", r.stdout, re.DOTALL)
    metrics = json.loads(m.group(0))
    metrics["n"] = len(subset)
    return metrics


def main() -> None:
    baseline_path = OUT_DIR / "predictions_HYBRID.json"
    if not baseline_path.exists():
        print(f"missing {baseline_path} — run twowiki_04_generate first", file=sys.stderr)
        sys.exit(1)
    with open(baseline_path) as f:
        baseline = json.load(f)
    qids = list(baseline["answer"].keys())
    print(f"fixed-qid set: {len(qids)} queries from predictions_HYBRID.json")

    sent_lookup = load_pages_sentences()
    queries = load_queries()

    variants = [
        ("A_baseline",         "HYBRID", "databricks-claude-haiku-4-5",  5,  PROMPT_BASE),
        ("B_k10",              "HYBRID", "databricks-claude-haiku-4-5",  10, PROMPT_BASE),
        ("C_sonnet",           "HYBRID", "databricks-claude-sonnet-4-6", 5,  PROMPT_BASE),
        ("D_cevi",             "HYBRID", "databricks-claude-haiku-4-5",  5,  PROMPT_CEVI),
        ("E_ann",              "ANN",    "databricks-claude-haiku-4-5",  5,  PROMPT_BASE),
        ("F_sonnet_k10",       "HYBRID", "databricks-claude-sonnet-4-6", 10, PROMPT_BASE),
        ("G_sonnet_ann",       "ANN",    "databricks-claude-sonnet-4-6", 5,  PROMPT_BASE),
        ("H_sonnet_ann_k10",   "ANN",    "databricks-claude-sonnet-4-6", 10, PROMPT_BASE),
    ]

    # A baseline reuses existing predictions_HYBRID.json
    a_path = OUT_DIR / "variant_A_baseline.json"
    if not a_path.exists():
        with open(baseline_path) as f:
            data = json.load(f)
        with open(a_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  A_baseline: copied from predictions_HYBRID.json")

    paths = {}
    for v in variants:
        name = v[0]
        if name == "A_baseline":
            paths[name] = a_path
            continue
        paths[name] = run_variant(*v, qids=qids, queries=queries, sent_lookup=sent_lookup)

    print("\nloading gold for eval...")
    with open(GOLD) as f:
        gold = json.load(f)

    print("\n=== variant eval ===")
    results = {}
    for name in [v[0] for v in variants]:
        m = eval_variant(name, paths[name], gold)
        results[name] = m
        if "error" in m:
            print(f"  {name:12s}  FAILED")
            continue
        print(f"  {name:12s}  n={m['n']}  "
              f"Ans F1={m['f1']:5.1f}  Sup F1={m['sp_f1']:5.1f}  "
              f"Evi F1={m['evi_f1']:5.1f}  Joint F1={m['joint_f1']:5.1f}")

    out = OUT_DIR / "variants_summary.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
