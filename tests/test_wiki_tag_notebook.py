"""Drift-guard test for `notebooks/wiki_tag.py`.

Unit tests in `test_tag_logic.py` cover the deterministic helpers.
This file covers the wiring — that the notebook parses, imports the right
helpers, and calls the expected WikiClient methods.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wikibricks.client import WikiClient

NOTEBOOK_PATH = Path("notebooks/wiki_tag.py")


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


def _make_ws_with_pages(rows: list[dict]) -> MagicMock:
    """WorkspaceClient mock that returns `rows` for any statement_execution call.

    Also routes serving_endpoints.query to a stub JSON tag response.
    """
    ws = MagicMock()

    def exec_statement(**kwargs):
        resp = MagicMock()
        if not rows:
            resp.result.data_array = []
            return resp
        cols = list(rows[0].keys())
        resp.result.data_array = [[r[c] for c in cols] for r in rows]
        col_mocks = []
        for c in cols:
            m = MagicMock()
            m.name = c
            col_mocks.append(m)
        resp.manifest.schema.columns = col_mocks
        return resp

    ws.statement_execution.execute_statement.side_effect = exec_statement

    def query(**kwargs):
        resp = MagicMock()
        resp.choices = [
            MagicMock(
                message=MagicMock(content='{"tags": ["row-level-security", "delta-lake"]}')
            )
        ]
        return resp

    ws.serving_endpoints.query.side_effect = query
    return ws


def _exec_notebook(ws: MagicMock, wiki: MagicMock, dbutils: MagicMock) -> dict:
    src = NOTEBOOK_PATH.read_text()
    ns = {"__name__": "wiki_tag_notebook", "__file__": str(NOTEBOOK_PATH), "dbutils": dbutils}
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

    def test_imports_tag_logic_helpers(self):
        for name in ("parse_tag_response", "dedupe_against_vocabulary",
                     "prefix_llm", "build_tag_event"):
            assert name in self.src, f"notebook must import {name}"

    def test_imports_vocabulary_table_constant(self):
        assert "VOCABULARY_TABLE" in self.src

    def test_filters_out_segregate_and_promote_children(self):
        # New top-level pages produced by mechanical writes shouldn't pull tags.
        assert "'segregate', 'promote'" in self.src or '"segregate", "promote"' in self.src

    def test_filters_out_pages_already_llm_tagged(self):
        # Prevents re-tagging on every run.
        assert "t LIKE 'llm:%'" in self.src

    def test_uses_thread_pool_executor(self):
        assert "ThreadPoolExecutor" in self.src

    def test_logs_auto_tag_op_type(self):
        assert '"auto_tag"' in self.src or "'auto_tag'" in self.src


class TestNotebookWiring:
    """Exec the notebook against spec_set'd WikiClient + dbutils stubs.

    spec_set causes any typo or removed method to fail loudly.
    """

    def test_no_candidates_short_circuits(self):
        ws = _make_ws_with_pages([])
        wiki = MagicMock(spec_set=WikiClient)
        dbutils = _make_dbutils()
        _exec_notebook(ws, wiki, dbutils)
        # No candidates means the notebook exits via dbutils.notebook.exit.
        wiki.upsert_vocabulary.assert_not_called()
        wiki.append_page_tags.assert_not_called()

    def test_happy_path_calls_upsert_and_append(self):
        # One candidate, fresh vocab.
        pages = [{
            "page_id": "p1", "path": "topics/foo",
            "title": "Row level security", "content_text": "About RLS.",
        }]

        ws = MagicMock()
        call_log = []

        def exec_statement(**kwargs):
            call_log.append(kwargs.get("statement", ""))
            sql = kwargs.get("statement", "")
            resp = MagicMock()
            if "FROM " in sql and "pages" in sql and "INTERVAL 7 DAYS" in sql:
                cols = list(pages[0].keys())
                resp.result.data_array = [[p[c] for c in cols] for p in pages]
                col_mocks = []
                for c in cols:
                    m = MagicMock()
                    m.name = c
                    col_mocks.append(m)
                resp.manifest.schema.columns = col_mocks
            else:
                resp.result.data_array = []
                col_mocks = []
                m = MagicMock()
                m.name = "slug"
                col_mocks.append(m)
                resp.manifest.schema.columns = col_mocks
            return resp

        ws.statement_execution.execute_statement.side_effect = exec_statement

        def query(**kwargs):
            resp = MagicMock()
            resp.choices = [
                MagicMock(
                    message=MagicMock(content='{"tags": ["row-level-security"]}')
                )
            ]
            return resp

        ws.serving_endpoints.query.side_effect = query

        wiki = MagicMock(spec_set=WikiClient)
        dbutils = _make_dbutils()
        _exec_notebook(ws, wiki, dbutils)

        # Vocab gets the parsed slug
        wiki.upsert_vocabulary.assert_called_once()
        observed = wiki.upsert_vocabulary.call_args[0][0]
        assert any(o["slug"] == "row-level-security" for o in observed)

        # Page gets the llm:-prefixed tag
        wiki.append_page_tags.assert_called_once()
        path, tags = wiki.append_page_tags.call_args[0]
        assert path == "topics/foo"
        assert "llm:row-level-security" in tags

        # auto_tag log event written
        log_call = wiki._log.call_args  # called positionally
        assert log_call.args[0] == "auto_tag"
