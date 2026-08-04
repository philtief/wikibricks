"""Sync Omnigent's local session store into WikiBricks.

Reads Omnigent's own conversation store (`~/.omnigent/chat.db`) **read-only**
and writes each real conversation as a WikiBricks page via `WikiClient`. Omnigent
is never modified — this is the harness-agnostic counterpart to the Claude Code
recorder plugin, for the (many) Omnigent sessions that Claude Code's SessionStart
hooks never see.

Trigger: run manually, or schedule locally (cron / launchd). It is NOT an
Omnigent scheduled task — that MCP endpoint is not served by every daemon build
and its store is a remote managed server, so a local reader is the portable path.

Run::

    uv run python scripts/omnigent_sync_cli.py \\
        --profile <databricks-profile> \\
        --catalog <c> --schema <s> --warehouse-id <wh> \\
        --dry-run                    # list what would sync, no writes

    uv run python scripts/omnigent_sync_cli.py \\
        --profile fe-vm-agent-marketplace \\
        --catalog agent_marketplace_catalog \\
        --schema wikibricks_personal_philipp \\
        --warehouse-id <wh> \\
        --since-days 7 --sync-index
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from wikibricks_recorder.omnigent_sync import (  # noqa: E402
    conversation_page,
    is_syncable,
)

DEFAULT_DB = Path.home() / ".omnigent" / "chat.db"


def _open_ro(db_path: Path) -> sqlite3.Connection:
    """Open chat.db strictly read-only so we can never mutate Omnigent state."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_conversations(
    con: sqlite3.Connection, *, since_epoch: int | None = None, limit: int = 0
) -> list[dict[str, Any]]:
    """Assemble conversation dicts (metadata + ordered items) from chat.db.

    Joins ``conversations`` → ``agents`` for the bound agent name, then pulls
    each conversation's ``conversation_items`` in position order. Only
    non-archived conversations are considered; ``is_syncable`` does the finer
    filtering downstream.
    """
    where = ["c.archived = 0"]
    params: list[Any] = []
    if since_epoch:
        where.append("c.updated_at >= ?")
        params.append(since_epoch)
    sql = (
        "SELECT lower(hex(c.id)) AS cid, c.title, c.created_at, c.updated_at, "
        "       c.workspace_id, a.name AS agent_name "
        "FROM conversations c "
        "LEFT JOIN agents a ON a.id = c.agent_id AND a.workspace_id = c.workspace_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY c.updated_at DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = con.execute(sql, params).fetchall()

    convs: list[dict[str, Any]] = []
    for r in rows:
        item_rows = con.execute(
            "SELECT type, data FROM conversation_items "
            "WHERE conversation_id = ? AND workspace_id = ? "
            "ORDER BY position ASC",
            (bytes.fromhex(r["cid"]), r["workspace_id"]),
        ).fetchall()
        items: list[tuple[int, dict]] = []
        for ir in item_rows:
            try:
                items.append((int(ir["type"]), json.loads(ir["data"])))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue  # skip an unparseable row, never crash the sync
        convs.append({
            "conversation_id": r["cid"],
            "title": r["title"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "agent_name": r["agent_name"],
            "workspace": None,  # not stored per-conversation in chat.db
            "archived": False,
            "items": items,
        })
    return convs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--user-id", default="philipp.tiefenbacher-at-databricks.com",
                   help="wiki user_id partition; defaults to the personal wiki user")
    p.add_argument("--db", default=str(DEFAULT_DB), help="path to Omnigent chat.db")
    p.add_argument("--since-days", type=int, default=0,
                   help="only sync conversations updated in the last N days (0 = all)")
    p.add_argument("--limit", type=int, default=0, help="cap conversations scanned")
    p.add_argument("--sync-index", action="store_true",
                   help="trigger a VS index sync after writing")
    p.add_argument("--dry-run", action="store_true",
                   help="list syncable conversations; do not write")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Omnigent store not found: {db_path}", file=sys.stderr)
        return 1

    since_epoch = int(time.time() - args.since_days * 86400) if args.since_days else None
    con = _open_ro(db_path)
    try:
        conversations = load_conversations(con, since_epoch=since_epoch, limit=args.limit)
    finally:
        con.close()

    syncable = [c for c in conversations if is_syncable(c)]
    print(f"Scanned {len(conversations)} conversations; {len(syncable)} syncable.")

    pages = [conversation_page(c, user_id=args.user_id) for c in syncable]
    for pg in pages[:10]:
        print(f"  {pg['path']}\n      {pg['title'][:80]}")
    if len(pages) > 10:
        print(f"  ... and {len(pages) - 10} more")

    if args.dry_run:
        print(f"\n[dry-run] {len(pages)} pages would be written. No changes made.")
        return 0
    if not pages:
        return 0

    os.environ["WIKIBRICKS_CATALOG"] = args.catalog
    os.environ["WIKIBRICKS_SCHEMA"] = args.schema
    from databricks.sdk import WorkspaceClient

    from wikibricks import WikiClient
    ws = WorkspaceClient(profile=args.profile)
    wiki = WikiClient(warehouse_id=args.warehouse_id, workspace_client=ws)

    written = 0
    for pg in pages:
        try:
            wiki.write_page(
                pg["path"], pg["title"], pg["content"],
                created_by="omnigent-sync", tags=pg["tags"],
                content_text_override=pg["content_text_override"],
            )
            written += 1
        except Exception as e:  # noqa: BLE001 — one bad page must not abort the run
            print(f"  skip {pg['path']}: {e}", file=sys.stderr)

    print(f"\nWrote {written} Omnigent session pages.")
    if args.sync_index:
        wiki.sync_index()
        print("Triggered VS index sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
