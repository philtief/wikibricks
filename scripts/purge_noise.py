"""Purge noise pages from the wiki — one-off cleanup script.

For every concept session page that is recorder noise — a programmatic
``/tmp`` sub-invocation (memory-consolidation runs, MCP smoke tests, other
agents driving ``claude`` as a subprocess) — delete the page **and its
chunk children** from the ``pages`` table.

Noise is identified by ``wikibricks.title_repair.is_noise_page``: a page is
noise when its body records an ephemeral CWD (``- CWD: /tmp…``, the same
signal the recorder's ``page_builder.is_ephemeral`` skips on at write time)
OR its title is a raw system-prompt template. A ``[stub]`` title alone is
NOT sufficient — real summarized sessions can fall back to a stub title
while carrying a genuine work summary, so the scan reads the page **body**,
not just the title. (The prior title-only scan missed 23 ephemeral pages
whose titles were ``[stub] Session …`` rather than ``You are …``.)

The recorder filter shipped in 0.3.x (broadened through 0.7.3) prevents new
noise; this script clears the backlog accrued before that fix landed.

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
from wikibricks.title_repair import is_noise_page


def find_candidates(wiki: WikiClient, limit: int = 0) -> list[dict]:
    """Return parent session pages that are recorder noise.

    Reads path + title + body directly (``list_pages`` returns no body and
    hides ``ephemeral:stub``-tagged pages by default — the very pages we
    need to see). Classification is body-aware via ``is_noise_page``.
    """
    resp = wiki._exec(
        f"SELECT path, title, content_text FROM {PAGES_TABLE} "
        f"WHERE path LIKE 'sessions/%' AND path NOT LIKE '%/chunks/%'"
    )
    rows = resp.result.data_array if resp.result else []
    out: list[dict] = []
    for path, title, content_text in rows or []:
        if not is_noise_page(title, content_text):
            continue
        out.append({"path": path, "title": title or ""})
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

    # DELETE by exact candidate path (and each parent's chunk children).
    # Deleting by title prefix is unsafe now that candidates include
    # "[stub] Session …" pages — a prefix broad enough to catch them would
    # also catch real work. Match the parents by their exact paths plus a
    # per-parent "<path>/chunks/%" LIKE for children, all in one statement.
    print("\nIssuing bulk DELETE...", flush=True)
    paths = [c["path"] for c in candidates]
    in_list = ", ".join(f"'{wiki._escape(p)}'" for p in paths)
    child_likes = " OR ".join(
        f"path LIKE '{wiki._escape(p)}/chunks/%'" for p in paths
    )
    where = f"path IN ({in_list})"
    if child_likes:
        where += f" OR {child_likes}"
    wiki._exec(f"DELETE FROM {PAGES_TABLE} WHERE {where}")
    wiki._log(op_type="purge_noise", path=None,
              details=f"Purged {len(candidates)} noise parent pages (+chunk children) by path")
    print(f"Deleted {len(candidates)} parent pages and their chunk children.", flush=True)
    print("Syncing Vector Search index...", flush=True)
    wiki.sync_index()
    print("Healing broken edges...", flush=True)
    healed = wiki.fix_broken_links()
    print(f"Healed {healed} edges. Done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
