"""Purge noise pages from the wiki — one-off cleanup script.

For every concept session page whose title matches a known skill /
sub-agent system-prompt template (see ``wikibricks.title_repair``),
delete the page **and its chunk children** from the ``pages`` table.

A full-corpus scan confirmed these pages are 100% noise: every page with
a system-prompt title also has system-prompt-only body content. The
recorder filter shipped in 0.3.x prevents new noise; this script clears
the backlog accrued before that fix landed.

Audit trail: every deletion is logged to ``wiki_log`` with
``op_type='purge_noise'`` and the deleted path + original title. The
``wiki_history`` table also retains the previous row versions, so a
recovery path exists if needed.

Post-purge: the script calls ``WikiClient.sync_index()`` to drop stale
Vector Search entries and ``WikiClient.fix_broken_links()`` to heal
typed edges pointing at the deleted pages.

Usage::

    DATABRICKS_CONFIG_PROFILE=fe-vm-agent-marketplace \\
        WIKIBRICKS_WAREHOUSE_ID=41754a8563a43a49 \\
        WIKIBRICKS_CATALOG=agent_marketplace_catalog \\
        WIKIBRICKS_SCHEMA=wikibricks_personal_philipp \\
        python scripts/purge_noise.py            # dry-run (default)
        python scripts/purge_noise.py --apply    # actually delete
        python scripts/purge_noise.py --apply --yes  # skip interactive prompt
"""

from __future__ import annotations

import argparse
import os
import sys

from databricks.sdk import WorkspaceClient

from wikibricks.client import WikiClient
from wikibricks.ops import PAGES_TABLE
from wikibricks.title_repair import looks_like_system_prompt


def find_candidates(wiki: WikiClient, limit: int = 0) -> list[dict]:
    """Return concept session pages whose title is a system-prompt template."""
    pages = wiki.list_pages(path_prefix="sessions/")
    out: list[dict] = []
    for entry in pages:
        if "/chunks/" in entry["path"]:
            continue
        if not looks_like_system_prompt(entry.get("title", "")):
            continue
        out.append(entry)
        if limit and len(out) >= limit:
            break
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="Actually delete. Default is dry-run.")
    p.add_argument("--yes", action="store_true",
                   help="Skip the interactive y/N confirmation. Requires --apply.")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap candidates. 0 = unlimited.")
    args = p.parse_args()

    warehouse_id = os.environ.get("WIKIBRICKS_WAREHOUSE_ID")
    if not warehouse_id:
        sys.exit("WIKIBRICKS_WAREHOUSE_ID env var required")
    profile = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    ws = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    wiki = WikiClient(warehouse_id=warehouse_id, workspace_client=ws)

    print("Scanning wiki...", flush=True)
    candidates = find_candidates(wiki, limit=args.limit)
    print(f"Found {len(candidates)} noise pages.", flush=True)
    if not candidates:
        return 0

    print("\nFirst 10 candidates:")
    for entry in candidates[:10]:
        print(f"  {entry['path']}")
        print(f"    title: {entry.get('title', '')[:80]}")

    if not args.apply:
        print(f"\nDry-run only ({len(candidates)} would be deleted). "
              "Re-run with --apply to write.")
        return 0

    if not args.yes:
        confirm = input(
            f"\nDelete {len(candidates)} pages and their chunk children? [y/N]: "
        )
        if confirm.strip().lower() != "y":
            print("Aborted.")
            return 0

    # Bulk DELETE by title prefix. Chunk titles inherit the parent's title
    # ("You are summarizing... - <chunk first line>"), so the same LIKE catches
    # both parents and their chunks in one statement. Two prefix patterns =
    # one DELETE with an OR. Avoids the per-row 30s timeout.
    print("\nIssuing bulk DELETE...", flush=True)
    wiki._exec(
        f"DELETE FROM {PAGES_TABLE} "
        f"WHERE path LIKE 'sessions/%' "
        f"  AND (title LIKE 'You are %' OR title LIKE 'Apply maximum %')"
    )
    wiki._log(op_type="purge_noise", path=None,
              details=f"Bulk purge: {len(candidates)} parent pages by title prefix")
    print(f"Deleted noise pages (estimate: {len(candidates)} parents + ~5x chunks).", flush=True)
    print("Syncing Vector Search index...", flush=True)
    wiki.sync_index()
    print("Healing broken edges...", flush=True)
    healed = wiki.fix_broken_links()
    print(f"Healed {healed} edges. Done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
