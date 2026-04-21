"""HotpotQA benchmark - step 3: run 7,405 queries × 3 modes + link-graph ablation.

Parallelized with ThreadPoolExecutor (20 workers). Writes benchmark_results.json
to repo root.
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from databricks.sdk import WorkspaceClient

os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", "fe-vm-agent-marketplace")

sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "src")))
from wikibricks.ops import (  # noqa: E402
    eval_mrr_multi,
    eval_recall_at_k_multi,
    eval_supporting_fact_f1,
)

CATALOG = "agent_marketplace_catalog"
SCHEMA = "wiki_hotpot"
WAREHOUSE_ID = "41754a8563a43a49"
VS_INDEX = f"{CATALOG}.{SCHEMA}.pages_index"
QUERIES_PATH = "src/wikibricks/seeds/hotpot/queries.jsonl"
OUT_PATH = "benchmark_results.json"
MODES = ["HYBRID", "ANN", "FULL_TEXT"]
K_VALUES = [2, 10]
WORKERS = 20
SAMPLE_SIZE = int(os.environ.get("HOTPOT_SAMPLE", "500"))  # 0 = full run

w = WorkspaceClient()


def extract_results(resp) -> list[tuple[str, float]]:
    """Return [(path, score), ...]. VS result rows put score in the last column."""
    if not resp.result or not resp.result.data_array:
        return []
    cols = [c.name for c in resp.manifest.columns]
    pi = cols.index("path")
    si = cols.index("score") if "score" in cols else -1
    return [(row[pi], float(row[si])) for row in resp.result.data_array]


RETRIEVE_K = 20  # wider pool so rerank can re-order within top-10


def query_once(question: str, mode: str, num_results: int = RETRIEVE_K) -> list[tuple[str, float]]:
    kwargs = {
        "index_name": VS_INDEX,
        "columns": ["page_id", "path", "title"],
        "query_text": question,
        "num_results": num_results,
    }
    if mode != "ANN":
        kwargs["query_type"] = mode
    try:
        return extract_results(w.vector_search_indexes.query_index(**kwargs))
    except Exception as e:
        print(f"  query error ({mode}, {question[:40]!r}): {e}", file=sys.stderr)
        return []


def load_queries() -> list[dict]:
    with open(QUERIES_PATH) as f:
        queries = [json.loads(line) for line in f if line.strip()]
    if SAMPLE_SIZE and SAMPLE_SIZE < len(queries):
        import random
        random.seed(42)
        queries = random.sample(queries, SAMPLE_SIZE)
        print(f"Sampled {SAMPLE_SIZE} queries (seed=42)")
    return queries


def fetch_supports_map() -> dict[str, list[str]]:
    """path -> list[target_path] for all link_type='supports' edges."""
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"""
            SELECT sp.path AS src, tp.path AS tgt
            FROM {CATALOG}.{SCHEMA}.links l
            JOIN {CATALOG}.{SCHEMA}.pages sp ON sp.page_id = l.source_page_id
            JOIN {CATALOG}.{SCHEMA}.pages tp ON tp.page_id = l.target_page_id
            WHERE l.link_type = 'supports'
        """,
        wait_timeout="50s",
    )
    # Poll if still running
    while r.status.state.value in ("PENDING", "RUNNING"):
        time.sleep(2)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state.value != "SUCCEEDED":
        raise RuntimeError(f"supports map fetch failed: {r.status}")
    rows = r.result.data_array or []
    out: dict[str, list[str]] = {}
    for src, tgt in rows:
        out.setdefault(src, []).append(tgt)
    return out


def run_mode(queries: list[dict], mode: str) -> tuple[dict, dict]:
    print(f"\n=== mode: {mode} ({len(queries):,} queries, {WORKERS} workers, k={RETRIEVE_K}) ===")
    t0 = time.time()
    per_query: dict[str, list[tuple[str, float]]] = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(query_once, q["question"], mode): q for q in queries}
        done = 0
        for fut in as_completed(futures):
            q = futures[fut]
            per_query[q["id"]] = fut.result()
            done += 1
            if done % 500 == 0:
                dt = time.time() - t0
                rate = done / dt
                eta = (len(queries) - done) / rate
                print(f"  {done:,}/{len(queries):,}  {rate:.1f} q/s  eta {int(eta)}s")

    # Compute metrics over top-10 slice (paths only)
    recall = {k: 0.0 for k in K_VALUES}
    mrr_sum = 0.0
    f1_sum = 0.0
    for q in queries:
        paths = [p for p, _ in per_query[q["id"]][:10]]
        relevant = q["relevant_paths"]
        for k in K_VALUES:
            recall[k] += eval_recall_at_k_multi(paths, relevant, k)
        mrr_sum += eval_mrr_multi(paths, relevant)
        f1_sum += eval_supporting_fact_f1(paths, relevant)

    n = len(queries)
    summary = {
        **{f"recall@{k}": round(recall[k] / n, 4) for k in K_VALUES},
        "mrr": round(mrr_sum / n, 4),
        "supporting_fact_f1": round(f1_sum / n, 4),
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(f"  {summary}")
    return summary, per_query


SUPPORT_DECAY = 0.9  # expansion candidate score = parent_score * decay


def _rerank_with_supports(
    retrieved: list[tuple[str, float]],
    supports: dict[str, list[str]],
    masked_edges: set[tuple[str, str]] | None = None,
) -> list[str]:
    """Merge retrieved + their supports neighbors, re-rank by score, return top-10 paths."""
    pool: dict[str, float] = {p: s for p, s in retrieved}
    for parent, parent_score in retrieved:
        for neighbor in supports.get(parent, []):
            if masked_edges and (parent, neighbor) in masked_edges:
                continue
            cand_score = parent_score * SUPPORT_DECAY
            if neighbor not in pool or pool[neighbor] < cand_score:
                pool[neighbor] = cand_score
    reranked = sorted(pool.items(), key=lambda x: x[1], reverse=True)
    return [p for p, _ in reranked[:10]]


def run_ablation(
    queries: list[dict],
    base_retrieved: dict[str, list[tuple[str, float]]],
    supports: dict[str, list[str]],
    *,
    loo_mask: bool,
    label: str,
) -> dict:
    print(f"\n=== ablation: {label} (replace-and-rerank, k={RETRIEVE_K}→10) ===")
    recall = {k: 0.0 for k in K_VALUES}
    mrr_sum = 0.0
    for q in queries:
        retrieved = base_retrieved.get(q["id"], [])
        if loo_mask and len(q["relevant_paths"]) == 2:
            a, b = q["relevant_paths"]
            masked = {(a, b), (b, a)}
        else:
            masked = None
        paths = _rerank_with_supports(retrieved, supports, masked)
        relevant = q["relevant_paths"]
        for k in K_VALUES:
            recall[k] += eval_recall_at_k_multi(paths, relevant, k)
        mrr_sum += eval_mrr_multi(paths, relevant)
    n = len(queries)
    summary = {
        **{f"recall@{k}": round(recall[k] / n, 4) for k in K_VALUES},
        "mrr": round(mrr_sum / n, 4),
    }
    print(f"  {summary}")
    return summary


def main():
    queries = load_queries()
    print(f"Loaded {len(queries):,} HotpotQA dev queries")

    print("Fetching supports map...")
    supports = fetch_supports_map()
    print(f"  {sum(len(v) for v in supports.values()):,} supports edges across "
          f"{len(supports):,} source pages")

    results_by_mode: dict[str, dict] = {}
    per_query_by_mode: dict[str, dict[str, list[str]]] = {}
    for mode in MODES:
        summary, per_q = run_mode(queries, mode)
        results_by_mode[mode] = summary
        per_query_by_mode[mode] = per_q

    # Run ablation on best base mode (ANN outperformed HYBRID at k=10 in earlier run).
    # LOO-masked = honest uplift (drops the edge between q's own golds).
    # Oracle = uncleaned dev graph (upper bound).
    base_mode = max(results_by_mode, key=lambda m: results_by_mode[m]["recall@10"])
    print(f"\nusing {base_mode} as ablation base (top recall@10)")
    ablation_loo = run_ablation(
        queries, per_query_by_mode[base_mode], supports,
        loo_mask=True, label=f"{base_mode} + supports, LOO-masked",
    )
    ablation_oracle = run_ablation(
        queries, per_query_by_mode[base_mode], supports,
        loo_mask=False, label=f"{base_mode} + supports, oracle (no mask)",
    )

    out = {
        "n_queries": len(queries),
        "retrieve_k": RETRIEVE_K,
        "corpus_size": None,  # filled by render step from warehouse
        "modes": results_by_mode,
        "ablation_base_mode": base_mode,
        "link_graph_ablation_loo": ablation_loo,
        "link_graph_ablation_oracle": ablation_oracle,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n=== wrote {OUT_PATH} ===")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
