from datetime import datetime, timezone

import pytest

from backend.models import (
    EdgeOut,
    GraphOut,
    NodeOut,
    ProposedEdgeOut,
)


def test_node_out_minimal_fields():
    n = NodeOut(id="topics/foo", label="Foo", in_degree=0, out_degree=0)
    assert n.id == "topics/foo"
    assert n.community_id is None
    assert n.hub_score is None


def test_node_out_full_fields():
    n = NodeOut(
        id="topics/foo", label="Foo",
        community_id=32, hub_score=0.42,
        page_type="concept", tags=["topic:foo"],
        in_degree=3, out_degree=2,
    )
    assert n.community_id == 32
    assert n.hub_score == pytest.approx(0.42)
    assert n.tags == ["topic:foo"]


def test_edge_out_default_weight():
    e = EdgeOut(source="a", target="b", kind="related")
    assert e.weight == 1.0


def test_graph_out_carries_etag():
    g = GraphOut(
        nodes=[NodeOut(id="a", label="A", in_degree=0, out_degree=0)],
        edges=[],
        generated_at=datetime.now(timezone.utc),
        etag="abc123",
    )
    assert g.etag == "abc123"
    assert len(g.nodes) == 1


def test_proposed_edge_out_carries_evidence():
    p = ProposedEdgeOut(
        proposal_id="p1",
        source_path="a", target_path="b",
        link_type="cites", evidence="example evidence",
        confidence=0.75, status="pending",
    )
    assert p.status == "pending"
    assert p.evidence == "example evidence"
