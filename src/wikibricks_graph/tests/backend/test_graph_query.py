from unittest.mock import MagicMock

from databricks.sdk.service.sql import StatementState, StatementStatus

from backend.services import graph_query


def _mock_resp(rows):
    resp = MagicMock()
    resp.status = StatementStatus(state=StatementState.SUCCEEDED, error=None)
    resp.result.data_array = rows
    return resp


def test_nodes_sql_hides_chunks_by_default():
    sql = graph_query.build_nodes_sql(catalog="c", schema="s")
    assert "page_type != 'chunk'" in sql or "page_type <> 'chunk'" in sql
    assert "c.s.pages" in sql


def test_nodes_sql_include_chunks_when_requested():
    sql = graph_query.build_nodes_sql(catalog="c", schema="s", include_chunks=True)
    assert "page_type != 'chunk'" not in sql
    assert "page_type <> 'chunk'" not in sql


def test_edges_sql_joins_links_to_pages_for_paths():
    sql = graph_query.build_edges_sql(catalog="c", schema="s")
    # edges are returned by source/target path, not page_id
    assert "src.path" in sql
    assert "tgt.path" in sql
    assert "c.s.links" in sql


def test_edges_sql_filters_currently_valid_only():
    sql = graph_query.build_edges_sql(catalog="c", schema="s")
    # bi-temporal links: only currently-valid (valid_until IS NULL OR > now)
    assert "valid_until" in sql


def test_fetch_graph_returns_nodes_and_edges():
    ws = MagicMock()
    ws.statement_execution.execute_statement.side_effect = [
        _mock_resp([
            # rows: path, title, community_id, hub_score, page_type, tags_str, out_deg, in_deg
            ["topics/foo", "Foo", 32, 0.42, "concept", "topic:foo", 1, 2],
            ["topics/bar", "Bar", 32, 0.18, "concept", "", 0, 0],
        ]),
        _mock_resp([
            # rows: source_path, target_path, link_type, confidence
            ["topics/foo", "topics/bar", "cites", 0.9],
        ]),
    ]
    graph = graph_query.fetch_graph(ws, warehouse_id="w", catalog="c", schema="s")
    assert len(graph["nodes"]) == 2
    assert graph["nodes"][0]["id"] == "topics/foo"
    assert graph["nodes"][0]["community_id"] == 32
    assert graph["nodes"][0]["tags"] == ["topic:foo"]
    assert graph["nodes"][0]["in_degree"] == 2
    assert graph["nodes"][0]["out_degree"] == 1
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["kind"] == "cites"
    assert graph["edges"][0]["weight"] == 0.9


def test_fetch_graph_propagates_sql_failure():
    ws = MagicMock()
    resp = MagicMock()
    resp.status = StatementStatus(
        state=StatementState.FAILED, error="bad sql"
    )
    ws.statement_execution.execute_statement.return_value = resp
    try:
        graph_query.fetch_graph(ws, warehouse_id="w", catalog="c", schema="s")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "bad sql" in str(e) or "SQL" in str(e)


def test_fetch_graph_handles_null_tags():
    """tags array might be NULL (empty string after array_join). Don't crash."""
    ws = MagicMock()
    ws.statement_execution.execute_statement.side_effect = [
        _mock_resp([
            ["topics/foo", "Foo", None, None, "concept", None, 0, 0],
        ]),
        _mock_resp([]),
    ]
    g = graph_query.fetch_graph(ws, warehouse_id="w", catalog="c", schema="s")
    assert g["nodes"][0]["tags"] == []
    assert g["nodes"][0]["community_id"] is None
    assert g["nodes"][0]["hub_score"] is None
