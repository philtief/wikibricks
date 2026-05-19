"""Page clustering for cross-session topic synthesis.

Two clustering signals are supported:

- ``cluster_pages_by_community(pages)`` — v0.7.6 default. Uses the
  ``community_id`` written nightly by the ``graph_analytics`` task
  (Leiden over the currently-valid edge graph). Pages without a
  ``community_id`` are dropped.
- ``cluster_pages_by_keyword(pages, keywords)`` — v0.5.0 path. Manual
  topic vocabulary; useful when a curated topic list matters more than
  whatever the graph found.

Pure functions, no IO, no LLM (per the library's hard rules). Used by
``notebooks/promote_topics.py`` to group pages before LLM synthesis.
"""

from __future__ import annotations

import re
from collections import Counter

UNCATEGORISED = "_uncategorised"

# Stop-words skipped when deriving a topic slug from member-page titles.
# Intentionally short; deeper NLP belongs in the LLM step, not here.
_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "he", "in", "is", "it", "its", "of", "on", "or", "that",
    "the", "this", "to", "was", "were", "will", "with", "we", "you",
    "your", "session", "sessions", "page", "chunk", "claude", "code",
    "stub", "summarize", "summary", "memory", "log", "entry", "agent",
    "agents", "task", "demo", "test", "meeting", "follow", "review",
    "draft", "doc", "docs", "update", "fix", "bug", "issue",
})
_MAX_SLUG_LEN = 60
_WORD_RE = re.compile(r"[a-z0-9]+")


def cluster_pages_by_keyword(
    pages: list[dict],
    keywords: dict[str, list[str]],
) -> dict[str, list[dict]]:
    """Group ``pages`` into topic buckets by case-insensitive substring match
    against page titles. Each page is assigned to the first topic whose terms
    match; non-matching pages go to ``UNCATEGORISED``. Empty buckets are pruned.

    ``keywords`` is ordered: when multiple topics match, the earliest one in
    the dict wins. Use insertion order to encode priority.
    """
    if not pages:
        return {}
    buckets: dict[str, list[dict]] = {slug: [] for slug in keywords}
    buckets[UNCATEGORISED] = []
    for page in pages:
        title_lower = (page.get("title") or "").lower()
        for slug, terms in keywords.items():
            if any(term.lower() in title_lower for term in terms):
                buckets[slug].append(page)
                break
        else:
            buckets[UNCATEGORISED].append(page)
    return {k: v for k, v in buckets.items() if v}


def cluster_pages_by_community(
    pages: list[dict],
    *,
    min_cluster_size: int = 2,
) -> dict[int, list[dict]]:
    """Group ``pages`` by their ``community_id`` (Leiden output).

    Pages with ``community_id is None`` are dropped (not yet analytics-scored).
    Clusters smaller than ``min_cluster_size`` are dropped (singletons aren't
    worth synthesising). Within each cluster, pages are sorted by ``hub_score``
    descending so downstream LLM synthesis can prefer authoritative members.
    """
    if not pages:
        return {}
    buckets: dict[int, list[dict]] = {}
    for page in pages:
        cid = page.get("community_id")
        if cid is None:
            continue
        buckets.setdefault(cid, []).append(page)
    # Drop below-threshold clusters, sort members by hub_score desc.
    return {
        cid: sorted(members, key=lambda p: -(p.get("hub_score") or 0.0))
        for cid, members in buckets.items()
        if len(members) >= min_cluster_size
    }


def topic_slug_from_titles(titles: list[str], *, community_id: int | None = None) -> str:
    """Deterministic slug derived from word frequency across ``titles``.

    Tokenises titles, drops stop-words, picks the 1–3 most common
    significant words, joins with ``-``. Falls back to ``community-<id>``
    when nothing useful can be extracted (or to ``community-unknown``
    when ``community_id`` is None too). Slug is lowercased and capped at
    ``_MAX_SLUG_LEN`` characters.
    """
    word_counts: Counter[str] = Counter()
    for title in titles:
        if not title:
            continue
        for word in _WORD_RE.findall(title.lower()):
            if len(word) < 3 or word in _STOP_WORDS:
                continue
            word_counts[word] += 1
    if not word_counts:
        if community_id is None:
            return "community-unknown"
        return f"community-{community_id}"
    # Take the top-3 words by frequency; ties broken by alphabetical order
    # for determinism.
    top = sorted(word_counts.most_common(3), key=lambda kv: (-kv[1], kv[0]))
    slug = "-".join(w for w, _ in top)
    return slug[:_MAX_SLUG_LEN]
