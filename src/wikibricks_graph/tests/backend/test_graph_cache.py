from unittest.mock import MagicMock

from backend.services import graph_cache


def test_get_or_fetch_caches_first_call():
    fetcher = MagicMock(return_value={"nodes": [{"id": "a"}], "edges": []})
    cache = graph_cache.GraphCache(ttl_seconds=60)
    g1 = cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    g2 = cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    assert g1["etag"] == g2["etag"]
    assert fetcher.call_count == 1


def test_get_or_fetch_different_keys_separate_calls():
    fetcher = MagicMock(side_effect=[
        {"nodes": [{"id": "a"}], "edges": []},
        {"nodes": [{"id": "b"}], "edges": []},
    ])
    cache = graph_cache.GraphCache(ttl_seconds=60)
    g1 = cache.get_or_fetch(key=("c", "s1"), fetcher=fetcher)
    g2 = cache.get_or_fetch(key=("c", "s2"), fetcher=fetcher)
    assert g1["etag"] != g2["etag"]
    assert fetcher.call_count == 2


def test_etag_stable_for_same_content():
    cache = graph_cache.GraphCache(ttl_seconds=60)
    e1 = cache._compute_etag({"nodes": [{"id": "a"}], "edges": []})
    e2 = cache._compute_etag({"nodes": [{"id": "a"}], "edges": []})
    assert e1 == e2


def test_etag_differs_for_different_content():
    cache = graph_cache.GraphCache(ttl_seconds=60)
    e1 = cache._compute_etag({"nodes": [{"id": "a"}], "edges": []})
    e2 = cache._compute_etag({"nodes": [{"id": "b"}], "edges": []})
    assert e1 != e2


def test_invalidate_forces_refetch():
    fetcher = MagicMock(return_value={"nodes": [], "edges": []})
    cache = graph_cache.GraphCache(ttl_seconds=60)
    cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    cache.invalidate(key=("c", "s"))
    cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    assert fetcher.call_count == 2


def test_returned_snapshot_carries_generated_at():
    fetcher = MagicMock(return_value={"nodes": [], "edges": []})
    cache = graph_cache.GraphCache(ttl_seconds=60)
    g = cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    assert "generated_at" in g
    # iso string
    assert isinstance(g["generated_at"], str)
    assert "T" in g["generated_at"]


def test_concurrent_cold_fetches_call_fetcher_once():
    """When two threads hit a cold cache simultaneously, only one
    fetcher call should land — the other waits on the lock and
    reads the cached value."""
    import threading
    import time

    call_count = {"n": 0}

    def slow_fetcher():
        call_count["n"] += 1
        time.sleep(0.05)  # simulate a slow SQL query
        return {"nodes": [{"id": "a"}], "edges": []}

    cache = graph_cache.GraphCache(ttl_seconds=60)
    results = []
    threads = []
    def worker():
        results.append(cache.get_or_fetch(key=("c", "s"), fetcher=slow_fetcher))
    for _ in range(5):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    # All 5 threads got a result; only ONE fetcher call should have run.
    assert len(results) == 5
    assert call_count["n"] == 1
    # All results should be the same cached snapshot
    etags = {r["etag"] for r in results}
    assert len(etags) == 1
