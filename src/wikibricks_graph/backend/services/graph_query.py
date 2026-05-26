"""SQL builders + executor for the graph view.

`build_nodes_sql` and `build_edges_sql` are pure string builders (no
side effects, fully unit-testable). `fetch_graph` is the thin adapter
that executes both queries against a `WorkspaceClient` and shapes the
results into the `{nodes, edges}` dicts that `GraphOut` consumes.

Chunks are hidden by default (page_type != 'chunk'). Pass
`include_chunks=True` to include them. Edges respect bi-temporal
validity — only `valid_until IS NULL OR > now` rows are returned.
"""

from __future__ import annotations

from typing import Any

from databricks.sdk.service.sql import StatementState


def build_nodes_sql(*, catalog: str, schema: str, include_chunks: bool = False) -> str:
    chunk_filter = "" if include_chunks else "AND p.page_type != 'chunk'"
    return f"""
SELECT
    p.path AS id,
    p.title AS label,
    p.community_id,
    p.hub_score,
    p.page_type,
    array_join(p.tags, ',') AS tags_str,
    (SELECT count(*) FROM {catalog}.{schema}.links l
       JOIN {catalog}.{schema}.pages src ON src.page_id = l.source_page_id
      WHERE src.path = p.path
        AND (l.valid_until IS NULL OR l.valid_until > current_timestamp())
    ) AS out_deg,
    (SELECT count(*) FROM {catalog}.{schema}.links l
       JOIN {catalog}.{schema}.pages tgt ON tgt.page_id = l.target_page_id
      WHERE tgt.path = p.path
        AND (l.valid_until IS NULL OR l.valid_until > current_timestamp())
    ) AS in_deg
FROM {catalog}.{schema}.pages p
WHERE 1=1 {chunk_filter}
""".strip()


def build_edges_sql(*, catalog: str, schema: str) -> str:
    return f"""
SELECT
    src.path AS source_path,
    tgt.path AS target_path,
    l.link_type,
    coalesce(l.confidence, 1.0) AS confidence
FROM {catalog}.{schema}.links l
JOIN {catalog}.{schema}.pages src ON src.page_id = l.source_page_id
JOIN {catalog}.{schema}.pages tgt ON tgt.page_id = l.target_page_id
WHERE l.valid_until IS NULL OR l.valid_until > current_timestamp()
""".strip()


def _exec(ws, warehouse_id: str, sql: str):
    resp = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=sql, wait_timeout="30s"
    )
    if resp.status.state != StatementState.SUCCEEDED:
        msg = getattr(resp.status, "error", None) or "unknown"
        raise RuntimeError(f"SQL execution failed: {msg}")
    return resp.result.data_array or []


def fetch_graph(
    ws,
    *,
    warehouse_id: str,
    catalog: str,
    schema: str,
    include_chunks: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Pull current graph state. Returns {'nodes': [...], 'edges': [...]}."""
    nodes_rows = _exec(
        ws, warehouse_id,
        build_nodes_sql(catalog=catalog, schema=schema, include_chunks=include_chunks),
    )
    nodes = []
    for path, label, community_id, hub_score, page_type, tags_str, out_deg, in_deg in nodes_rows:
        nodes.append({
            "id": path,
            "label": (label or "")[:120],
            "community_id": int(community_id) if community_id is not None else None,
            "hub_score": float(hub_score) if hub_score is not None else None,
            "page_type": page_type,
            "tags": [t for t in (tags_str or "").split(",") if t],
            "in_degree": int(in_deg or 0),
            "out_degree": int(out_deg or 0),
        })
    edges_rows = _exec(
        ws, warehouse_id,
        build_edges_sql(catalog=catalog, schema=schema),
    )
    edges = []
    for source, target, kind, confidence in edges_rows:
        edges.append({
            "source": source,
            "target": target,
            "kind": kind or "related",
            "weight": float(confidence or 1.0),
        })
    return {"nodes": nodes, "edges": edges}
