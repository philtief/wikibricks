"""Nightly local curation orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable
from uuid import uuid5

from wikibricks.curation import (
    apply_run,
    build_manifest,
    canonical_json,
    get_or_create_replica_id,
    store_manifest,
)
from wikibricks.curation.backlog import load_curation_backlog
from wikibricks.storage.sqlite_store import SQLiteStore
from wikibricks_curator.evidence import build_request
from wikibricks_remote.proposals import build_patches
from wikibricks_remote.resources import load_policy, load_prompt, load_schema

Chat = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _living_page(item: dict[str, Any]) -> str:
    return next(
        (path for path in item.get("pages", []) if path.startswith("topics/")),
        f"topics/{item['project']}",
    )


def _proposal_result(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "operation": proposal.get("operation"),
            "path": proposal.get("path"),
            "title": proposal.get("title"),
            "risk_class": proposal.get("risk_class"),
            "reason": proposal.get("reason"),
        }
        for proposal in raw.get("proposals", [])
        if isinstance(proposal, dict)
    ]


def _guard_shrinking_updates(
    raw: dict[str, Any],
    pages: list[dict[str, Any]],
) -> int:
    proposals = raw.get("proposals")
    if not isinstance(proposals, list):
        return 0
    current = {page["path"]: page for page in pages}
    guarded = 0
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        if proposal.get("operation") != "update_page":
            continue
        path = proposal.get("path")
        page = current.get(path) if isinstance(path, str) else None
        if page is None:
            continue
        content = page.get("content") or {}
        current_length = len(str(content.get("summary", ""))) + len(
            str(content.get("body", ""))
        )
        proposed_length = len(str(proposal.get("summary", ""))) + len(
            str(proposal.get("body", ""))
        )
        if proposed_length < current_length / 2:
            proposal["risk_class"] = "high"
            guarded += 1
    return guarded


def run_curator(
    database_path: str | Path,
    *,
    chat: Chat,
    projects: int = 3,
    apply: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Propose curation for local backlog projects and optionally apply safe groups."""
    store = SQLiteStore(database_path)
    store.migrate()
    prompt = load_prompt() + "\n\n" + (
        files("wikibricks_curator")
        .joinpath("resources", "local-curator.md")
        .read_text(encoding="utf-8")
    )
    schema = load_schema()
    policy = replace(load_policy(), allowed_operations=("create_page", "update_page", "add_link"))
    with store.connection() as conn:
        backlog = load_curation_backlog(conn, limit=projects)
    replica_id = get_or_create_replica_id(store)
    results: list[dict[str, Any]] = []
    errors = 0

    for item in backlog:
        project = item["project"]
        living_page = _living_page(item)
        result: dict[str, Any] = {
            "project": project,
            "living_page": living_page,
            "events": 0,
            "status": "no_evidence",
            "proposals": [],
            "guarded": 0,
            "run_id": None,
            "counts": {},
            "error": None,
        }
        with store.connection() as conn:
            cursor = store.get_sync_cursor(f"curator:{project}").get("newest_evidence_at")
            built = build_request(conn, item, cursor=cursor)
        if built is None:
            results.append(result)
            continue
        result["events"] = len(built["request"]["evidence"])
        try:
            raw = chat(prompt, built["request"], schema)
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)[:300]
            results.append(result)
            errors += 1
            continue
        result["guarded"] = _guard_shrinking_updates(raw, built["pages"])
        result["proposals"] = _proposal_result(raw)
        digest = hashlib.sha256(
            canonical_json(built["request"]).encode("utf-8")
        ).hexdigest()
        run_id = uuid5(replica_id, f"wikibricks:local-curator:{project}:{digest}")
        try:
            patches = build_patches(
                raw,
                run_id=run_id,
                pages=built["pages"],
                evidence_ids=built["evidence_ids"],
                policy=policy,
            )
        except ValueError as exc:
            result["status"] = "error"
            result["error"] = str(exc)[:300]
            results.append(result)
            errors += 1
            continue
        if not patches:
            result["status"] = "no_changes"
            results.append(result)
            if not dry_run:
                store.set_sync_cursor(
                    f"curator:{project}",
                    {"newest_evidence_at": built["newest_evidence_at"]},
                )
            continue
        if dry_run:
            result["status"] = "dry_run"
            result["run_id"] = str(run_id)
            results.append(result)
            continue
        manifest = build_manifest(
            replica_id=replica_id,
            input_watermark=0,
            patches=patches,
            run_id=run_id,
        )
        store_manifest(store, manifest)
        result["run_id"] = str(run_id)
        if apply:
            outcome = apply_run(store, run_id, policy="safe")
            result["counts"] = outcome["counts"]
            result["status"] = (
                "applied"
                if outcome["counts"].get("applied") == len(outcome["groups"])
                else "review_required"
            )
        else:
            result["status"] = "review_required"
        store.set_sync_cursor(
            f"curator:{project}",
            {"newest_evidence_at": built["newest_evidence_at"]},
        )
        results.append(result)

    return {"projects": results, "errors": errors}
