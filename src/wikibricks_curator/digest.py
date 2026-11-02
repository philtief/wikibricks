"""Give pages a short summary card and key points without rewriting their body."""

from __future__ import annotations

import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable
from uuid import uuid5

from wikibricks.curation import (
    apply_run,
    build_manifest,
    get_or_create_replica_id,
    store_manifest,
)
from wikibricks.curation.digest import SUMMARY_MAX_CHARS, undigested_pages, with_key_points
from wikibricks.curation.protocol import content_hash
from wikibricks.storage.sqlite_store import SQLiteStore
from wikibricks_curator.evidence import _active_pages
from wikibricks_remote.proposals import build_patches
from wikibricks_remote.resources import load_policy

Chat = Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
_KEY_POINT_MAX_CHARS = 200


def _resource(name: str) -> str:
    return files("wikibricks_curator").joinpath("resources", name).read_text(encoding="utf-8")


def _problems(reply: Any) -> list[str]:
    if not isinstance(reply, dict):
        return ["the reply is not a JSON object"]
    summary = reply.get("summary")
    points = reply.get("key_points")
    problems = []
    if not isinstance(summary, str) or not summary.strip():
        problems.append("summary is missing")
    elif len(summary.strip()) > SUMMARY_MAX_CHARS:
        problems.append(
            f"summary has {len(summary.strip())} characters; shorten it to about 200 "
            f"(the limit is {SUMMARY_MAX_CHARS})"
        )
    if not isinstance(points, list) or not 3 <= len(points) <= 7:
        problems.append("key_points must hold three to seven items")
        return problems
    for index, point in enumerate(points, start=1):
        if not isinstance(point, str) or not point.strip():
            problems.append(f"key point {index} is empty")
        elif len(point.strip()) > _KEY_POINT_MAX_CHARS:
            problems.append(
                f"key point {index} has {len(point.strip())} characters; shorten it to about 120 "
                f"(the limit is {_KEY_POINT_MAX_CHARS})"
            )
    return problems


def _proposal(page: dict[str, Any], reply: dict[str, Any]) -> dict[str, Any]:
    return {
        "group": page["path"],
        "operation": "update_page",
        "path": page["path"],
        "title": page["title"],
        "page_type": page["page_type"],
        "summary": reply["summary"].strip(),
        "body": with_key_points(
            str(page["content"].get("body") or ""),
            [point.strip() for point in reply["key_points"]],
        ),
        "tags": page["tags"],
        "source_ids": page["source_ids"],
        "target_path": None,
        "evidence_ids": [page["evidence_id"]],
        "reason": "Summary card and key points for readers; the rest of the body is unchanged.",
        "risk_class": "low",
    }


def run_digest(
    database_path: str | Path,
    *,
    chat: Chat,
    limit: int = 10,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Rewrite the summary and key points of up to `limit` pages that break the layout."""
    store = SQLiteStore(database_path)
    store.migrate()
    prompt = _resource("digest.md")
    schema = json.loads(_resource("digest.schema.json"))
    with store.connection() as conn:
        pending = undigested_pages(conn)
        selected = pending[:limit]
        pages = {
            page["path"]: page
            for page in _active_pages(
                conn, paths={item["path"] for item in selected}, project="", related=0
            )
        }
    results: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for item in selected:
        page = pages[item["path"]]
        request = {
            "path": page["path"],
            "title": page["title"],
            "page_type": page["page_type"],
            "summary": str(page["content"].get("summary") or ""),
            "body": str(page["content"].get("body") or ""),
        }
        reply, error, attempt_request = None, None, request
        # The model cannot count characters; the retry names what to shorten.
        for _attempt in (1, 2):
            try:
                candidate = chat(prompt, attempt_request, schema)
            except Exception as exc:
                error = str(exc)[:300]
                continue
            problems = _problems(candidate)
            if not problems:
                reply = candidate
                break
            error = "; ".join(problems)
            attempt_request = {**request, "rejected_reply": candidate, "problems": problems}
        if reply is None:
            results.append({**item, "status": "error", "error": error})
            continue
        proposals.append(_proposal(page, reply))
        results.append({**item, "status": "dry_run" if dry_run else "proposed"})
    counts: dict[str, int] = {}
    run_id = None
    if proposals and not dry_run:
        replica_id = get_or_create_replica_id(store)
        run_id = uuid5(replica_id, f"wikibricks:local-digest:{content_hash(proposals)}")
        policy = replace(
            load_policy(),
            allowed_operations=("update_page",),
            max_proposals_per_replica=len(proposals),
        )
        patches = build_patches(
            {"proposals": proposals},
            run_id=run_id,
            pages=list(pages.values()),
            evidence_ids=set(),
            policy=policy,
        )
        manifest = build_manifest(
            replica_id=replica_id,
            input_watermark=0,
            patches=patches,
            run_id=run_id,
        )
        store_manifest(store, manifest)
        counts = apply_run(store, run_id, policy="safe", created_by="local-curator")["counts"]
    return {
        "pages": results,
        "run_id": str(run_id) if run_id else None,
        "counts": counts,
        "errors": sum(result["status"] == "error" for result in results),
        "undigested": len(pending),
    }


__all__ = ["run_digest"]
