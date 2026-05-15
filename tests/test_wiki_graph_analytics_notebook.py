"""Drift-guard test for `notebooks/wiki_graph_analytics.py`.

Pure unit tests in `test_graph_logic.py` cover the deterministic helpers.
This file covers the wiring — parses-as-Python + the WikiClient surface
used by the notebook stays in sync with `WikiClient` itself (via spec_set).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wikibricks.client import WikiClient

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "wiki_graph_analytics.py"


class NotebookExit(Exception):
    def __init__(self, msg: str = "") -> None:
        super().__init__(msg)
        self.msg = msg


def _make_dbutils() -> MagicMock:
    dbutils = MagicMock()
    dbutils.widgets.get.return_value = ""

    def _exit(msg: str = "") -> None:
        raise NotebookExit(msg)

    dbutils.notebook.exit.side_effect = _exit
    return dbutils


def _make_ws_with(pages_rows: list[dict], edges_rows: list[dict]) -> MagicMock:
    ws = MagicMock()

    def exec_statement(**kwargs):
        sql = kwargs.get("statement", "")
        if "FROM " in sql and "pages" in sql.lower() and "links" not in sql.lower():
            rows_data = pages_rows
        elif "links" in sql.lower():
            rows_data = edges_rows
        else:
            rows_data = []
        resp = MagicMock()
        if not rows_data:
            resp.result.data_array = []
            return resp
        cols = list(rows_data[0].keys())
        resp.result.data_array = [[r[c] for c in cols] for r in rows_data]
        col_mocks = []
        for c in cols:
            m = MagicMock()
            m.name = c
            col_mocks.append(m)
        resp.manifest.schema.columns = col_mocks
        return resp

    ws.statement_execution.execute_statement.side_effect = exec_statement
    return ws


def _exec_notebook(ws: MagicMock, wiki: MagicMock, dbutils: MagicMock) -> dict:
    src = NOTEBOOK_PATH.read_text()
    ns = {"__name__": "wiki_graph_analytics_notebook",
          "__file__": str(NOTEBOOK_PATH), "dbutils": dbutils}
    with (
        patch("databricks.sdk.WorkspaceClient", return_value=ws),
        patch("wikibricks.WikiClient", return_value=wiki),
    ):
        try:
            exec(compile(src, str(NOTEBOOK_PATH), "exec"), ns)
        except NotebookExit:
            pass
    return ns


class TestNotebookSource:
    @classmethod
    def setup_class(cls) -> None:
        cls.src = NOTEBOOK_PATH.read_text()

    def test_parses_as_python(self):
        compile(self.src, str(NOTEBOOK_PATH), "exec")

    def test_imports_graph_logic_helpers(self):
        for name in ("build_igraph", "compute_pagerank", "compute_communities"):
            assert name in self.src, f"notebook must import {name}"

    def test_filters_links_to_currently_valid(self):
        # Bi-temporal correctness: PageRank must run on the currently-valid
        # subgraph only. Historical (closed) edges would skew the authority.
        assert "valid_until IS NULL" in self.src

    def test_calls_update_graph_scores(self):
        assert "update_graph_scores" in self.src

    def test_logs_graph_analytics_op_type(self):
        assert "graph_analytics" in self.src


class TestNotebookWiring:
    def test_no_pages_short_circuits(self):
        ws = _make_ws_with([], [])
        wiki = MagicMock(spec_set=WikiClient)
        dbutils = _make_dbutils()
        _exec_notebook(ws, wiki, dbutils)
        wiki.update_graph_scores.assert_not_called()

    def test_happy_path_calls_update_graph_scores(self):
        # 6 pages, two disjoint triangles. Communities should split, all
        # pages should get a hub_score.
        pages = [{"page_id": p} for p in ["a", "b", "c", "x", "y", "z"]]
        edges = [
            {"source_page_id": "a", "target_page_id": "b"},
            {"source_page_id": "b", "target_page_id": "c"},
            {"source_page_id": "c", "target_page_id": "a"},
            {"source_page_id": "x", "target_page_id": "y"},
            {"source_page_id": "y", "target_page_id": "z"},
            {"source_page_id": "z", "target_page_id": "x"},
        ]
        ws = _make_ws_with(pages, edges)
        wiki = MagicMock(spec_set=WikiClient)
        dbutils = _make_dbutils()
        _exec_notebook(ws, wiki, dbutils)

        wiki.update_graph_scores.assert_called_once()
        scores = wiki.update_graph_scores.call_args[0][0]
        assert len(scores) == 6
        assert all("hub_score" in s and "community_id" in s for s in scores)
        # Communities split — at least 2 distinct community_ids.
        comm_ids = {s["community_id"] for s in scores if s["community_id"] is not None}
        assert len(comm_ids) >= 2

        # An auto_tag-style log event landed
        log_call = wiki._log.call_args
        assert log_call.args[0] == "graph_analytics"
