"""Re-title session pages whose `title` is leaked LLM system-prompt boilerplate.

Earlier recorder versions (≤ 0.7.2) picked the first line of `first_prompt`
as the page title. When prompts opened with summarizer scaffolding like
`"You are summarizing a Claude Code session..."` or `"Apply maximum
compression. Rules:"`, that scaffolding became the title — making every
such page indistinguishable in any list view.

v0.7.3 fixes the recorder forward. This script fixes the historical pages.

Strategy:
1. Find pages whose `title` matches the boilerplate-leak shape.
2. For each, parse the markdown body for the first informative line and
   use it as the new title. Body has a fixed shape (set by
   `page_builder.session_content`): a `# Session <sid>` heading, metadata
   bullets, then `## Timeline` followed by `### prompt @ <ts>\n> <text>`
   blocks. We pull the first non-boilerplate line of the first prompt.
3. UPDATE the page in place via `wiki.write_page` (MERGE preserves tags).

Run::

    uv run python scripts/backfill_recorder_titles.py \\
        --profile <databricks-profile> \\
        --catalog <c> --schema <s> --warehouse-id <wh> \\
        --dry-run                    # report counts only, no writes
    uv run python scripts/backfill_recorder_titles.py \\
        --profile <databricks-profile> \\
        --catalog <c> --schema <s> --warehouse-id <wh>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from wikibricks_recorder.page_builder import _is_boilerplate  # type: ignore[attr-defined]


def _extract_first_user_prompt(body: str) -> str | None:
    """Return the text of the first `### prompt @ ...` block in the body."""
    lines = body.splitlines()
    in_prompt = False
    captured: list[str] = []
    for line in lines:
        if line.startswith("### prompt @"):
            in_prompt = True
            continue
        if in_prompt:
            if line.startswith("###"):
                break
            if line.startswith("> "):
                captured.append(line[2:])
            elif line.strip() == "":
                continue
            else:
                captured.append(line)
    if not captured:
        return None
    return "\n".join(captured).strip()


def _better_title(body: str, session_id: str) -> str:
    """Pick a real title from a page body.

    Two body shapes show up in practice:

    1. Curate/segregate-processed pages: body opens with a free-form
       summary paragraph (often the very thing we want as a title), then
       `## Contents` listing chunk links. First non-boilerplate line of
       the summary wins.
    2. Raw recorder bodies: `# Session <sid>` header, metadata bullets,
       `## Timeline` then `### prompt @ <ts>\\n> <text>` blocks. We
       extract the first prompt and apply the boilerplate filter to it.

    Fall back to `Session <short-id>` if neither yields content.
    """
    # Shape 1: first informative line of the body (skip Markdown headers
    # and the `# Session <sid>` template line).
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-") or s.startswith("["):
            continue
        if _is_boilerplate(s):
            continue
        return s[:120]

    # Shape 2: parse the prompt block.
    prompt = _extract_first_user_prompt(body)
    if prompt:
        for line in prompt.splitlines():
            if not _is_boilerplate(line):
                return line.strip()[:120]

    return f"Session {session_id[:8]}"


def _parse_body_field(content_str: str | None) -> str:
    """`content` is a VARIANT serialised to a JSON string in the result row."""
    if not content_str:
        return ""
    try:
        return json.loads(content_str).get("body", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="report changes; do not call write_page")
    p.add_argument("--limit", type=int, default=10_000)
    args = p.parse_args()

    os.environ["WIKIBRICKS_CATALOG"] = args.catalog
    os.environ["WIKIBRICKS_SCHEMA"] = args.schema

    from databricks.sdk import WorkspaceClient

    from wikibricks import WikiClient

    ws = WorkspaceClient(profile=args.profile)
    wiki = WikiClient(warehouse_id=args.warehouse_id, workspace_client=ws)

    def run_sql(sql: str) -> list[list[Any]]:
        r = ws.statement_execution.execute_statement(
            warehouse_id=args.warehouse_id, statement=sql, wait_timeout="50s"
        )
        sid = r.statement_id
        deadline = time.time() + 180
        while r.status and r.status.state.value in ("PENDING", "RUNNING") and time.time() < deadline:
            time.sleep(2)
            r = ws.statement_execution.get_statement(sid)
        return (r.result.data_array if r.result else None) or []

    # Find candidates — title looks like instruction boilerplate, page is a
    # top-level session (not a chunk).
    table = f"{args.catalog}.{args.schema}.pages"
    # Strip leading "> " (Markdown quote prefix that occasionally precedes
    # the boilerplate) before pattern-matching, so all variants are caught.
    rows = run_sql(f"""
        SELECT page_id, path, title, CAST(content AS STRING) AS body_json
        FROM {table}
        WHERE path LIKE 'sessions/%'
          AND path NOT LIKE '%/chunks/%'
          AND (
            CASE
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'you are %' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'apply %compression%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'apply %rules%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'apply maximum %' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'summari%e %' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'please summari%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'read the conversation%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'read the transcript%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'read the session%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'write % memory %' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'write one memory %' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'extract % from the conversation%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'generate % summary%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) LIKE 'output % summary%' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) = 'rules:' THEN 1
              WHEN lower(trim(BOTH '> ' FROM title)) = 'instructions:' THEN 1
              ELSE 0
            END = 1
          )
        LIMIT {args.limit}
    """)

    print(f"Found {len(rows)} candidate pages with boilerplate-leak titles")
    changes: list[tuple[str, str, str]] = []  # (path, old, new)
    for _pid, path, old_title, body_json in rows:
        body = _parse_body_field(body_json)
        sid = path.rsplit("/", 1)[-1]
        new_title = _better_title(body, sid)
        if new_title != old_title:
            changes.append((path, old_title or "", new_title))

    print(f"Will re-title {len(changes)} pages (skipped {len(rows) - len(changes)} unchanged)")
    for path, old, new in changes[:10]:
        print(f"  {path}")
        print(f"      old: {old[:80]!r}")
        print(f"      new: {new[:80]!r}")
    if len(changes) > 10:
        print(f"  ... and {len(changes) - 10} more")

    if args.dry_run:
        print("\n[dry-run] no writes performed")
        return 0

    if not changes:
        return 0

    # Direct SQL UPDATE on the title column. write_page would re-MERGE the
    # whole row and risk clobbering tags or page_type — single-column update
    # is safer for a title-only backfill.
    _ = wiki  # WikiClient unused on this path; we go through the SQL API directly
    fixed = 0
    for path, _old, new_title in changes:
        safe_title = new_title.replace("'", "''")
        safe_path = path.replace("'", "''")
        try:
            run_sql(
                f"UPDATE {table} SET title = '{safe_title}' "
                f"WHERE path = '{safe_path}'"
            )
            fixed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  skip {path}: update failed ({e})", file=sys.stderr)

    print(f"\nRe-titled {fixed} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
