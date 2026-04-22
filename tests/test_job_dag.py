"""Cross-task DAG integration test for the wikibricks_curate job.

The job has two sequential tasks — `curate` then `promote` — that share a
library contract (`wikibricks.WikiClient`) but not runtime state. The only
way silent drift leaks in is if a curate-side refactor removes or renames
a method that promote still calls. MagicMock auto-attributes hide this.

This test uses `spec_set=WikiClient` so any missing attribute raises, and
execs both notebooks end-to-end in the same sequence the Databricks job
runs them. If promote calls `wiki.foo()` and `foo` was removed from the
real WikiClient, this test fails where the isolated notebook tests would
not.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wikibricks import WikiClient

REPO_ROOT = Path(__file__).parent.parent
CURATE_NB = REPO_ROOT / "notebooks" / "wiki_curate.py"
PROMOTE_NB = REPO_ROOT / "notebooks" / "promote_from_traces.py"


class NotebookExit(Exception):
    def __init__(self, msg: str = "") -> None:
        super().__init__(msg)
        self.msg = msg


def _make_dbutils() -> MagicMock:
    dbutils = MagicMock()
    dbutils.widgets.get.side_effect = Exception("first run")
    dbutils.notebook.exit.side_effect = lambda m="": (_ for _ in ()).throw(NotebookExit(m))
    return dbutils


def _make_spec_wiki() -> MagicMock:
    """WikiClient mock that only allows methods/attrs that actually exist."""
    wiki = MagicMock(spec_set=WikiClient)
    wiki.propose_edges.return_value = []
    wiki.commit_edges.return_value = 0
    wiki.fix_broken_links.return_value = 0
    wiki.search.return_value = []
    wiki.read_page.return_value = None
    wiki.list_pages.return_value = []
    wiki.write_page.return_value = "written"
    wiki.promote_answer.return_value = "promoted/foo"
    wiki.history.return_value = []
    wiki.sync_index.return_value = None
    # `_log` is private but both notebooks call it; spec_set allows because it
    # exists on the class.
    return wiki


def _make_spark_curate() -> MagicMock:
    spark = MagicMock()
    empty = MagicMock()
    empty.collect.return_value = []
    spark.sql.return_value = empty
    return spark


def _make_spark_promote() -> MagicMock:
    """Promote-side spark: checkpoint miss, zero silver rows.

    Exits via `dbutils.notebook.exit("no traces to promote")` — that's a
    successful early-exit, not a failure. The test still proves promote's
    import block + pre-loop SQL run against the spec_set'd wiki.
    """
    spark = MagicMock()

    def sql_fn(query: str):
        q = query.strip()
        result = MagicMock()
        if q.startswith("SELECT last_watermark_ts"):
            result.collect.return_value = []  # checkpoint miss (first run)
        elif "FROM agent_marketplace_catalog.wiki.agent_traces" in q:
            result.collect.return_value = []  # no traces
        else:
            result.collect.return_value = []
        return result

    spark.sql.side_effect = sql_fn
    return spark


def _make_ws() -> MagicMock:
    ws = MagicMock()
    ws.config.host = "https://test"
    # statement_execution mock for curate's run_sql helper
    resp = MagicMock()
    resp.result.data_array = []
    resp.manifest.schema.columns = []
    ws.statement_execution.execute_statement.return_value = resp
    return ws


def _exec_notebook(path: Path, spark: MagicMock, ws: MagicMock, wiki: MagicMock, dbutils: MagicMock):
    src = path.read_text()
    ns = {
        "__name__": "notebook_under_test",
        "__file__": str(path),
        "spark": spark,
        "dbutils": dbutils,
    }
    with patch("databricks.sdk.WorkspaceClient", return_value=ws), \
         patch("wikibricks.WikiClient", return_value=wiki):
        try:
            exec(compile(src, str(path), "exec"), ns)
        except NotebookExit:
            pass
    return ns


class TestJobDagSchemaContract:
    """Guards cross-task method contract between curate and promote."""

    def test_curate_then_promote_succeed_with_spec_set_wiki(self):
        """Run both notebooks in DAG order against a spec_set'd WikiClient.

        If either notebook calls a method that doesn't exist on the real
        WikiClient (typo, removed method, renamed), spec_set raises
        AttributeError and this test fails. MagicMock without spec_set
        would silently accept any attribute.
        """
        wiki = _make_spec_wiki()
        ws = _make_ws()
        dbutils = _make_dbutils()

        _exec_notebook(CURATE_NB, _make_spark_curate(), ws, wiki, dbutils)
        _exec_notebook(PROMOTE_NB, _make_spark_promote(), ws, wiki, _make_dbutils())

        # Curate's two phases both ran: propose_edges may have been called
        # zero times (no recent pages) but fix_broken_links unconditionally
        # runs when REPAIR_BROKEN_LINKS is true (default).
        wiki.fix_broken_links.assert_called()
        # Curate logs a `curate_run` summary at the end.
        assert any(
            c.args and c.args[0] == "curate_run"
            for c in wiki._log.call_args_list
        )

    def test_all_wiki_methods_used_by_promote_exist_on_class(self):
        """Static contract check: enumerate the WikiClient methods the promote
        notebook calls and assert every one is present on the real class.

        Belt-and-suspenders alongside spec_set: this version catches the case
        where promote imports a method but never executes that branch under
        the default mock silver (empty). Explicit names survive any refactor.
        """
        expected = {
            "search", "read_page", "write_page", "promote_answer",
            "sync_index", "_log",
        }
        missing = expected - set(dir(WikiClient))
        assert not missing, f"promote uses methods missing from WikiClient: {missing}"

    def test_all_wiki_methods_used_by_curate_exist_on_class(self):
        """Same guard for curate-side calls."""
        expected = {"propose_edges", "commit_edges", "fix_broken_links", "_log"}
        missing = expected - set(dir(WikiClient))
        assert not missing, f"curate uses methods missing from WikiClient: {missing}"
