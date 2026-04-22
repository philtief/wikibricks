"""Pure helpers used by `notebooks/promote_from_traces.py`.

Everything LLM-flavored (endpoint queries, judge scoring, canonical-answer
synthesis) stays inline in the notebook; this module holds only the
deterministic math + decision logic so it can be unit-tested without a
workspace.
"""

from __future__ import annotations

from math import sqrt
from typing import Sequence


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sqrt(sum(x * x for x in a))
    nb = sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def cluster_by_cosine(
    rows: list[dict], threshold: float, *, embedding_key: str = "embedding"
) -> list[list[dict]]:
    """Threshold-agglomerative clustering over row embeddings.

    A row joins the first existing cluster whose medoid (first member) scores
    above `threshold`. Otherwise it starts its own. Stable under row order,
    O(n * k) where k = number of clusters — adequate for < 10 k rows.
    """
    clusters: list[list[dict]] = []
    for r in rows:
        placed = False
        for cluster in clusters:
            if cosine(r[embedding_key], cluster[0][embedding_key]) >= threshold:
                cluster.append(r)
                placed = True
                break
        if not placed:
            clusters.append([r])
    return clusters


def filter_eligible_clusters(
    clusters: list[list[dict]],
    *,
    min_members: int,
    min_distinct_sessions: int,
    max_clusters: int,
    session_key: str = "session_id",
) -> list[list[dict]]:
    """Keep clusters that represent a recurring question across distinct users.

    Two gates:
      - `len(cluster) >= min_members` — not a one-off.
      - `|{session_id}| >= min_distinct_sessions` — not one user asking five
        times.
    Final list is truncated to `max_clusters` to bound per-run LLM cost.
    """
    eligible = [
        c for c in clusters
        if len(c) >= min_members
        and len({m[session_key] for m in c}) >= min_distinct_sessions
    ]
    return eligible[:max_clusters]


def parse_judge_score(text: str) -> float:
    """Extract a 1-5 judge score from an LLM response.

    Prompt asks for a single digit. Be tolerant of leading whitespace or
    trailing punctuation. Return 0.0 on anything unparseable so the caller
    can reject the cluster.
    """
    if not text:
        return 0.0
    stripped = text.strip()
    if not stripped:
        return 0.0
    first = stripped[0]
    try:
        return float(first) if first.isdigit() else 0.0
    except ValueError:
        return 0.0


def is_duplicate_hit(
    search_hit: dict | None,
    *,
    score_threshold: float = 0.9,
    path_prefix: str = "promoted/",
) -> bool:
    """Decide whether an existing promoted page is close enough to dedup against."""
    if not search_hit:
        return False
    path = search_hit.get("path", "") or ""
    score = search_hit.get("score", 0.0) or 0.0
    return path.startswith(path_prefix) and score > score_threshold
