"""Behavioral tests for `wikibricks.graph_logic`.

Pure unit tests. No Databricks needed. Builds known small graphs with igraph,
runs PageRank / community detection, asserts the results match igraph's
reference implementation. Verifies RRF math against published reference
examples.
"""

import math

import pytest

ig = pytest.importorskip("igraph")

from wikibricks.graph_logic import (  # noqa: E402 — igraph import must succeed first
    build_igraph,
    compute_communities,
    compute_pagerank,
    rrf_fuse,
)


class TestBuildIgraph:
    def test_empty_input_returns_empty_graph(self):
        g = build_igraph(pages=[], edges=[])
        assert g.vcount() == 0
        assert g.ecount() == 0

    def test_pages_become_vertices_with_page_id_attribute(self):
        pages = [
            {"page_id": "p1", "path": "topics/foo"},
            {"page_id": "p2", "path": "topics/bar"},
        ]
        g = build_igraph(pages=pages, edges=[])
        assert g.vcount() == 2
        names = sorted(g.vs["name"])
        assert names == ["p1", "p2"]

    def test_edges_connect_vertices_by_page_id(self):
        pages = [{"page_id": "p1"}, {"page_id": "p2"}]
        edges = [{"source_page_id": "p1", "target_page_id": "p2"}]
        g = build_igraph(pages=pages, edges=edges)
        assert g.ecount() == 1
        # Verify the edge goes p1 -> p2
        e = g.es[0]
        src_name = g.vs[e.source]["name"]
        tgt_name = g.vs[e.target]["name"]
        assert src_name == "p1"
        assert tgt_name == "p2"

    def test_graph_is_directed(self):
        # PageRank cares about direction. cites is a directional relationship.
        g = build_igraph(pages=[{"page_id": "a"}, {"page_id": "b"}],
                         edges=[{"source_page_id": "a", "target_page_id": "b"}])
        assert g.is_directed()

    def test_edges_to_unknown_pages_are_dropped(self):
        # Defensive: if a link points to a page_id that doesn't exist in the
        # pages list, skip it rather than raise.
        pages = [{"page_id": "p1"}]
        edges = [{"source_page_id": "p1", "target_page_id": "ghost"}]
        g = build_igraph(pages=pages, edges=edges)
        assert g.ecount() == 0


class TestComputePagerank:
    def test_returns_dict_keyed_by_page_id(self):
        g = build_igraph(
            pages=[{"page_id": "a"}, {"page_id": "b"}],
            edges=[{"source_page_id": "a", "target_page_id": "b"}],
        )
        scores = compute_pagerank(g)
        assert set(scores.keys()) == {"a", "b"}
        assert all(isinstance(v, float) for v in scores.values())

    def test_scores_sum_to_approximately_one(self):
        # PageRank values form a probability distribution.
        g = build_igraph(
            pages=[{"page_id": f"p{i}"} for i in range(5)],
            edges=[
                {"source_page_id": "p0", "target_page_id": "p1"},
                {"source_page_id": "p1", "target_page_id": "p2"},
                {"source_page_id": "p2", "target_page_id": "p3"},
                {"source_page_id": "p3", "target_page_id": "p4"},
                {"source_page_id": "p4", "target_page_id": "p0"},
            ],
        )
        scores = compute_pagerank(g)
        assert math.isclose(sum(scores.values()), 1.0, abs_tol=1e-6)

    def test_authority_node_has_highest_score(self):
        # Star graph with B as the hub everyone points to.
        # B should rank highest.
        pages = [{"page_id": p} for p in ["a", "b", "c", "d"]]
        edges = [
            {"source_page_id": "a", "target_page_id": "b"},
            {"source_page_id": "c", "target_page_id": "b"},
            {"source_page_id": "d", "target_page_id": "b"},
        ]
        g = build_igraph(pages=pages, edges=edges)
        scores = compute_pagerank(g)
        assert scores["b"] == max(scores.values())

    def test_empty_graph_returns_empty_dict(self):
        g = build_igraph(pages=[], edges=[])
        assert compute_pagerank(g) == {}

    def test_damping_factor_accepted(self):
        g = build_igraph(
            pages=[{"page_id": "a"}, {"page_id": "b"}],
            edges=[{"source_page_id": "a", "target_page_id": "b"}],
        )
        # Different damping should produce different (still valid) scores.
        s1 = compute_pagerank(g, damping=0.85)
        s2 = compute_pagerank(g, damping=0.5)
        assert s1 != s2
        assert math.isclose(sum(s2.values()), 1.0, abs_tol=1e-6)


