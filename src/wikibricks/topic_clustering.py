"""Keyword-based page clustering for cross-session topic synthesis.

Pure functions, no IO, no LLM (per the library's hard rules). Used by
the ``notebooks/promote_topics.py`` task to group session pages into
topic buckets before LLM synthesis.
"""

from __future__ import annotations

UNCATEGORISED = "_uncategorised"


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
