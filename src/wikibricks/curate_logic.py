"""Pure helpers used by `notebooks/wiki_curate.py`.

Extracted so the curate flow's decision logic can be unit-tested without a
workspace. No LLM calls, no SDK calls.
"""

from __future__ import annotations

from typing import Iterable

BODY_OVERSIZE_THRESHOLD = 50_000
"""Bytes-of-body above which a page is flagged for segregation."""


def partition_by_confidence(
    edges: Iterable[dict], threshold: float
) -> tuple[list[dict], list[dict]]:
    """Split proposed edges into (auto-commit, defer-to-agent) by `confidence`.

    Edges whose `confidence` is >= threshold go in the first list (written
    deterministically by the curate job). Lower-confidence edges are returned
    separately so the agent can review them on its next call.
    """
    high, low = [], []
    for e in edges:
        (high if e.get("confidence", 0.0) >= threshold else low).append(e)
    return high, low


def build_curate_summary(
    *,
    paths_scanned: int,
    edges_proposed: int,
    edges_committed: int,
    deferred_low_confidence: int,
    auto_commit_threshold: float,
    lint_issues: list[dict],
    broken_links_deleted: int | None,
    health: dict | None = None,
) -> dict:
    """Build the per-run JSON summary the curate notebook prints at the end."""
    by_check = {
        c: sum(1 for i in lint_issues if i.get("check") == c)
        for c in ("orphan", "stale", "duplicate_path", "broken_link")
    }
    summary = {
        "connect": {
            "pages_scanned": paths_scanned,
            "edges_proposed": edges_proposed,
            "edges_committed": edges_committed,
            "deferred_low_confidence": deferred_low_confidence,
            "auto_commit_threshold": auto_commit_threshold,
        },
        "lint": {
            "issues": len(lint_issues),
            "by_check": by_check,
        },
        "repair": {"broken_links_deleted": broken_links_deleted},
    }
    if health is not None:
        summary["health"] = health
    return summary


def classify_page_health(
    page: dict, *, body_max: int = BODY_OVERSIZE_THRESHOLD
) -> tuple[str, float]:
    """Deterministic health check for one page row.

    Returns `(status, score)` where status is one of `ok`, `oversize`, `empty`,
    and score is in [0.0, 1.0]. Pages above `body_max` are candidates for
    segregation into a parent-summary + chunk pages by a future v2 LLM step.
    """
    body = (page.get("body") or "").strip()
    if not body:
        return "empty", 0.0
    if len(body) > body_max:
        return "oversize", 0.3
    return "ok", 1.0


def find_duplicate_paths(pages: Iterable[dict]) -> list[dict]:
    """Return one row per path that occurs more than once.

    Each row: `{path, count, page_ids}`. Rows missing `path` are skipped.
    """
    by_path: dict[str, list] = {}
    for p in pages:
        path = p.get("path")
        if not path:
            continue
        by_path.setdefault(path, []).append(p.get("id"))
    return [
        {"path": path, "count": len(ids), "page_ids": ids}
        for path, ids in by_path.items()
        if len(ids) > 1
    ]


def build_health_summary(
    *,
    pages_checked: int,
    by_status: dict[str, int],
    duplicates: int,
) -> dict:
    """Per-run JSON for the health phase. Logged under `op_type='curate_run'`."""
    return {
        "pages_checked": pages_checked,
        "by_status": by_status,
        "duplicates": duplicates,
    }
