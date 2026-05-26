"""CRUD for the edges_proposed staging table.

`list_pending` returns rows ready for review. `approve` / `reject`
flip status with proper SQL escaping. The nightly `promote_edges`
notebook in the main repo picks up confirmed rows.
"""

from __future__ import annotations

from typing import Any

from databricks.sdk.service.sql import StatementState


def _esc(s: str) -> str:
    """Escape backslashes first, then single quotes — same convention as
    the main library's ops.py escape helper."""
    return (s or "").replace("\\", "\\\\").replace("'", "\\'")


def _exec(ws, warehouse_id: str, sql: str):
    resp = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=sql, wait_timeout="30s"
    )
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL execution failed: {getattr(resp.status, 'error', '')}")
    return resp.result.data_array or []


def list_pending(ws, *, warehouse_id: str, catalog: str, schema: str) -> list[dict[str, Any]]:
    sql = f"""
SELECT proposal_id, source_path, target_path, link_type, evidence, confidence, status
FROM {catalog}.{schema}.edges_proposed
WHERE status = 'pending'
ORDER BY created_at DESC
LIMIT 500
""".strip()
    rows = _exec(ws, warehouse_id, sql)
    out = []
    for proposal_id, source_path, target_path, link_type, evidence, confidence, status in rows:
        out.append({
            "proposal_id": proposal_id,
            "source_path": source_path,
            "target_path": target_path,
            "link_type": link_type,
            "evidence": evidence or "",
            "confidence": float(confidence) if confidence is not None else None,
            "status": status,
        })
    return out


def approve(ws, *, warehouse_id: str, catalog: str, schema: str, proposal_id: str) -> None:
    sql = (
        f"UPDATE {catalog}.{schema}.edges_proposed "
        f"SET status = 'confirmed' "
        f"WHERE proposal_id = '{_esc(proposal_id)}'"
    )
    _exec(ws, warehouse_id, sql)


def reject(
    ws, *, warehouse_id: str, catalog: str, schema: str,
    proposal_id: str, reason: str = "user-rejected",
) -> None:
    sql = (
        f"UPDATE {catalog}.{schema}.edges_proposed "
        f"SET status = 'rejected', "
        f"evidence = concat(coalesce(evidence, ''), ' [rejected: {_esc(reason)}]') "
        f"WHERE proposal_id = '{_esc(proposal_id)}'"
    )
    _exec(ws, warehouse_id, sql)
