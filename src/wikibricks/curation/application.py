"""Transactional application and conflict resolution for curation patches."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from wikibricks.curation.planning import grouped, preflight_group
from wikibricks.curation.repository import (
    insert_receipt,
    mark_run_applied,
    page_state,
    patches_for_run,
    receipt_count,
    target_page,
)
from wikibricks.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from psycopg import Connection

    from wikibricks.postgres_store import PostgresStore

_SYNC_LOCK_KEY = "wikibricks:curation-sync"


@contextmanager
def _write_connection(store: PostgresStore | SQLiteStore):
    if isinstance(store, SQLiteStore):
        with store.connection(write=True) as conn:
            yield conn
        return
    with store.connection() as conn, conn.transaction():
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (_SYNC_LOCK_KEY,),
        )
        yield conn


def _retarget_links(
    conn: Connection,
    source: dict[str, Any],
    target: dict[str, Any],
) -> None:
    if isinstance(conn, sqlite3.Connection):
        source_id = source["page_id"]
        target_id = target["page_id"]
        incoming = conn.execute(
            "SELECT link_id, source_page_id, link_type, origin, metadata "
            "FROM links WHERE target_page_id = ?",
            (source_id,),
        ).fetchall()
        for link_id, from_id, link_type, origin, metadata in incoming:
            if from_id != target_id:
                conn.execute(
                    "INSERT INTO links "
                    "(link_id, source_page_id, target_page_id, link_type, origin, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source_page_id, target_page_id, link_type) DO NOTHING",
                    (
                        str(uuid4()),
                        from_id,
                        target_id,
                        link_type,
                        origin,
                        metadata,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            conn.execute("DELETE FROM links WHERE link_id = ?", (link_id,))
        outgoing = conn.execute(
            "SELECT link_id, target_page_id, link_type, origin, metadata "
            "FROM links WHERE source_page_id = ?",
            (source_id,),
        ).fetchall()
        for link_id, to_id, link_type, origin, metadata in outgoing:
            if to_id not in {source_id, target_id}:
                conn.execute(
                    "INSERT INTO links "
                    "(link_id, source_page_id, target_page_id, link_type, origin, metadata, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source_page_id, target_page_id, link_type) DO NOTHING",
                    (
                        str(uuid4()),
                        target_id,
                        to_id,
                        link_type,
                        origin,
                        metadata,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            conn.execute("DELETE FROM links WHERE link_id = ?", (link_id,))
        return

    from psycopg.types.json import Jsonb

    source_id = UUID(source["page_id"])
    target_id = UUID(target["page_id"])
    incoming = conn.execute(
        "SELECT link_id, source_page_id, link_type, origin, metadata "
        "FROM links WHERE target_page_id = %s",
        (source_id,),
    ).fetchall()
    for link_id, from_id, link_type, origin, metadata in incoming:
        if from_id != target_id:
            conn.execute(
                "INSERT INTO links "
                "(link_id, source_page_id, target_page_id, link_type, origin, "
                "metadata) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (source_page_id, target_page_id, link_type) "
                "DO NOTHING",
                (
                    uuid4(),
                    from_id,
                    target_id,
                    link_type,
                    origin,
                    Jsonb(metadata),
                ),
            )
        conn.execute("DELETE FROM links WHERE link_id = %s", (link_id,))
    outgoing = conn.execute(
        "SELECT link_id, target_page_id, link_type, origin, metadata "
        "FROM links WHERE source_page_id = %s",
        (source_id,),
    ).fetchall()
    for link_id, to_id, link_type, origin, metadata in outgoing:
        if to_id not in {source_id, target_id}:
            conn.execute(
                "INSERT INTO links "
                "(link_id, source_page_id, target_page_id, link_type, origin, "
                "metadata) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (source_page_id, target_page_id, link_type) "
                "DO NOTHING",
                (
                    uuid4(),
                    target_id,
                    to_id,
                    link_type,
                    origin,
                    Jsonb(metadata),
                ),
            )
        conn.execute("DELETE FROM links WHERE link_id = %s", (link_id,))


def _apply_patch(
    store: PostgresStore | SQLiteStore,
    conn: Connection,
    patch: dict[str, Any],
    *,
    force: bool,
) -> UUID | None:
    operation = patch["operation"]
    proposal = patch["proposal"]
    patch_id = UUID(patch["patch_id"])
    if operation in {"create_page", "update_page"}:
        if isinstance(store, SQLiteStore):
            expected = None if operation == "create_page" else patch["base_content_hash"]
            if force:
                _message, version_id = store.write_page_in_connection(
                    conn,
                    patch["path"],
                    proposal["title"],
                    proposal["content"],
                    page_type=proposal["page_type"],
                    created_by="remote-curator",
                    tags=proposal["tags"],
                    source_ids=proposal["source_ids"],
                    parent_id=proposal["parent_id"],
                    chunk_index=proposal["chunk_index"],
                    content_text=proposal["content_text"],
                    curation_patch_id=str(patch_id),
                )
            else:
                _message, version_id = store.write_page_in_connection(
                    conn,
                    patch["path"],
                    proposal["title"],
                    proposal["content"],
                    page_type=proposal["page_type"],
                    created_by="remote-curator",
                    tags=proposal["tags"],
                    source_ids=proposal["source_ids"],
                    parent_id=proposal["parent_id"],
                    chunk_index=proposal["chunk_index"],
                    content_text=proposal["content_text"],
                    curation_patch_id=str(patch_id),
                    expected_base_content_hash=expected,
                )
            return UUID(version_id)
        kwargs: dict[str, Any] = {}
        if not force:
            kwargs["expected_base_content_hash"] = (
                None
                if operation == "create_page"
                else patch["base_content_hash"]
            )
        _message, version_id = store.pages.write_in_connection(
            conn,
            patch["path"],
            proposal["title"],
            proposal["content"],
            page_type=proposal["page_type"],
            created_by="remote-curator",
            tags=proposal["tags"],
            source_ids=proposal["source_ids"],
            parent_id=proposal["parent_id"],
            chunk_index=proposal["chunk_index"],
            content_text_override=proposal["content_text"],
            curation_patch_id=patch_id,
            preserve_llm_tags=False,
            **kwargs,
        )
        return version_id
    source = page_state(conn, patch["path"], lock=True)
    target = target_page(conn, proposal["target_path"], lock=True)
    if not source or not target:
        raise RuntimeError(
            "curation cleanup precondition disappeared: "
            f"{patch['path']}"
        )
    if operation == "retarget_links":
        _retarget_links(conn, source, target)
    elif operation == "add_alias":
        if isinstance(conn, sqlite3.Connection):
            conn.execute(
                "INSERT INTO page_aliases "
                "(alias_path, target_page_id, curation_patch_id) "
                "VALUES (?, ?, ?) ON CONFLICT (alias_path) DO NOTHING",
                (patch["path"], target["page_id"], str(patch_id)),
            )
        else:
            conn.execute(
                "INSERT INTO page_aliases "
                "(alias_path, target_page_id, curation_patch_id) "
                "VALUES (%s, %s, %s) ON CONFLICT (alias_path) DO NOTHING",
                (patch["path"], UUID(target["page_id"]), patch_id),
            )
    elif operation == "supersede_page":
        if isinstance(conn, sqlite3.Connection):
            conn.execute(
                "UPDATE pages SET status = 'superseded', "
                "superseded_by_page_id = ?, updated_at = ? WHERE page_id = ?",
                (
                    target["page_id"],
                    datetime.now(timezone.utc).isoformat(),
                    source["page_id"],
                ),
            )
        else:
            conn.execute(
                "UPDATE pages SET status = 'superseded', "
                "superseded_by_page_id = %s, updated_at = now() "
                "WHERE page_id = %s",
                (UUID(target["page_id"]), UUID(source["page_id"])),
            )
    if isinstance(conn, sqlite3.Connection):
        conn.execute(
            "INSERT INTO operations "
            "(operation_id, op_type, path, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                operation,
                patch["path"],
                json.dumps({"curation_patch_id": patch["patch_id"]}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    else:
        from psycopg.types.json import Jsonb

        conn.execute(
            "INSERT INTO operations "
            "(operation_id, op_type, path, details) VALUES (%s, %s, %s, %s)",
            (
                uuid4(),
                operation,
                patch["path"],
                Jsonb({"curation_patch_id": patch["patch_id"]}),
            ),
        )
    return None


def _apply_group(
    store: PostgresStore | SQLiteStore,
    conn: Connection,
    run_id: UUID,
    group_id: UUID,
    patches: list[dict[str, Any]],
    *,
    force: bool,
    failpoint: Callable[[str], None],
) -> tuple[str, list[dict[str, Any]]]:
    processed = receipt_count(conn, patches)
    if processed == len(patches):
        return "already_processed", []
    if processed:
        raise RuntimeError(
            f"curation group has partial receipts: {group_id}"
        )
    statuses, conflicts = preflight_group(
        conn,
        patches,
        lock=True,
        force=force,
    )
    if conflicts:
        if isinstance(conn, sqlite3.Connection):
            conn.execute(
                "INSERT INTO curation_conflicts "
                "(conflict_id, run_id, group_id, details) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (run_id, group_id) DO UPDATE "
                "SET details = excluded.details WHERE curation_conflicts.status = 'pending'",
                (
                    str(uuid4()),
                    str(run_id),
                    str(group_id),
                    json.dumps(conflicts, separators=(",", ":"), sort_keys=True),
                ),
            )
        else:
            from psycopg.types.json import Jsonb

            conn.execute(
                "INSERT INTO curation_conflicts "
                "(conflict_id, run_id, group_id, details) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (run_id, group_id) DO UPDATE "
                "SET details = excluded.details "
                "WHERE curation_conflicts.status = 'pending'",
                (uuid4(), run_id, group_id, Jsonb(conflicts)),
            )
        return "conflict", conflicts
    for patch, patch_status in zip(patches, statuses):
        if patch_status == "already_applied":
            state = page_state(conn, patch["path"], lock=False)
            result_id = UUID(state["version_id"]) if state else None
            insert_receipt(
                conn,
                run_id,
                patch,
                status="already_applied",
                result_version_id=result_id,
            )
        else:
            result_id = _apply_patch(store, conn, patch, force=force)
            insert_receipt(
                conn,
                run_id,
                patch,
                status="applied",
                result_version_id=result_id,
            )
    failpoint("before_group_commit")
    return "applied", []


def apply_run(
    store: PostgresStore | SQLiteStore,
    run_id: UUID,
    *,
    policy: str = "safe",
    failpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if policy not in {"safe", "all"}:
        raise ValueError("curation policy must be 'safe' or 'all'")
    store.migrate()
    trigger = failpoint or (lambda _stage: None)
    with store.connection() as conn:
        patches = patches_for_run(conn, run_id)
    results = []
    for group_id, items in grouped(patches):
        if policy == "safe" and any(
            item["risk_class"] != "low" for item in items
        ):
            results.append(
                {
                    "group_id": str(group_id),
                    "status": "review_required",
                }
            )
            continue
        with _write_connection(store) as conn:
            status, details = _apply_group(
                store,
                conn,
                run_id,
                group_id,
                items,
                force=False,
                failpoint=trigger,
            )
            mark_run_applied(conn, run_id)
        results.append(
            {
                "group_id": str(group_id),
                "status": status,
                "details": details,
            }
        )
    return {
        "run_id": str(run_id),
        "groups": results,
        "counts": dict(Counter(item["status"] for item in results)),
    }


def resolve_conflict(
    store: PostgresStore | SQLiteStore,
    run_id: UUID,
    group_id: UUID,
    *,
    action: str,
) -> dict[str, Any]:
    if action == "defer":
        return {
            "run_id": str(run_id),
            "group_id": str(group_id),
            "resolution": "deferred",
        }
    if action not in {"keep_local", "accept_remote", "merged"}:
        raise ValueError(
            "resolution must be keep_local, accept_remote, merged, or defer"
    )
    store.migrate()
    with _write_connection(store) as conn:
        sqlite = isinstance(conn, sqlite3.Connection)
        conflict = conn.execute(
            "SELECT conflict_id FROM curation_conflicts "
            f"WHERE run_id = {'?' if sqlite else '%s'} "
            f"AND group_id = {'?' if sqlite else '%s'} AND status = 'pending' "
            + ("" if sqlite else "FOR UPDATE"),
            (
                str(run_id) if sqlite else run_id,
                str(group_id) if sqlite else group_id,
            ),
        ).fetchone()
        if not conflict:
            raise KeyError(
                "pending curation conflict not found: "
                f"{run_id}/{group_id}"
            )
        patches = [
            patch
            for patch in patches_for_run(conn, run_id)
            if UUID(patch["group_id"]) == group_id
        ]
        if action == "accept_remote":
            status, details = _apply_group(
                store,
                conn,
                run_id,
                group_id,
                patches,
                force=True,
                failpoint=lambda _stage: None,
            )
            if status == "conflict":
                raise RuntimeError(
                    "remote proposal no longer has a valid target: "
                    f"{details}"
                )
        else:
            receipt_status = (
                "kept_local" if action == "keep_local" else "merged"
            )
            for patch in patches:
                state = page_state(conn, patch["path"], lock=True)
                result_id = UUID(state["version_id"]) if state else None
                insert_receipt(
                    conn,
                    run_id,
                    patch,
                    status=receipt_status,
                    result_version_id=result_id,
                )
        if sqlite:
            conn.execute(
                "UPDATE curation_conflicts SET status = 'resolved', "
                "resolution = ?, resolved_at = ? WHERE conflict_id = ?",
                (action, datetime.now(timezone.utc).isoformat(), conflict[0]),
            )
        else:
            conn.execute(
                "UPDATE curation_conflicts SET status = 'resolved', "
                "resolution = %s, resolved_at = now() WHERE conflict_id = %s",
                (action, conflict[0]),
            )
        mark_run_applied(conn, run_id)
    return {
        "run_id": str(run_id),
        "group_id": str(group_id),
        "resolution": action,
    }
