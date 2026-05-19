"""Pure helpers for graph analytics over the wikibricks links table.

The `notebooks/wiki_graph_analytics.py` job calls these to compute hub-scores
(PageRank) and communities (Leiden) and write them back to `pages`. The
`WikiClient.search` rerank uses `rrf_fuse` to blend vector-search rank and
PageRank rank into a single ordering.

Pure, LLM-free, deterministic. Tested without a Databricks workspace.

Why these specific algorithm choices:
- **PageRank**: directional authority signal. `cites` and other directed
  edges flow authority in one direction. Use igraph's default PRPACK
  implementation with damping=0.85.
- **Leiden** over Louvain: Leiden guarantees well-connected communities and
  is faster. Requires undirected input — we collapse the directed graph
  before community detection. PageRank stays on the directed graph.
- **Reciprocal Rank Fusion (k=60)** for the search rerank: industry-standard
  hybrid-search fusion in 2026, robust to score-scale differences between
  vector similarity and PageRank values.
"""

from collections.abc import Iterable

try:
    import igraph as ig
except ImportError as e:
    raise ImportError(
        "graph_logic requires `igraph>=0.11`. Install with `uv pip install igraph`."
    ) from e

DEFAULT_DAMPING = 0.85
DEFAULT_RRF_K = 60
DEFAULT_COMMUNITY_MIN_NODES = 5


def build_igraph(pages: Iterable[dict], edges: Iterable[dict]) -> ig.Graph:
    """Build a directed igraph Graph from page + edge dicts.

    `pages`: iterable of dicts each with a `page_id` key.
    `edges`: iterable of dicts each with `source_page_id` and `target_page_id`.

    Edges referencing unknown page_ids are dropped (defensive — a stale edge
    after a fix_broken_links lag shouldn't crash the curate job).
    """
    page_list = list(pages)
    edge_list = list(edges)

    if not page_list:
        return ig.Graph(directed=True)

    page_ids = [p["page_id"] for p in page_list]
    id_set = set(page_ids)
    idx_of = {pid: i for i, pid in enumerate(page_ids)}

    valid_edges: list[tuple[int, int]] = []
    for e in edge_list:
        src = e.get("source_page_id")
        tgt = e.get("target_page_id")
        if src in id_set and tgt in id_set:
            valid_edges.append((idx_of[src], idx_of[tgt]))

    g = ig.Graph(n=len(page_ids), edges=valid_edges, directed=True)
    g.vs["name"] = page_ids
    return g


def compute_pagerank(g: ig.Graph, damping: float = DEFAULT_DAMPING) -> dict[str, float]:
    """Return `{page_id: pagerank_score}` over the directed graph.

    Empty graph → empty dict. Otherwise igraph's PRPACK implementation is used
    (most stable + fastest for all but trivially small graphs). Scores form a
    probability distribution and sum to 1.0.
    """
    if g.vcount() == 0:
        return {}
    scores = g.pagerank(damping=damping)
    return dict(zip(g.vs["name"], scores))


def compute_communities(
    g: ig.Graph, min_nodes: int = DEFAULT_COMMUNITY_MIN_NODES,
) -> dict[str, int]:
    """Return `{page_id: community_id}` via Leiden on the undirected projection.

    Leiden requires undirected input — we collapse directed edges (both A→B
    and B→A treated as a single A↔B edge). For graphs with fewer than
    `min_nodes` vertices, returns an empty dict (community detection is not
    informative at that scale).
    """
    if g.vcount() < min_nodes:
        return {}
    undirected = g.as_undirected(mode="collapse")
    partition = undirected.community_leiden(objective_function="modularity")
    membership = partition.membership  # list[int], one per vertex
    return dict(zip(g.vs["name"], membership))


def rrf_fuse(
    rankings: list[list[str]], k: int = DEFAULT_RRF_K,
) -> dict[str, float]:
    """Reciprocal Rank Fusion across multiple rankings.

    Each ranking is a list of document IDs in best-first order. RRF score for
    document `d` is `sum_over_rankers(1 / (k + rank_d))`, where `rank_d` is
    the 1-indexed position in the ranker (documents missing from a ranker
    contribute 0). Higher score = more relevant. k=60 is the published
    standard (Cormack et al. 2009; widely cited as the robust default).

    Used by `WikiClient.search(rerank_with_pagerank=True)` to blend vector
    similarity rank and PageRank rank into a single ordering. RRF is robust
    to score-scale differences — no normalization step needed.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank_zero_indexed, doc_id in enumerate(ranking):
            rank = rank_zero_indexed + 1  # RRF is 1-indexed
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores
