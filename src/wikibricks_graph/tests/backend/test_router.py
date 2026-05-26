from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend import core
from backend.app import create_app


@pytest.fixture
def client(monkeypatch):
    # Stub the WorkspaceClient dependency
    fake_ws = MagicMock()

    def _ws_factory():
        return fake_ws

    # Stub the app config so it doesn't need env vars
    monkeypatch.setattr(core, "get_app_config", lambda: {
        "catalog": "c", "schema": "s", "warehouse_id": "w",
    })

    # Replace the cache singleton each test so state doesn't leak
    from backend.services.graph_cache import GraphCache
    monkeypatch.setattr(core, "_graph_cache", GraphCache(ttl_seconds=60))

    app = create_app()
    app.dependency_overrides[core.get_user_ws] = _ws_factory
    client = TestClient(app)
    client._fake_ws = fake_ws  # type: ignore[attr-defined]
    return client


def test_api_graph_returns_graph_out_shape(client, monkeypatch):
    from backend.services import graph_query
    monkeypatch.setattr(
        graph_query, "fetch_graph",
        lambda *a, **kw: {
            "nodes": [{"id": "topics/foo", "label": "Foo",
                       "community_id": 1, "hub_score": 0.5,
                       "page_type": "concept", "tags": [],
                       "in_degree": 0, "out_degree": 0}],
            "edges": [],
        },
    )
    r = client.get("/api/graph")
    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data and "edges" in data
    assert "etag" in data
    assert data["nodes"][0]["id"] == "topics/foo"


def test_api_graph_returns_304_on_matching_etag(client, monkeypatch):
    from backend.services import graph_query
    monkeypatch.setattr(
        graph_query, "fetch_graph",
        lambda *a, **kw: {"nodes": [{"id": "a", "label": "A",
                                     "in_degree": 0, "out_degree": 0}], "edges": []},
    )
    r = client.get("/api/graph")
    etag = r.json()["etag"]
    r2 = client.get("/api/graph", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_api_graph_refresh_invalidates_cache(client, monkeypatch):
    from backend.services import graph_query
    call_count = {"n": 0}
    def fake_fetch(*a, **kw):
        call_count["n"] += 1
        return {"nodes": [{"id": f"n{call_count['n']}", "label": "x",
                           "in_degree": 0, "out_degree": 0}], "edges": []}
    monkeypatch.setattr(graph_query, "fetch_graph", fake_fetch)
    client.get("/api/graph")
    client.get("/api/graph")
    assert call_count["n"] == 1  # cached
    r = client.post("/api/graph/refresh")
    assert r.status_code == 204
    client.get("/api/graph")
    assert call_count["n"] == 2  # refetched


def test_api_pages_returns_detail(client, monkeypatch):
    # /api/pages/{path:path} — supports paths with slashes
    fake_resp = MagicMock()
    fake_resp.status.state.name = "SUCCEEDED"
    # row: title, page_type, tags_str, summary, body, community_id, hub_score
    fake_resp.result.data_array = [
        ["My Title", "concept", "topic:foo,domain:test",
         "summary text", "body text", 32, 0.5],
    ]
    fake_resp.status.error = None
    from databricks.sdk.service.sql import StatementState, StatementStatus
    fake_resp.status = StatementStatus(state=StatementState.SUCCEEDED, error=None)
    client._fake_ws.statement_execution.execute_statement.return_value = fake_resp

    r = client.get("/api/pages/topics/foo")
    assert r.status_code == 200
    data = r.json()
    assert data["path"] == "topics/foo"
    assert data["title"] == "My Title"
    assert "topic:foo" in data["tags"]


def test_api_pages_returns_404_when_missing(client):
    fake_resp = MagicMock()
    from databricks.sdk.service.sql import StatementState, StatementStatus
    fake_resp.status = StatementStatus(state=StatementState.SUCCEEDED, error=None)
    fake_resp.result.data_array = []
    client._fake_ws.statement_execution.execute_statement.return_value = fake_resp
    r = client.get("/api/pages/does/not/exist")
    assert r.status_code == 404


def test_api_edges_proposed_list(client, monkeypatch):
    from backend.services import proposed_edges
    monkeypatch.setattr(
        proposed_edges, "list_pending",
        lambda *a, **kw: [{"proposal_id": "p1", "source_path": "a",
                           "target_path": "b", "link_type": "cites",
                           "evidence": "ev", "confidence": 0.7, "status": "pending"}],
    )
    r = client.get("/api/edges/proposed")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_api_edges_proposed_approve(client, monkeypatch):
    from backend.services import proposed_edges
    called = {}
    def fake_approve(ws, *, warehouse_id, catalog, schema, proposal_id):
        called["proposal_id"] = proposal_id
    monkeypatch.setattr(proposed_edges, "approve", fake_approve)
    r = client.post("/api/edges/proposed/p1/approve")
    assert r.status_code == 204
    assert called["proposal_id"] == "p1"


def test_api_edges_proposed_reject(client, monkeypatch):
    from backend.services import proposed_edges
    called = {}
    def fake_reject(ws, *, warehouse_id, catalog, schema, proposal_id, reason="user-rejected"):
        called["proposal_id"] = proposal_id
        called["reason"] = reason
    monkeypatch.setattr(proposed_edges, "reject", fake_reject)
    r = client.post("/api/edges/proposed/p1/reject", json={"reason": "bad-target"})
    assert r.status_code == 204
    assert called["proposal_id"] == "p1"
    assert called["reason"] == "bad-target"
