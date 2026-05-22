"""Tests for keyword-based page clustering used by promote_topics."""

from __future__ import annotations

from wikibricks.topic_clustering import cluster_pages_by_keyword

KW = {
    "solvd": ["solvd", "controlexpert"],
    "allianz-italy": ["allianz italy", "az italy", "azitaly"],
    "agi": ["allianz global investors", "agi"],
}


def _page(title: str, path: str = "p") -> dict:
    return {"path": path, "title": title}


def test_returns_empty_dict_for_empty_input():
    assert cluster_pages_by_keyword([], KW) == {}


def test_buckets_pages_by_first_matching_topic():
    pages = [
        _page("Solvd Lakebase migration scope"),
        _page("ControlExpert pricing decisions"),
        _page("Allianz Italy demo with NTT Data"),
    ]
    buckets = cluster_pages_by_keyword(pages, KW)
    assert len(buckets["solvd"]) == 2
    assert len(buckets["allianz-italy"]) == 1


def test_unmatched_pages_go_to_uncategorised():
    pages = [_page("Random other thing")]
    buckets = cluster_pages_by_keyword(pages, KW)
    assert buckets == {"_uncategorised": pages}


def test_case_insensitive_matching():
    pages = [_page("SOLVD KICKOFF MEETING")]
    buckets = cluster_pages_by_keyword(pages, KW)
    assert buckets["solvd"] == pages


def test_first_match_wins_when_multiple_topics_match():
    # "allianz global investors solvd" mentions both, solvd is listed first
    kw_ordered = {"solvd": ["solvd"], "agi": ["allianz global investors"]}
    pages = [_page("Solvd and Allianz Global Investors joint meeting")]
    buckets = cluster_pages_by_keyword(pages, kw_ordered)
    assert "solvd" in buckets
    assert "agi" not in buckets


def test_empty_buckets_are_pruned():
    pages = [_page("Solvd thing")]
    buckets = cluster_pages_by_keyword(pages, KW)
    assert "agi" not in buckets
    assert "allianz-italy" not in buckets
    assert list(buckets.keys()) == ["solvd"]
