from unittest.mock import MagicMock

from databricks.sdk.service.sql import StatementState, StatementStatus

from backend.services import proposed_edges


def _ok(rows):
    resp = MagicMock()
    resp.status = StatementStatus(state=StatementState.SUCCEEDED, error=None)
    resp.result.data_array = rows
    return resp


def test_list_pending_returns_rows():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([
        ["p1", "a", "b", "cites", "evidence here", 0.7, "pending"],
    ])
    rows = proposed_edges.list_pending(ws, warehouse_id="w", catalog="c", schema="s")
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == "p1"
    assert rows[0]["status"] == "pending"
    assert rows[0]["confidence"] == 0.7


def test_list_pending_handles_null_evidence_and_confidence():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([
        ["p1", "a", "b", "related", None, None, "pending"],
    ])
    rows = proposed_edges.list_pending(ws, warehouse_id="w", catalog="c", schema="s")
    assert rows[0]["evidence"] == ""
    assert rows[0]["confidence"] is None


def test_approve_emits_update_statement():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([])
    proposed_edges.approve(ws, warehouse_id="w", catalog="c", schema="s", proposal_id="p1")
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "UPDATE" in sql
    assert "edges_proposed" in sql
    assert "status = 'confirmed'" in sql
    assert "'p1'" in sql


def test_reject_emits_update_with_reason():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([])
    proposed_edges.reject(
        ws, warehouse_id="w", catalog="c", schema="s",
        proposal_id="p1", reason="user-rejected",
    )
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "status = 'rejected'" in sql
    assert "user-rejected" in sql
    assert "'p1'" in sql


def test_proposal_id_is_sql_escaped():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([])
    proposed_edges.approve(
        ws, warehouse_id="w", catalog="c", schema="s",
        proposal_id="it's-malicious",
    )
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "it\\'s-malicious" in sql


def test_reject_reason_is_sql_escaped():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([])
    proposed_edges.reject(
        ws, warehouse_id="w", catalog="c", schema="s",
        proposal_id="p1", reason="it's bad",
    )
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "it\\'s bad" in sql


def test_failure_raises_runtime_error():
    ws = MagicMock()
    resp = MagicMock()
    resp.status = StatementStatus(state=StatementState.FAILED, error="boom")
    ws.statement_execution.execute_statement.return_value = resp
    try:
        proposed_edges.approve(ws, warehouse_id="w", catalog="c", schema="s", proposal_id="p1")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e) or "SQL" in str(e)
