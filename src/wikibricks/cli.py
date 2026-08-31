"""Command-line interface for local WikiBricks and explicit remote sync."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from wikibricks.adapters.jsonl import iter_jsonl_sessions
from wikibricks.adapters.omnigent import (
    conversation_to_session,
    is_syncable_conversation,
    load_conversations,
)
from wikibricks.postgres_store import PostgresStore

DEFAULT_OMNIGENT_DB = Path.home() / ".omnigent" / "chat.db"


def import_omnigent(
    *,
    database_url: str | None,
    db_path: Path,
    user_id: str,
    since_days: int = 0,
    limit: int = 0,
) -> dict[str, int]:
    if not db_path.exists():
        raise FileNotFoundError(f"Omnigent store not found: {db_path}")
    store = PostgresStore(database_url)
    store.migrate()
    target = f"omnigent:{db_path.resolve()}"
    saved = store.get_sync_cursor(target)
    cursor = None
    if "updated_at" in saved and "conversation_id" in saved:
        cursor = (int(saved["updated_at"]), str(saved["conversation_id"]))
    since_epoch = int(time.time() - since_days * 86400) if since_days else None
    conversations = load_conversations(
        db_path,
        cursor=cursor,
        since_epoch=since_epoch,
        limit=limit,
    )
    result = {"scanned": len(conversations), "imported": 0, "skipped": 0, "errors": 0}
    last_cursor = cursor
    for conversation in conversations:
        try:
            if is_syncable_conversation(conversation):
                record = conversation_to_session(conversation, user_id=user_id)
                store.ingest_session(record)
                result["imported"] += 1
            else:
                result["skipped"] += 1
            last_cursor = (
                int(conversation.get("updated_at") or 0),
                str(conversation["conversation_id"]),
            )
        except Exception:
            result["errors"] += 1
            break
    if last_cursor is not None:
        store.set_sync_cursor(
            target,
            {"updated_at": last_cursor[0], "conversation_id": last_cursor[1]},
        )
    return result


def import_jsonl(
    *,
    database_url: str | None,
    source: Path,
) -> dict[str, int]:
    store = PostgresStore(database_url)
    store.migrate()
    result = {"scanned": 0, "imported": 0, "errors": 0}
    with source.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            result["scanned"] += 1
            try:
                record = next(iter_jsonl_sessions([line]))
                store.ingest_session(record)
                result["imported"] += 1
            except Exception as exc:
                result["errors"] += 1
                print(f"line {result['scanned']}: {exc}", file=sys.stderr)
    return result


def _print_json(value: Any) -> None:
    print(json.dumps(value, default=str, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wikibricks")
    parser.add_argument("--database-url", help="PostgreSQL connection URL")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Apply local PostgreSQL migrations")
    init.set_defaults(handler=_command_init)

    search = commands.add_parser("search", help="Search local memory")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5)
    search.set_defaults(handler=_command_search)

    check = commands.add_parser("check", help="Validate local database invariants")
    check.set_defaults(handler=_command_check)

    curate = commands.add_parser("curate", help="Run deterministic local curation")
    curate.add_argument(
        "--prune-archived-sessions-after-days",
        type=int,
        help="Delete old sessions only when every event version is archived",
    )
    curate.set_defaults(handler=_command_curate)

    backup = commands.add_parser("backup", help="Create a pg_dump backup")
    backup.add_argument("output", type=Path)
    backup.set_defaults(handler=_command_backup)

    restore = commands.add_parser("restore", help="Restore into a new database")
    restore.add_argument("source", type=Path)
    restore.set_defaults(handler=_command_restore)

    vacuum = commands.add_parser("vacuum", help="Vacuum and analyze local memory")
    vacuum.set_defaults(handler=_command_vacuum)

    importer = commands.add_parser("import", help="Import harness sessions")
    formats = importer.add_subparsers(dest="format", required=True)
    omnigent = formats.add_parser("omnigent")
    omnigent.add_argument("--db", type=Path, default=DEFAULT_OMNIGENT_DB)
    omnigent.add_argument("--user-id", required=True)
    omnigent.add_argument("--since-days", type=int, default=0)
    omnigent.add_argument("--limit", type=int, default=0)
    omnigent.set_defaults(handler=_command_import_omnigent)
    jsonl = formats.add_parser("jsonl")
    jsonl.add_argument("source", type=Path)
    jsonl.set_defaults(handler=_command_import_jsonl)

    sync = commands.add_parser("sync", help="Explicit remote archival")
    sync_targets = sync.add_subparsers(dest="sync_target", required=True)
    lakebase = sync_targets.add_parser("lakebase")
    lakebase.add_argument("--profile", required=True)
    lakebase.add_argument("--project", required=True)
    lakebase.add_argument("--branch", default="production")
    lakebase.add_argument("--endpoint", default="primary")
    lakebase.add_argument("--database", default="wikibricks")
    lakebase.add_argument("--limit", type=int, default=1000)
    lakebase.add_argument("--pull-curated", action="store_true")
    lakebase.add_argument(
        "--pull-patches",
        action="store_true",
        help="Download immutable remote curation manifests without applying them",
    )
    lakebase.set_defaults(handler=_command_sync_lakebase)

    replica = sync_targets.add_parser("replica", help="Show the stable local replica ID")
    replica.set_defaults(handler=_command_sync_replica)

    plan = sync_targets.add_parser("plan", help="Plan a downloaded curation run locally")
    plan.add_argument("run_id", type=UUID)
    plan.add_argument("--policy", choices=("safe", "all"), default="safe")
    plan.set_defaults(handler=_command_sync_plan)

    apply = sync_targets.add_parser("apply", help="Apply a downloaded curation run locally")
    apply.add_argument("run_id", type=UUID)
    apply.add_argument("--policy", choices=("safe", "all"), default="safe")
    apply.set_defaults(handler=_command_sync_apply)

    conflicts = sync_targets.add_parser("conflicts", help="List unresolved local curation conflicts")
    conflicts.set_defaults(handler=_command_sync_conflicts)

    resolve = sync_targets.add_parser("resolve", help="Resolve one local curation conflict group")
    resolve.add_argument("run_id", type=UUID)
    resolve.add_argument("group_id", type=UUID)
    resolve.add_argument(
        "--action",
        required=True,
        choices=("keep_local", "accept_remote", "merged", "defer"),
    )
    resolve.set_defaults(handler=_command_sync_resolve)
    return parser


def _command_init(args: argparse.Namespace) -> int:
    from wikibricks.maintenance import initialize_database

    database_url = args.database_url or PostgresStore().database_url
    initialize_database(database_url)
    print("WikiBricks PostgreSQL schema is ready.")
    return 0


def _command_search(args: argparse.Namespace) -> int:
    store = PostgresStore(args.database_url)
    store.migrate()
    _print_json(store.search(args.query, num_results=args.k))
    return 0


def _command_check(args: argparse.Namespace) -> int:
    from wikibricks.maintenance import check_database

    database_url = args.database_url or PostgresStore().database_url
    result = check_database(database_url)
    _print_json(result)
    return 0 if result["ok"] else 1


def _command_curate(args: argparse.Namespace) -> int:
    from wikibricks.maintenance import curate_database

    database_url = args.database_url or PostgresStore().database_url
    result = curate_database(
        database_url,
        prune_archived_sessions_after_days=args.prune_archived_sessions_after_days,
    )
    _print_json(result)
    return 0


def _command_backup(args: argparse.Namespace) -> int:
    from wikibricks.maintenance import backup_database

    database_url = args.database_url or PostgresStore().database_url
    backup_database(database_url, args.output)
    print(args.output)
    return 0


def _command_restore(args: argparse.Namespace) -> int:
    from wikibricks.maintenance import restore_database

    database_url = args.database_url or PostgresStore().database_url
    restore_database(args.source, database_url)
    print("WikiBricks backup restored and ready.")
    return 0


def _command_vacuum(args: argparse.Namespace) -> int:
    from wikibricks.maintenance import vacuum_database

    database_url = args.database_url or PostgresStore().database_url
    vacuum_database(database_url)
    print("WikiBricks PostgreSQL vacuum complete.")
    return 0


def _command_import_omnigent(args: argparse.Namespace) -> int:
    _print_json(
        import_omnigent(
            database_url=args.database_url,
            db_path=args.db,
            user_id=args.user_id,
            since_days=args.since_days,
            limit=args.limit,
        )
    )
    return 0


def _command_import_jsonl(args: argparse.Namespace) -> int:
    result = import_jsonl(database_url=args.database_url, source=args.source)
    _print_json(result)
    return 1 if result["errors"] else 0


def _command_sync_lakebase(args: argparse.Namespace) -> int:
    from wikibricks_databricks.lakebase_sync import (
        LakebaseTarget,
        pull_curated_snapshot,
        pull_curation_patches,
        sync_to_archive,
    )

    local = PostgresStore(args.database_url)
    local.migrate()
    target = LakebaseTarget(
        project=args.project,
        branch=args.branch,
        endpoint=args.endpoint,
        database=args.database,
        profile=args.profile,
    )
    remote_url = target.fresh_database_url()
    result = sync_to_archive(local, remote_url, limit=args.limit)
    if args.pull_curated:
        remote_url = target.fresh_database_url()
        result["curated_pages_imported"] = pull_curated_snapshot(local, remote_url)
    if args.pull_patches:
        remote_url = target.fresh_database_url()
        result["curation"] = pull_curation_patches(local, remote_url)
    _print_json(result)
    return 0


def _command_sync_replica(args: argparse.Namespace) -> int:
    from wikibricks.curation_sync import get_or_create_replica_id

    store = PostgresStore(args.database_url)
    _print_json({"replica_id": str(get_or_create_replica_id(store))})
    return 0


def _command_sync_plan(args: argparse.Namespace) -> int:
    from wikibricks.curation_sync import plan_run

    store = PostgresStore(args.database_url)
    _print_json(plan_run(store, args.run_id, policy=args.policy))
    return 0


def _command_sync_apply(args: argparse.Namespace) -> int:
    from wikibricks.curation_sync import apply_run

    store = PostgresStore(args.database_url)
    _print_json(apply_run(store, args.run_id, policy=args.policy))
    return 0


def _command_sync_conflicts(args: argparse.Namespace) -> int:
    from wikibricks.curation_sync import list_conflicts

    store = PostgresStore(args.database_url)
    _print_json(list_conflicts(store))
    return 0


def _command_sync_resolve(args: argparse.Namespace) -> int:
    from wikibricks.curation_sync import resolve_conflict

    store = PostgresStore(args.database_url)
    _print_json(
        resolve_conflict(
            store,
            args.run_id,
            args.group_id,
            action=args.action,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


def init_main() -> int:
    return main([*sys.argv[1:], "init"])


if __name__ == "__main__":
    raise SystemExit(main())
