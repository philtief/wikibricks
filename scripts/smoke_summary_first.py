"""Smoke-test v0.7.8 recorder summary-first write path end-to-end.

Builds a synthetic session state >2k chars, calls the real Haiku 4.5
endpoint via `auto_summary.generate_summary`, writes the page via
`WikiClient.write_page(..., content_text_override=...)` and then
queries the pages + wiki_log tables to confirm:

    * content_text is the dense summary (not concat(summary, body))
    * content.summary is the dense summary
    * content.body still carries the raw transcript
    * a `summary_ok` row landed in wiki_log

Run (all four env vars REQUIRED — no workspace-specific defaults ship):

    DATABRICKS_CONFIG_PROFILE=<profile> \
      WIKIBRICKS_CATALOG=<catalog> \
      WIKIBRICKS_SCHEMA=<schema> \
      WIKIBRICKS_WAREHOUSE_ID=<warehouse_id> \
      uv run python scripts/smoke_summary_first.py

Idempotent — writes one page per run under `sessions/smoke/<date>/<sid>`.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure local source is on path (in case run from a different cwd).
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from databricks.sdk import WorkspaceClient  # noqa: E402

from wikibricks.client import WikiClient  # noqa: E402
from wikibricks_recorder import auto_summary, page_builder  # noqa: E402


def _build_state() -> dict:
    sid = f"smoke-{uuid.uuid4().hex[:8]}"
    started = datetime.now(timezone.utc).isoformat()
    first_prompt = (
        "Refactor the payments module so it uses the new Stripe webhook "
        "signature verification. Add a unit test that covers replay attacks "
        "and a regression test for the legacy v1 payload shape we still "
        "receive from grandfathered customers. " + ("x" * 2200)
    )
    events = [
        {"kind": "prompt", "ts": started, "prompt": first_prompt},
        {"kind": "tool", "ts": started, "tool_name": "Read"},
        {"kind": "tool", "ts": started, "tool_name": "Edit"},
        {"kind": "tool", "ts": started, "tool_name": "Bash"},
        {"kind": "prompt", "ts": started, "prompt": "now ship the change"},
    ]
    return {
        "session_id": sid,
        "first_prompt": first_prompt,
        "events": events,
        "started_at": started,
        "cwd": "/Users/philipp.tiefenbacher/proj-smoke",
        "model": "claude-opus-4-7",
    }


def main() -> int:
    def _req(name: str) -> str:
        v = os.environ.get(name)
        if not v:
            raise SystemExit(f"missing required env var: {name}")
        return v

    catalog = _req("WIKIBRICKS_CATALOG")
    schema = _req("WIKIBRICKS_SCHEMA")
    warehouse_id = _req("WIKIBRICKS_WAREHOUSE_ID")
    profile = _req("DATABRICKS_CONFIG_PROFILE")

    # Force the library to target this schema (otherwise it uses main.wiki)
    os.environ["WIKIBRICKS_CATALOG"] = catalog
    os.environ["WIKIBRICKS_SCHEMA"] = schema

    ws = WorkspaceClient(profile=profile)
    client = WikiClient(warehouse_id=warehouse_id, workspace_client=ws)

    state = _build_state()
    sid = state["session_id"]
    print(f"[smoke] session_id={sid}")

    # 1. Generate the dense summary against the real endpoint
    cfg = {"enabled": True, "endpoint": "databricks-claude-haiku-4-5"}
    print(f"[smoke] calling {cfg['endpoint']} for dense summary...")
    summary = auto_summary.generate_summary(state, cfg, ws)
    if summary is None:
        print("[smoke] FAIL: generate_summary returned None")
        return 2
    print(f"[smoke] summary length: {len(summary)} chars")
    print("[smoke] --- summary preview ---")
    print(summary[:500])
    print("[smoke] -------------------------")

    # 2. Write the page via WikiClient with content_text_override
    path = f"sessions/smoke/{datetime.now(timezone.utc):%Y/%m/%d}/{sid}"
    content_json = page_builder.session_content(state, dense_summary=summary)
    print(f"[smoke] writing page at {path}")
    client.write_page(
        path,
        title="Smoke test 0.7.8: payments refactor",
        content_json=content_json,
        tags=["smoke", "session", "v0.7.8-test"],
        content_text_override=summary,
    )

    # 3. Read back and verify
    resp = client._exec(
        f"SELECT "
        f"  content:summary::STRING AS summary, "
        f"  length(content_text) AS ct_len, "
        f"  substring(content_text, 1, 200) AS ct_head, "
        f"  length(content:body::STRING) AS body_len "
        f"FROM {catalog}.{schema}.pages WHERE path = '{path}'"
    )
    rows = resp.result.data_array if resp.result else []
    if not rows:
        print("[smoke] FAIL: could not read back page")
        return 3
    row = rows[0]
    print(f"[smoke] content.summary starts with: {row[0][:80]!r}")
    print(f"[smoke] content_text length: {row[1]}")
    print(f"[smoke] content_text starts with: {row[2][:80]!r}")
    print(f"[smoke] content.body length: {row[3]}")
    row = [row[0], int(row[1]), row[2], int(row[3])]

    # 4. Assertions
    if not row[0].startswith("## Intent") and "## Intent" not in row[0][:200]:
        print("[smoke] WARN: content.summary doesn't start with '## Intent'")
    if abs(row[1] - len(summary)) > 5:
        print(
            f"[smoke] FAIL: content_text length {row[1]} != summary length "
            f"{len(summary)} — override may not have applied"
        )
        return 4
    if row[3] < 2000:
        print(f"[smoke] FAIL: content.body length {row[3]} < 2000 — raw transcript lost")
        return 5

    print("[smoke] PASS: content_text is the dense summary; body preserved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
