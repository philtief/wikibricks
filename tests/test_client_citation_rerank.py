"""Tests for the citation-aware search reranker on WikiClient."""

from __future__ import annotations

from unittest.mock import MagicMock

from wikibricks.client import WikiClient


def _client_with_exec(exec_return):
    ws = MagicMock()
    c = WikiClient(warehouse_id="w", workspace_client=ws)
    c._exec = MagicMock(return_value=exec_return)
    return c


def _exec_rows(rows):
    """Build a fake _exec response with the given (path, count) rows."""
    resp = MagicMock()
    resp.result.data_array = rows
    return resp


def test_fetch_citation_counts_empty_paths():
    c = _client_with_exec(None)
    assert c._fetch_citation_counts([]) == {}
    c._exec.assert_not_called()


def test_fetch_citation_counts_returns_dict():
    c = _client_with_exec(_exec_rows([("sessions/abc", 5), ("topics/solvd", 2)]))
    counts = c._fetch_citation_counts(["sessions/abc", "topics/solvd", "topics/x"])
    assert counts == {"sessions/abc": 5, "topics/solvd": 2}


def test_fetch_citation_counts_handles_empty_result():
    resp = MagicMock()
    resp.result.data_array = None
    c = _client_with_exec(resp)
    assert c._fetch_citation_counts(["sessions/x"]) == {}


def test_rerank_promotes_cited_path():
    """A bottom-ranked but heavily-cited path should move up."""
    c = _client_with_exec(_exec_rows([("sessions/cited", 50)]))
    hits = [
        {"path": "sessions/never-cited-1", "title": "A"},
        {"path": "sessions/never-cited-2", "title": "B"},
        {"path": "sessions/cited", "title": "C"},
    ]
    out = c._rerank_by_citations(hits)
    assert out[0]["path"] == "sessions/cited"


def test_rerank_preserves_order_when_no_citations():
    c = _client_with_exec(_exec_rows([]))
    hits = [{"path": "a"}, {"path": "b"}, {"path": "c"}]
    out = c._rerank_by_citations(hits)
    assert [h["path"] for h in out] == ["a", "b", "c"]


def test_rerank_handles_empty_hits():
    c = _client_with_exec(None)
    assert c._rerank_by_citations([]) == []


def test_rerank_handles_hits_missing_path():
    c = _client_with_exec(_exec_rows([]))
    hits = [{"title": "no path"}, {"path": "a"}]
    out = c._rerank_by_citations(hits)
    assert len(out) == 2


def _isolated_search(c, *args, **kwargs):
    """Run search with PageRank rerank disabled so citation-rerank tests
    isolate the citation path (v0.7.5 made PageRank rerank the default)."""
    kwargs.setdefault("rerank_with_pagerank", False)
    return c.search(*args, **kwargs)


def test_citation_rerank_off_by_default():
    """Without the env var and without the explicit flag, _rerank_by_citations
    is not called. (Renamed from test_search_does_not_rerank_by_default —
    v0.7.5 made PageRank rerank the default; this test now covers citations
    specifically.)
    """
    c = WikiClient(warehouse_id="w", workspace_client=MagicMock())
    fake = MagicMock()
    fake.result.data_array = [["id1", "p1", "T", "x", "txt", [], 1]]
    fake.manifest.columns = [
        MagicMock(name=n)
        for n in ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]
    ]
    for col, name in zip(fake.manifest.columns,
                         ["page_id", "path", "title", "page_type",
                          "content_text", "tags", "version"]):
        col.name = name
    c.ws.vector_search_indexes.query_index.return_value = fake
    c._log = MagicMock()
    c._rerank_by_citations = MagicMock(side_effect=lambda h: h)
    _isolated_search(c, "q")
    c._rerank_by_citations.assert_not_called()


def test_search_reranks_when_explicit_flag(monkeypatch):
    monkeypatch.delenv("WIKIBRICKS_RERANK_BY_CITATIONS", raising=False)
    c = WikiClient(warehouse_id="w", workspace_client=MagicMock())
    fake = MagicMock()
    fake.result.data_array = [["id1", "p1", "T", "x", "txt", [], 1]]
    cols = ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]
    fake.manifest.columns = [MagicMock() for _ in cols]
    for col, name in zip(fake.manifest.columns, cols):
        col.name = name
    c.ws.vector_search_indexes.query_index.return_value = fake
    c._log = MagicMock()
    c._rerank_by_citations = MagicMock(side_effect=lambda h: h)
    _isolated_search(c, "q", rerank_by_citations=True)
    c._rerank_by_citations.assert_called_once()


def test_search_reranks_when_env_var_set(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_RERANK_BY_CITATIONS", "1")
    c = WikiClient(warehouse_id="w", workspace_client=MagicMock())
    fake = MagicMock()
    fake.result.data_array = [["id1", "p1", "T", "x", "txt", [], 1]]
    cols = ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]
    fake.manifest.columns = [MagicMock() for _ in cols]
    for col, name in zip(fake.manifest.columns, cols):
        col.name = name
    c.ws.vector_search_indexes.query_index.return_value = fake
    c._log = MagicMock()
    c._rerank_by_citations = MagicMock(side_effect=lambda h: h)
    _isolated_search(c, "q")
    c._rerank_by_citations.assert_called_once()