class TestComputeCommunities:
    def test_returns_dict_keyed_by_page_id_with_int_values(self):
        # Two well-separated triangles → two communities expected.
        pages = [{"page_id": p} for p in
                 ["a", "b", "c", "x", "y", "z"]]
        edges = [
            {"source_page_id": "a", "target_page_id": "b"},
            {"source_page_id": "b", "target_page_id": "c"},
            {"source_page_id": "c", "target_page_id": "a"},
            {"source_page_id": "x", "target_page_id": "y"},
            {"source_page_id": "y", "target_page_id": "z"},
            {"source_page_id": "z", "target_page_id": "x"},
        ]
        g = build_igraph(pages=pages, edges=edges)
        comms = compute_communities(g)
        assert set(comms.keys()) == set(p["page_id"] for p in pages)
        assert all(isinstance(v, int) for v in comms.values())

    def test_two_disjoint_clusters_get_different_community_ids(self):
        pages = [{"page_id": p} for p in
                 ["a", "b", "c", "x", "y", "z"]]
        edges = [
            {"source_page_id": "a", "target_page_id": "b"},
            {"source_page_id": "b", "target_page_id": "c"},
            {"source_page_id": "c", "target_page_id": "a"},
            {"source_page_id": "x", "target_page_id": "y"},
            {"source_page_id": "y", "target_page_id": "z"},
            {"source_page_id": "z", "target_page_id": "x"},
        ]
        g = build_igraph(pages=pages, edges=edges)
        comms = compute_communities(g)
        # a, b, c share one community; x, y, z share another; they differ.
        assert comms["a"] == comms["b"] == comms["c"]
        assert comms["x"] == comms["y"] == comms["z"]
        assert comms["a"] != comms["x"]

    def test_tiny_graph_returns_empty_dict(self):
        # Communities on <5 nodes is meaningless; skip + return empty.
        g = build_igraph(
            pages=[{"page_id": "a"}, {"page_id": "b"}],
            edges=[{"source_page_id": "a", "target_page_id": "b"}],
        )
        comms = compute_communities(g, min_nodes=5)
        assert comms == {}

    def test_empty_graph_returns_empty_dict(self):
        g = build_igraph(pages=[], edges=[])
        assert compute_communities(g) == {}


class TestRrfFuse:
    def test_single_ranker_preserves_order(self):
        rankings = [["a", "b", "c"]]
        scores = rrf_fuse(rankings, k=60)
        # In a single-ranker RRF, the ordering is preserved.
        ordered = sorted(scores, key=lambda x: -scores[x])
        assert ordered == ["a", "b", "c"]

    def test_two_rankers_agreement_promotes_consistent_winner(self):
        # Both rankers put 'a' first → a should rank highest.
        rankings = [["a", "b", "c"], ["a", "c", "b"]]
        scores = rrf_fuse(rankings, k=60)
        ordered = sorted(scores, key=lambda x: -scores[x])
        assert ordered[0] == "a"

    def test_rrf_math_matches_reference_formula(self):
        # Reference: score(d) = sum_over_rankers(1 / (k + rank))
        # Rank is 1-indexed.
        # For doc 'a' in ranker1: rank=1, contribution=1/(60+1)=1/61
        # For doc 'a' in ranker2: rank=1, contribution=1/(60+1)=1/61
        # Total for 'a' = 2/61
        rankings = [["a", "b"], ["a", "b"]]
        scores = rrf_fuse(rankings, k=60)
        assert math.isclose(scores["a"], 2 / 61, abs_tol=1e-9)
        assert math.isclose(scores["b"], 2 / 62, abs_tol=1e-9)

    def test_doc_in_only_one_ranker_still_scored(self):
        # 'c' appears only in ranker1; should still get a partial score.
        rankings = [["a", "b", "c"], ["a", "b"]]
        scores = rrf_fuse(rankings, k=60)
        assert "c" in scores
        assert scores["c"] > 0

    def test_empty_input_returns_empty(self):
        assert rrf_fuse([], k=60) == {}
        assert rrf_fuse([[], []], k=60) == {}

    def test_default_k_is_sixty(self):
        # Industry-standard default. Locks the contract so a future code
        # change doesn't silently drift the constant.
        scores = rrf_fuse([["a"]])  # using default k
        assert math.isclose(scores["a"], 1 / 61, abs_tol=1e-9)
