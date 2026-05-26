"""API router. All endpoints depend on `get_user_ws` + `get_app_config`."""

from __future__ import annotations

from typing import Any

from databricks.sdk.service.sql import StatementState
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel

from backend import core
from backend.core import get_user_ws
from backend.models import EdgeOut, GraphOut, NodeOut, ProposedEdgeOut
from backend.services import graph_query, proposed_edges

router = APIRouter(prefix="/api")


class _RejectBody(BaseModel):
    reason: str = "user-rejected"


@router.get("/graph", response_model=GraphOut)
def get_graph(
    response: Response,
    include_chunks: bool = False,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    ws=Depends(get_user_ws),
):
    cfg = core.get_app_config()
    cache = core.get_graph_cache()

    def _fetch():
        return graph_query.fetch_graph(
            ws,
            warehouse_id=cfg["warehouse_id"],
            catalog=cfg["catalog"],
            schema=cfg["schema"],
            include_chunks=include_chunks,
        )

    snapshot = cache.get_or_fetch(
        key=(cfg["catalog"], cfg["schema"], include_chunks),
        fetcher=_fetch,
    )
    if if_none_match and if_none_match == snapshot["etag"]:
        return Response(status_code=304)
    response.headers["ETag"] = snapshot["etag"]
    return GraphOut(
        nodes=[NodeOut(**n) for n in snapshot["nodes"]],
        edges=[EdgeOut(**e) for e in snapshot["edges"]],
        generated_at=snapshot["generated_at"],
        etag=snapshot["etag"],
    )


@router.post("/graph/refresh", status_code=204)
def refresh_graph(ws=Depends(get_user_ws)):
    cfg = core.get_app_config()
    cache = core.get_graph_cache()
    cache.invalidate(key=(cfg["catalog"], cfg["schema"], False))
    cache.invalidate(key=(cfg["catalog"], cfg["schema"], True))
    return Response(status_code=204)


@router.get("/pages/{path:path}")
def get_page(path: str, ws=Depends(get_user_ws)) -> dict[str, Any]:
    cfg = core.get_app_config()

    def _esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace("'", "\\'")

    sql = (
        f"SELECT p.title, p.page_type, array_join(p.tags, ',') AS tags_str, "
        f"p.content:summary::STRING AS summary, "
        f"p.content:body::STRING AS body, "
        f"p.community_id, p.hub_score "
        f"FROM {cfg['catalog']}.{cfg['schema']}.pages p "
        f"WHERE p.path = '{_esc(path)}' LIMIT 1"
    )
    resp = ws.statement_execution.execute_statement(
        warehouse_id=cfg["warehouse_id"], statement=sql, wait_timeout="30s",
    )
    if resp.status.state != StatementState.SUCCEEDED:
        raise HTTPException(status_code=500, detail=str(resp.status.error or "SQL failed"))
    rows = resp.result.data_array or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"page not found: {path}")
    title, page_type, tags_str, summary, body, community_id, hub_score = rows[0]
    return {
        "path": path,
        "title": title or "",
        "page_type": page_type,
        "tags": [t for t in (tags_str or "").split(",") if t],
        "summary": summary or "",
        "body": body or "",
        "community_id": int(community_id) if community_id is not None else None,
        "hub_score": float(hub_score) if hub_score is not None else None,
    }


@router.get("/edges/proposed", response_model=list[ProposedEdgeOut])
def list_proposed(ws=Depends(get_user_ws)) -> list[ProposedEdgeOut]:
    cfg = core.get_app_config()
    rows = proposed_edges.list_pending(
        ws,
        warehouse_id=cfg["warehouse_id"],
        catalog=cfg["catalog"],
        schema=cfg["schema"],
    )
    return [ProposedEdgeOut(**r) for r in rows]


@router.post("/edges/proposed/{proposal_id}/approve", status_code=204)
def approve_edge(proposal_id: str, ws=Depends(get_user_ws)):
    cfg = core.get_app_config()
    proposed_edges.approve(
        ws,
        warehouse_id=cfg["warehouse_id"],
        catalog=cfg["catalog"],
        schema=cfg["schema"],
        proposal_id=proposal_id,
    )
    return Response(status_code=204)


@router.post("/edges/proposed/{proposal_id}/reject", status_code=204)
def reject_edge(proposal_id: str, body: _RejectBody, ws=Depends(get_user_ws)):
    cfg = core.get_app_config()
    proposed_edges.reject(
        ws,
        warehouse_id=cfg["warehouse_id"],
        catalog=cfg["catalog"],
        schema=cfg["schema"],
        proposal_id=proposal_id,
        reason=body.reason,
    )
    return Response(status_code=204)
