"""v0.7.5 — PageRank RRF rerank is the default for WikiClient.search.

Replaces the v0.7.4 semantics where rerank_with_pagerank had to be opted
into. Opt-out via the WIKIBRICKS_DISABLE_PAGERANK_RERANK=1 env var.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from wikibricks.client import WikiClient


def _client_with_vs(hits: list[dict]):
    """Build a WikiClient with a vector_search_indexes.query_index that
    returns the given hits, and stub helpers we don't care about here.
    """
    ws = MagicMock()
    cols = ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]
    resp = MagicMock()
    resp.result.data_array = [[h[k] for k in cols] for h in hits]
    resp.manifest.columns = [MagicMock(name=c) for c in cols]
    for col, c in zip(resp.manifest.columns, cols):
        col.name = c
    ws.vector_search_indexes.query_index.return_value = resp

    c = WikiClient(warehouse_id="w", workspace_client=ws)
    c._log = MagicMock()
    c._exec = MagicMock()
    return c


def _hit(pid: str, path: str = None, tags: str = "") -> dict:
    return {
        "page_id": pid,
        "path": path or f"sessions/{pid}",
        "title": pid.title(),
        "page_type": "concept",
        "content_text": "",
        "tags": tags,
        "version": 1,
    }


def test_search_reranks_with_pagerank_by_default():
    """Default behaviour (no args, no env var) MUST invoke the PageRank
    RRF reranker — v0.7.5 promotion of the v0.7.4 opt-in path.
    """
    c = _client_with_vs([_hit("a"), _hit("b"), _hit("c")])
    c._rerank_by_rrf = MagicMock(side_effect=lambda h: h)
    c.search("q")
    c._rerank_by_rrf.assert_called_once()


def test_search_skips_rerank_when_env_disable_set(monkeypatch):
    """WIKIBRICKS_DISABLE_PAGERANK_RERANK=1 turns the default off so
    operators can A/B test without a code change.
    """
    monkeypatch.setenv("WIKIBRICKS_DISABLE_PAGERANK_RERANK", "1")
    c = _client_with_vs([_hit("a"), _hit("b")])
    c._rerank_by_rrf = MagicMock(side_effect=lambda h: h)
    c.search("q")
    c._rerank_by_rrf.assert_not_called()


def test_search_skips_rerank_when_explicitly_false():
    """Explicit `rerank_with_pagerank=False` still works as a per-call
    opt-out and overrides the (new) True default.
    """
    c = _client_with_vs([_hit("a"), _hit("b")])
    c._rerank_by_rrf = MagicMock(side_effect=lambda h: h)
    c.search("q", rerank_with_pagerank=False)
    c._rerank_by_rrf.assert_not_called()


def test_search_skips_rerank_when_empty_hits():
    """No VS hits → no rerank attempt (avoids spurious _exec call)."""
    c = _client_with_vs([])
    c._rerank_by_rrf = MagicMock(side_effect=lambda h: h)
    c.search("q")
    c._rerank_by_rrf.assert_not_called()
