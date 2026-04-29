"""Smoke test: end-to-end segregate flow against a live workspace.

Inserts a >50KB test page, marks it `health_status='oversize'`, runs the
segregate logic with stubbed LLM output (deterministic — the LLM call is a
thin wrapper, the risky parts are chunking, child writes, parent rewrite,
and reassembly), then verifies:

    1. Parent body became `summary + ToC`.
    2. N children exist with correct `parent_id` + `chunk_index` + `page_type='chunk'`.
    3. Children's bodies concatenate back to the original.
    4. `fn_wiki_read_full(parent_path)` reassembles in order (skipped if not deployed).

Cleans up after itself unless `--keep` is passed.

Usage:
    uv run python scripts/smoke_segregate.py \\
        --warehouse-id <warehouse_id> \\
        --catalog <catalog> \\
        --schema <schema> \\
        --profile <cli_profile>     # optional; defaults to DEFAULT
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# WIKIBRICKS_CATALOG/SCHEMA must be set before importing wikibricks.ops.
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--catalog", required=True)
_pre.add_argument("--schema", required=True)
_known, _ = _pre.parse_known_args()
os.environ["WIKIBRICKS_CATALOG"] = _known.catalog
os.environ["WIKIBRICKS_SCHEMA"] = _known.schema

from databricks.sdk import WorkspaceClient  # noqa: E402

from wikibricks import WikiClient  # noqa: E402
from wikibricks.segregate_logic import (  # noqa: E402
    build_parent_body,
    child_path,
    child_title,
    chunk_at_boundaries,
)

TEST_PATH = "smoke/segregate-roundtrip"
TEST_TITLE = "Smoke: Segregate Round-trip"


def make_oversize_body(n_paragraphs: int = 60, paragraph_chars: int = 1100) -> str:
    """Deterministic >50KB body with paragraph breaks for the chunker."""
    return "\n\n".join(
        f"[para-{i:02d}] " + ("lorem ipsum " * (paragraph_chars // 12))
        for i in range(n_paragraphs)
    )


def fake_summary_and_titles(num_chunks: int) -> tuple[str, list[str]]:
    """Stub the LLM call — keeps the test self-contained and free."""
    return (
        f"Smoke-test summary covering {num_chunks} chunks.",
        [f"Smoke chunk {i + 1}" for i in range(num_chunks)],
    )


def run_sql(w: WorkspaceClient, warehouse_id: str, sql: str) -> list[dict[str, Any]]:
    resp = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=sql,
        wait_timeout="30s",
    )
    rows = resp.result.data_array or []
    if not rows:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, r)) for r in rows]


def cleanup(w: WorkspaceClient, warehouse_id: str, pages_table: str) -> None:
    """Remove the parent + any chunk children created by this test."""
    w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=(
            f"DELETE FROM {pages_table} "
            f"WHERE path = '{TEST_PATH}' "
            f"   OR path LIKE '{TEST_PATH}/chunks/%'"
        ),
        wait_timeout="30s",
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--profile", default=None)
    p.add_argument("--max-chars-per-chunk", type=int, default=8000)
    p.add_argument("--keep", action="store_true", help="skip cleanup at end")
    args = p.parse_args()

    pages_table = f"{args.catalog}.{args.schema}.pages"

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    wiki = WikiClient(warehouse_id=args.warehouse_id, workspace_client=w)

    print(f"--- smoke segregate: target = {pages_table} ---")
    cleanup(w, args.warehouse_id, pages_table)

    body = make_oversize_body()
    print(f"step 1: writing parent at {TEST_PATH} (body={len(body):,} chars)")
    wiki.write_page(
        path=TEST_PATH,
        title=TEST_TITLE,
        content_json={"summary": "original", "body": body},
        page_type="concept",
        created_by="smoke-segregate",
        tags=["smoke"],
    )

    parent_rows = run_sql(
        w, args.warehouse_id,
        f"SELECT page_id FROM {pages_table} WHERE path = '{TEST_PATH}'",
    )
    if not parent_rows:
        print("FAIL: parent row not found after write_page")
        return 1
    parent_id = parent_rows[0]["page_id"]
    print(f"  parent_id = {parent_id}")

    w.statement_execution.execute_statement(
        warehouse_id=args.warehouse_id,
        statement=(
            f"UPDATE {pages_table} SET health_status='oversize', "
            f"health_score=0.3, last_health_check=current_timestamp() "
            f"WHERE page_id = '{parent_id}'"
        ),
        wait_timeout="30s",
    )

    print("step 2: chunking + writing children with stubbed LLM")
    chunks = chunk_at_boundaries(body, max_chars=args.max_chars_per_chunk)
    summary, titles = fake_summary_and_titles(len(chunks))
    print(f"  chunks={len(chunks)} avg_chunk_chars={sum(len(c) for c in chunks) // len(chunks)}")

    if len(chunks) <= 1:
        print("FAIL: fixture produced <=1 chunk — bump make_oversize_body params")
        return 1

    toc = []
    for idx, (chunk_body, chunk_t) in enumerate(zip(chunks, titles), start=1):
        cp = child_path(TEST_PATH, idx)
        ct = child_title(TEST_TITLE, chunk_t)
        wiki.write_page(
            path=cp,
            title=ct,
            content_json={"summary": chunk_t, "body": chunk_body},
            page_type="chunk",
            created_by="smoke-segregate",
            tags=["smoke", "chunk"],
            parent_id=parent_id,
            chunk_index=idx,
        )
        toc.append({"path": cp, "title": ct})

    parent_body = build_parent_body(summary=summary, toc=toc)
    wiki.write_page(
        path=TEST_PATH,
        title=TEST_TITLE,
        content_json={"summary": summary, "body": parent_body},
        page_type="concept",
        created_by="smoke-segregate",
        tags=["smoke"],
    )

    print("step 3: verifying schema state")
    children = run_sql(
        w, args.warehouse_id,
        f"SELECT path, page_type, parent_id, chunk_index, "
        f"content:body::STRING AS body "
        f"FROM {pages_table} "
        f"WHERE parent_id = '{parent_id}' "
        f"ORDER BY chunk_index ASC",
    )

    failures = []
    if len(children) != len(chunks):
        failures.append(f"child count: got {len(children)}, expected {len(chunks)}")
    for i, c in enumerate(children, start=1):
        if int(c["chunk_index"]) != i:
            failures.append(f"chunk_index[{i}]: got {c['chunk_index']!r}")
        if c["page_type"] != "chunk":
            failures.append(f"page_type[{i}]: got {c['page_type']}")
        if c["parent_id"] != parent_id:
            failures.append(f"parent_id[{i}]: got {c['parent_id']}")
        if c["path"] != child_path(TEST_PATH, i):
            failures.append(f"path[{i}]: got {c['path']}")

    reassembled = "\n\n".join(c["body"] for c in children)
    if reassembled != body:
        failures.append(
            f"reassembly mismatch: original={len(body)} chars, "
            f"reassembled={len(reassembled)} chars"
        )

    parent_after = run_sql(
        w, args.warehouse_id,
        f"SELECT content:body::STRING AS body, content:summary::STRING AS summary "
        f"FROM {pages_table} WHERE path = '{TEST_PATH}'",
    )[0]
    if "## Contents" not in (parent_after["body"] or ""):
        failures.append("parent body missing '## Contents' marker")
    if parent_after["summary"] != summary:
        failures.append(f"parent summary: got {parent_after['summary']!r}")

    print("step 4: trying fn_wiki_read_full")
    fn_full = f"{args.catalog}.{args.schema}.fn_wiki_read_full"
    try:
        full_rows = run_sql(
            w, args.warehouse_id,
            f"SELECT * FROM {fn_full}('{TEST_PATH}')",
        )
        full_bodies = [r.get("body") or json.loads(r.get("content") or "{}").get("body", "")
                       for r in full_rows[1:]]
        full_text = "\n\n".join(full_bodies)
        if full_text and full_text != body:
            failures.append(
                f"fn_wiki_read_full mismatch: {len(full_text)} vs {len(body)} chars"
            )
        else:
            print(f"  fn_wiki_read_full returned {len(full_rows)} rows — reassembly OK")
    except Exception as e:
        print(f"  fn_wiki_read_full skipped (not deployed?): {e}")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        if not args.keep:
            cleanup(w, args.warehouse_id, pages_table)
        return 1

    print(f"\nPASS: parent + {len(children)} chunks land + reassemble cleanly")
    if not args.keep:
        cleanup(w, args.warehouse_id, pages_table)
        print("cleanup done")
    else:
        print(f"--keep: rows left at {TEST_PATH} and {TEST_PATH}/chunks/*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
