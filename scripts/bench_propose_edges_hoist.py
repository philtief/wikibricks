"""Live benchmark for the propose_edges list_pages-hoist fix.

Hits the real warehouse against the personal philipp wiki. Compares:

- Before fix: per-call list_pages SQL (default behaviour when other_pages=None)
- After fix: pre-fetched list_pages, passed in via other_pages=

Run:
    uv run python scripts/bench_propose_edges_hoist.py \\
        --profile fe-vm-agent-marketplace \\
        --warehouse-id 41754a8563a43a49 \\
        --catalog agent_marketplace_catalog \\
        --schema wikibricks_personal_philipp \\
        --n 5
"""
from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor

from databricks.sdk import WorkspaceClient


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True)
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--concurrency", type=int, default=8,
                   help="ThreadPoolExecutor max_workers for the AFTER+CONCURRENT run")
    args = p.parse_args()

    os.environ["WIKIBRICKS_CATALOG"] = args.catalog
    os.environ["WIKIBRICKS_SCHEMA"] = args.schema

    from wikibricks import WikiClient
    from wikibricks.ops import PAGES_TABLE

    w = WorkspaceClient(profile=args.profile)
    wiki = WikiClient(warehouse_id=args.warehouse_id, workspace_client=w)

    # Pull `n` recent agent paths the same way the patched curate notebook will.
    sql = (
        f"SELECT path FROM {PAGES_TABLE} "
        f"WHERE updated_at >= current_timestamp() - INTERVAL 48 HOURS "
        f"  AND parent_id IS NULL "
        f"  AND (created_by IS NULL OR created_by NOT IN ('segregate', 'promote')) "
        f"  AND path NOT LIKE '_meta/%' "
        f"ORDER BY updated_at DESC LIMIT {args.n}"
    )
    resp = w.statement_execution.execute_statement(
        warehouse_id=args.warehouse_id, statement=sql, wait_timeout="30s",
    )
    paths = [r[0] for r in (resp.result.data_array or [])]
    if not paths:
        print("no candidate paths — try widening the lookback")
        return
    print(f"benchmarking {len(paths)} paths")

    # Warmup — pay warehouse cold-start cost once so it doesn't tax BEFORE alone.
    wiki.list_pages()
    wiki.propose_edges(paths[0], min_similarity=0.7)

    # --- BEFORE: each propose_edges call issues its own list_pages SQL ---
    t0 = time.monotonic()
    for path in paths:
        wiki.propose_edges(path, min_similarity=0.7)
    before = time.monotonic() - t0

    # --- AFTER: pre-fetch list_pages once, reuse across calls ---
    t0 = time.monotonic()
    all_pages = wiki.list_pages()
    for path in paths:
        wiki.propose_edges(path, min_similarity=0.7, other_pages=all_pages)
    after = time.monotonic() - t0

    per_before = before / len(paths)
    per_after = after / len(paths)
    # --- AFTER+CONCURRENT: list_pages hoisted + parallel propose_edges ---
    t0 = time.monotonic()
    all_pages_par = wiki.list_pages()

    def _one(p):
        return wiki.propose_edges(p, min_similarity=0.7, other_pages=all_pages_par)

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        list(ex.map(_one, paths))
    after_par = time.monotonic() - t0

    per_before = before / len(paths)
    per_after = after / len(paths)
    per_after_par = after_par / len(paths)
    print(f"BEFORE (list_pages inside loop):  {before:6.2f}s total  "
          f"{per_before:5.2f}s/page")
    print(f"AFTER  (list_pages hoisted):      {after:6.2f}s total  "
          f"{per_after:5.2f}s/page")
    print(f"AFTER+CONCURRENT (workers={args.concurrency}):"
          f"{' ' * max(0, 9 - len(str(args.concurrency)))}"
          f"{after_par:6.2f}s total  {per_after_par:5.2f}s/page")
    if per_before > 0:
        print(f"speedup per page (BEFORE→AFTER):       "
              f"{per_before / per_after:.1f}x")
    if per_after > 0:
        print(f"speedup per page (AFTER→CONCURRENT):   "
              f"{per_after / per_after_par:.1f}x")
    if before > 0:
        print(f"wall-time speedup (BEFORE→CONCURRENT): "
              f"{before / after_par:.1f}x")


if __name__ == "__main__":
    main()
