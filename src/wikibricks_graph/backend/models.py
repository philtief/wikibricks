"""Pydantic models for the graph API.

All `*Out` models are response-shapes — flat, stable, suitable for caching
and ETag computation. Keep them additive (never change existing field
semantics) so frontend types stay stable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LinkType = Literal["related", "cites", "extends", "contradicts", "supersedes"]
ProposedStatus = Literal["pending", "confirmed", "rejected"]


class NodeOut(BaseModel):
    """A wiki page as a graph node. id == path."""

    id: str = Field(description="path, e.g. 'topics/foo' or 'sessions/u/2026/.../sid'")
    label: str
    community_id: int | None = None
    hub_score: float | None = None
    page_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    in_degree: int
    out_degree: int


class EdgeOut(BaseModel):
    """A typed edge in the `links` table."""

    source: str
    target: str
    kind: LinkType | str = "related"
    weight: float = 1.0


class GraphOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    generated_at: datetime
    etag: str


class ProposedEdgeOut(BaseModel):
    """A row in the `edges_proposed` staging table."""

    proposal_id: str
    source_path: str
    target_path: str
    link_type: LinkType | str
    evidence: str
    confidence: float | None = None
    status: ProposedStatus | str = "pending"
