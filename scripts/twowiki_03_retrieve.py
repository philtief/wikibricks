"""2WikiMultiHopQA — step 3: retrieve top-K passages per dev query.

For each dev question × each mode in {HYBRID, ANN, FULL_TEXT}, queries the
wiki_2wiki VS index and writes data/twowiki/retrieved_{mode}.jsonl. Each line:

    {"id": qid, "mode": mode, "retrieved": [{path, title, sentences, score}, ...]}

Parallelized with ThreadPoolExecutor (20 workers). Env:
    TWOWIKI_SAMPLE=N  (0 = full 12,576)
    TWOWIKI_MODES=HYBRID,ANN,FULL_TEXT  (subset supported)
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from databricks.sdk import WorkspaceClient

os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fe-vm-agent-marketplace")

CATALOG = "agent_marketplace_catalog"
SCHEMA = "wiki_2wiki"
WAREHOUSE_ID = "41754a8563a43a49"
VS_INDEX = f"{CATALOG}.{SCHEMA}.pages_index"
QUERIES_PATH = Path("src/wikibricks/seeds/twowiki/queries.jsonl")
OUT_DIR = Path("data/twowiki")

DEFAULT_MODES = ["HYBRID", "ANN", "FULL_TEXT"]
MODES = [m.strip() for m in os.environ.get("TWOWIKI_MODES", ",".join(DEFAULT_MODES)).split(",") if m.strip()]
RETRIEVE_K = 20
WORKERS = 20
SAMPLE_SIZE = int(os.environ.get("TWOWIKI_SAMPLE", "0"))

w = WorkspaceClient()


def load_queries() -> list[dict]:
    with open(QUERIES_PATH) as f:
        queries = [json.loads(line) for line in f if line.strip()]
    if SAMPLE_SIZE and SAMPLE_SIZE < len(queries):
        import random
        random.seed(42)
        queries = random.sample(queries, SAMPLE_SIZE)
        print(f"Sampled {SAMPLE_SIZE} queries (seed=42)")
    return queries


def query_once(question: str, mode: str) -> list[dict]:
    kwargs = {
        "index_name": VS_INDEX,
        "columns": ["page_id", "path", "title", "content_text"],
        "query_text": question,
        "num_results": RETRIEVE_K,
    }
    if mode != "ANN":
        kwargs["query_type"] = mode
    try:
        resp = w.vector_search_indexes.query_index(**kwargs)
    except Exception as e:
        print(f"  query error ({mode}): {e}", file=sys.stderr)
        return []
    if not resp.result or not resp.result.data_array:
        return []
    cols = [c.name for c in resp.manifest.columns]
    out = []
    for row in resp.result.data_array:
        rec = dict(zip(cols, row))
        out.append({
            "path": rec.get("path"),
            "title": rec.get("title"),
            "content_text": rec.get("content_text") or "",
            "score": float(rec.get("score", 0.0)),
        })
    return out


def run_mode(queries: list[dict], mode: str, out_path: Path) -> None:
    print(f"\n=== mode: {mode} ({len(queries):,} queries, {WORKERS} workers) ===")
    t0 = time.time()
    results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(query_once, q["question"], mode): q for q in queries}
        done = 0
        for fut in as_completed(futures):
            q = futures[fut]
            results[q["id"]] = fut.result()
            done += 1
            if done % 500 == 0:
                dt = time.time() - t0
                rate = done / dt
                eta = (len(queries) - done) / rate
                print(f"  {done:,}/{len(queries):,}  {rate:.1f} q/s  eta {int(eta)}s")
    with open(out_path, "w") as f:
        for q in queries:
            f.write(json.dumps({"id": q["id"], "mode": mode,
                                "retrieved": results.get(q["id"], [])}) + "\n")
    dt = time.time() - t0
    print(f"  wrote {out_path} ({dt:.0f}s)")


def main() -> None:
    queries = load_queries()
    print(f"Loaded {len(queries):,} dev queries")
    print(f"Modes: {MODES}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for mode in MODES:
        out_path = OUT_DIR / f"retrieved_{mode}.jsonl"
        run_mode(queries, mode, out_path)

    print("\n=== retrieval done ===")


if __name__ == "__main__":
    main()
