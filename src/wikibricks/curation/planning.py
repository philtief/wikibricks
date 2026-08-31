"""Preflight planning for local curation patch groups."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from psycopg import Connection

from wikibricks.curation.repository import (
    page_state,
    patches_for_run,
    receipt_count,
    target_page,
)
from wikibricks.postgres_store import PostgresStore


def grouped(
    patches: Iterable[dict[str, Any]],
) -> list[tuple[UUID, list[dict[str, Any]]]]:
    groups: dict[UUID, list[dict[str, Any]]] = {}
    for patch in patches:
        groups.setdefault(UUID(patch["group_id"]), []).append(patch)
    return [
        (
            group_id,
            sorted(items, key=lambda item: item["position"]),
        )
        for group_id, items in groups.items()
    ]


def _conflict_detail(
    patch: dict[str, Any],
    local: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "patch_id": patch["patch_id"],
        "operation": patch["operation"],
        "path": patch["path"],
        "base_version_id": patch["base_version_id"],
        "base_content_hash": patch["base_content_hash"],
        "local": local,
        "remote": patch["proposal"],
        "reason": reason,
    }


def preflight_group(
    conn: Connection,
    patches: list[dict[str, Any]],
    *,
    lock: bool,
    force: bool = False,
) -> tuple[list[str], list[dict[str, Any]]]:
    statuses = []
    conflicts = []
    supersedes = {
        patch["path"]: patch["proposal"]["target_path"]
        for patch in patches
        if patch["operation"] == "supersede_page"
    }
    for patch in patches:
        operation = patch["operation"]
        local = page_state(conn, patch["path"], lock=lock)
        if operation == "create_page":
            if local is None:
                statuses.append("applicable")
            elif (
                local["status"] == "active"
                and local["content_hash"] == patch["proposed_hash"]
            ):
                statuses.append("already_applied")
            elif force and local["status"] == "active":
                statuses.append("applicable")
            else:
                statuses.append("conflict")
                conflicts.append(
                    _conflict_detail(patch, local, "path already exists")
                )
            continue
        target_path = patch["proposal"].get("target_path")
        target = (
            target_page(conn, target_path, lock=lock)
            if target_path
            else None
        )
        if (
            operation == "supersede_page"
            and local
            and local["status"] == "superseded"
        ):
            if (
                target
                and local["superseded_by_page_id"] == target["page_id"]
            ):
                statuses.append("already_applied")
            else:
                statuses.append("conflict")
                conflicts.append(
                    _conflict_detail(
                        patch,
                        local,
                        "page is already superseded elsewhere",
                    )
                )
            continue
        if operation == "add_alias":
            alias = conn.execute(
                "SELECT target_page_id FROM page_aliases WHERE alias_path = %s",
                (patch["path"],),
            ).fetchone()
            if alias and target and str(alias[0]) == target["page_id"]:
                statuses.append("already_applied")
                continue
            if alias:
                statuses.append("conflict")
                conflicts.append(
                    _conflict_detail(
                        patch,
                        local,
                        "alias points to another page",
                    )
                )
                continue
        if local is None:
            statuses.append("conflict")
            conflicts.append(
                _conflict_detail(patch, None, "base page is missing")
            )
            continue
        base_matches = (
            local["version_id"] == patch["base_version_id"]
            and local["content_hash"] == patch["base_content_hash"]
        )
        if (
            operation == "update_page"
            and local["content_hash"] == patch["proposed_hash"]
        ):
            statuses.append("already_applied")
        elif not force and (
            local["status"] != "active" or not base_matches
        ):
            statuses.append("conflict")
            conflicts.append(
                _conflict_detail(
                    patch,
                    local,
                    "local page changed after the remote base",
                )
            )
        elif (
            operation
            in {"retarget_links", "add_alias", "supersede_page"}
            and not target
        ):
            statuses.append("conflict")
            conflicts.append(
                _conflict_detail(
                    patch,
                    local,
                    "canonical target is missing or inactive",
                )
            )
        elif (
            operation == "add_alias"
            and local["status"] == "active"
            and supersedes.get(patch["path"]) != target_path
        ):
            statuses.append("conflict")
            conflicts.append(
                _conflict_detail(
                    patch,
                    local,
                    "active page is not superseded in this group",
                )
            )
        else:
            statuses.append("applicable")
    return statuses, conflicts


def plan_run(
    store: PostgresStore,
    run_id: UUID,
    *,
    policy: str = "safe",
) -> dict[str, Any]:
    if policy not in {"safe", "all"}:
        raise ValueError("curation policy must be 'safe' or 'all'")
    store.migrate()
    groups = []
    with store.connection() as conn:
        patches = patches_for_run(conn, run_id)
        for group_id, items in grouped(patches):
            processed = receipt_count(conn, items)
            if processed == len(items):
                status = "already_processed"
                details = []
            elif processed:
                status = "conflict"
                details = [{"reason": "patch group has partial receipts"}]
            elif policy == "safe" and any(
                item["risk_class"] != "low" for item in items
            ):
                status = "review_required"
                details = []
            else:
                patch_statuses, details = preflight_group(
                    conn,
                    items,
                    lock=False,
                )
                if details:
                    status = "conflict"
                elif all(
                    value == "already_applied"
                    for value in patch_statuses
                ):
                    status = "already_applied"
                else:
                    status = "applicable"
            groups.append(
                {
                    "group_id": str(group_id),
                    "status": status,
                    "details": details,
                }
            )
    return {
        "run_id": str(run_id),
        "groups": groups,
        "counts": dict(Counter(item["status"] for item in groups)),
    }
