"""v0.7.6 — community_id-based page clustering (Leiden output) +
deterministic topic-slug derivation from page titles.

Replaces the v0.5.0 keyword-only flow as the default clustering signal
for the promote_topics task. Keyword clustering stays available for
operators who want a curated topic vocabulary.
"""

from __future__ import annotations

from wikibricks.topic_clustering import (
    cluster_pages_by_community,
    topic_slug_from_titles,
)


def _page(pid: str, title: str, community_id: int | None, hub_score: float = 0.0) -> dict:
    return {"page_id": pid, "title": title, "community_id": community_id, "hub_score": hub_score}


# ----------------------------------------------------------------------------
# cluster_pages_by_community
# ----------------------------------------------------------------------------

def test_cluster_empty_input():
    assert cluster_pages_by_community([]) == {}


def test_cluster_groups_pages_by_community_id():
    pages = [
        _page("a", "Solvd lakebase scope", 1),
        _page("b", "Solvd KYC review", 1),
        _page("c", "Allianz Italy demo", 2),
        _page("d", "Allianz Italy follow-up", 2),
    ]
    buckets = cluster_pages_by_community(pages)
    assert set(buckets.keys()) == {1, 2}
    assert len(buckets[1]) == 2
    assert len(buckets[2]) == 2


def test_cluster_drops_pages_with_null_community_id():
    pages = [
        _page("a", "Solvd thing", 1),
        _page("b", "Solvd thing 2", 1),
        _page("c", "Just-written page", None),  # not yet scored
    ]
    buckets = cluster_pages_by_community(pages)
    assert 1 in buckets
    assert None not in buckets
    assert len(buckets[1]) == 2


def test_cluster_drops_singletons_when_min_size_2():
    pages = [
        _page("a", "Solo page", 1),
        _page("b", "Pair 1", 2),
        _page("c", "Pair 2", 2),
    ]
    buckets = cluster_pages_by_community(pages, min_cluster_size=2)
    assert 1 not in buckets
    assert 2 in buckets


def test_cluster_respects_min_size_param():
    pages = [
        _page(f"p{i}", f"Title {i}", 1) for i in range(4)
    ] + [
        _page(f"q{i}", f"Title {i}", 2) for i in range(2)
    ]
    buckets = cluster_pages_by_community(pages, min_cluster_size=3)
    assert 1 in buckets and len(buckets[1]) == 4
    assert 2 not in buckets  # below threshold


def test_cluster_pages_within_bucket_sorted_by_hub_score_desc():
    """Top-ranked pages per cluster should come first so the synthesis
    step can pick the most authoritative members.
    """
    pages = [
        _page("a", "low", 1, hub_score=0.001),
        _page("b", "high", 1, hub_score=0.05),
        _page("c", "mid", 1, hub_score=0.01),
    ]
    buckets = cluster_pages_by_community(pages)
    titles_in_order = [p["title"] for p in buckets[1]]
    assert titles_in_order == ["high", "mid", "low"]


# ----------------------------------------------------------------------------
# topic_slug_from_titles
# ----------------------------------------------------------------------------

def test_slug_returns_lowercase_hyphenated():
    slug = topic_slug_from_titles(["Allianz Italy demo", "Allianz Italy follow-up"])
    assert slug == slug.lower()
    assert " " not in slug


def test_slug_picks_common_significant_words():
    """Common words across titles should drive the slug; stop-words ignored."""
    slug = topic_slug_from_titles([
        "Solvd Lakebase migration scope",
        "Solvd KYC review meeting",
        "Solvd quarterly check-in",
    ])
    assert "solvd" in slug


def test_slug_deterministic_for_same_input():
    titles = ["Foo bar", "Foo baz", "Foo quux"]
    assert topic_slug_from_titles(titles) == topic_slug_from_titles(titles)


def test_slug_falls_back_to_community_id_when_no_signal():
    """Empty or noise-only titles should produce a usable fallback."""
    slug = topic_slug_from_titles([], community_id=42)
    assert slug == "community-42"


def test_slug_limits_length():
    long_titles = ["AAAA " * 50 for _ in range(3)]
    slug = topic_slug_from_titles(long_titles)
    assert len(slug) <= 60
