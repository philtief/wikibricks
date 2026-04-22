"""Pure helpers used by `notebooks/wiki_curate.py`.

Extracted so the curate flow's decision logic can be unit-tested without a
workspace. No LLM calls, no SDK calls.
"""

from __future__ import annotations

from typing import Iterable


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
) -> dict:
    """Build the per-run JSON summary the curate notebook prints at the end."""
    by_check = {
        c: sum(1 for i in lint_issues if i.get("check") == c)
        for c in ("orphan", "stale", "duplicate_path", "broken_link")
    }
    return {
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
