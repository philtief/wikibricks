"""Mock-based integration test for `notebooks/promote_from_traces.py`.

Unit tests in `test_promote_logic.py` cover the deterministic helpers.
This file covers the *wiring* between those helpers, `spark.sql`, VS
embeddings, FMAPI chat, and `WikiClient` calls - the integration surface
where Phase-3 end-to-end validation surfaced four real bugs that pure
helper tests did not catch:

    1. `resp.data[0]["embedding"]` subscript on a typed SDK object.
    2. Silver SQL called `collect_set` on `ARRAY<STRING>`, yielding
       array-of-arrays that then crashed repr() during dedup lookups.
    3. `pages` table missing `source_ids` column (library fix; see
       test_client.py).
    4. `_log` INSERT missing `log_id` (library fix; see test_client.py).

The strategy: exec the notebook source with `spark`, `dbutils`,
`WorkspaceClient`, and `WikiClient` replaced by MagicMocks that route
on SQL content / endpoint name. Then assert the mocks were called with
the shapes the production runtime requires.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

NOTEBOOK_PATH = Path("notebooks/promote_from_traces.py")


class NotebookExit(Exception):
    """Raised by the dbutils.notebook.exit mock so exec() stops where the
    real runtime would short-circuit."""

    def __init__(self, msg: str = "") -> None:
        super().__init__(msg)
        self.msg = msg


def _fake_row(data: dict) -> MagicMock:
    """Spark Row stand-in supporting `row["k"]` and `row.asDict()`."""
    row = MagicMock()
    row.__getitem__.side_effect = lambda k: data[k]
    row.asDict.return_value = data
    return row


def _make_dbutils() -> MagicMock:
    dbutils = MagicMock()
    # Return "" so _param falls back to its hardcoded default via `val or default`.
    dbutils.widgets.get.return_value = ""

    def _exit(msg: str = "") -> None:
        raise NotebookExit(msg)

    dbutils.notebook.exit.side_effect = _exit
    return dbutils


def _make_spark(*, silver_rows=None, silver_raises: bool = False) -> MagicMock:
    """Route `spark.sql` by SQL content; record every call for assertions."""
    spark = MagicMock()
    spark._captured_sql = []

    def sql_side_effect(sql: str):
        spark._captured_sql.append(sql)
        result = MagicMock()
        if "SELECT last_watermark_ts" in sql:
            result.collect.return_value = []
        elif "GROUP BY session_id" in sql:
            if silver_raises:
                raise RuntimeError("traces table not found")
            result.collect.return_value = silver_rows or []
        return result

    spark.sql.side_effect = sql_side_effect
    return spark


def _embed_for(text: str) -> list[float]:
    """Deterministic fake embedding.

    Queries tagged `"Cluster 0 ..."` / `"Cluster 1 ..."` map to orthogonal
    unit vectors, so rows with the same tag cluster together and rows
    with different tags don't. Lets tests exercise multi-cluster runs
    without touching a real embedding endpoint.
    """
    if "Cluster 0" in text:
        return [1.0, 0.0, 0.0]
    if "Cluster 1" in text:
        return [0.0, 1.0, 0.0]
    if "Cluster 2" in text:
        return [0.0, 0.0, 1.0]
    return [1.0, 0.0, 0.0]


def _make_ws(*, judge_score: str = "5") -> MagicMock:
    """Route `serving_endpoints.query` by endpoint name.

    - embed endpoint: `.data[0].embedding` is a deterministic vector
      routed off the input text via `_embed_for`.
    - chat endpoint: synth or judge response based on the system prompt.
    """
    ws = MagicMock()

    def query_side_effect(**kwargs):
        name = kwargs.get("name", "")
        resp = MagicMock()
        if "bge" in name or "embed" in name:
            texts = kwargs.get("input") or [""]
            data = []
            for t in texts:
                emb = MagicMock()
                emb.embedding = _embed_for(t)
                data.append(emb)
            resp.data = data
            return resp
        sys_content = ""
        msgs = kwargs.get("messages", [])
        if msgs:
            sys_content = getattr(msgs[0], "content", "") or ""
        if "Synthesise" in sys_content:
            resp.choices = [MagicMock(message=MagicMock(content="Canonical answer."))]
        else:
            resp.choices = [MagicMock(message=MagicMock(content=judge_score))]
        return resp

    ws.serving_endpoints.query.side_effect = query_side_effect
    return ws


def _make_wiki(*, search_hit=None, read_page=None) -> MagicMock:
    wiki = MagicMock()
    wiki.search.return_value = [search_hit] if search_hit else []
    wiki.read_page.return_value = read_page
    return wiki


def _exec_notebook(spark: MagicMock, ws: MagicMock, wiki: MagicMock, dbutils: MagicMock) -> dict:
    """Exec the notebook source with runtime globals pre-seeded.

    `WorkspaceClient` and `WikiClient` are patched at their source
    modules so the notebook's `from databricks.sdk import WorkspaceClient`
    / `from wikibricks import WikiClient` resolve to the mocks.
    """
    src = NOTEBOOK_PATH.read_text()
    ns = {
        "__name__": "promote_notebook",
        "__file__": str(NOTEBOOK_PATH),
        "spark": spark,
        "dbutils": dbutils,
    }
    with (
        patch("databricks.sdk.WorkspaceClient", return_value=ws),
        patch("wikibricks.WikiClient", return_value=wiki),
    ):
        try:
            exec(compile(src, str(NOTEBOOK_PATH), "exec"), ns)
        except NotebookExit:
            pass
    return ns


# --------------------------------------------------------------------------- #
# Source-level regressions (cheap + fast, guard against the 4 Phase-3 bugs)
# --------------------------------------------------------------------------- #


class TestNotebookSourceRegressions:
    @classmethod
    def setup_class(cls) -> None:
        cls.src = NOTEBOOK_PATH.read_text()

    def test_silver_sql_flattens_array_sources(self):
        # Bug #2: collect_set on ARRAY<STRING> produces array-of-arrays;
        # downstream Python needs a flat list. Must wrap in flatten().
        assert "array_distinct(flatten(collect_set(retrieved_paths)))" in self.src

    def test_embedding_uses_attribute_not_subscript(self):
        # Bug #1: resp.data[0] is a typed SDK object, not a dict.
        assert "resp.data[0].embedding" in self.src
        assert 'resp.data[0]["embedding"]' not in self.src

    def test_cluster_threshold_is_parameterized(self):
        # BGE paraphrase cosine can dip below 0.88 on real traffic; threshold
        # must be tunable via job parameter, not a magic number.
        assert "CLUSTER_THRESHOLD" in self.src
        assert "cluster_threshold" in self.src

    def test_checkpoint_merge_exists(self):
        assert "MERGE INTO {PROMOTE_CHECKPOINT_TABLE}" in self.src

    def test_empty_silver_guard_exists(self):
        # Promote is opt-in; an absent traces table must not fail the curate job.
        assert 'dbutils.notebook.exit("no traces to promote")' in self.src


# --------------------------------------------------------------------------- #
# Integration: exec the notebook with mocks, assert behavior
# --------------------------------------------------------------------------- #


class TestNotebookIntegration:
    def _silver(self, n_per_cluster: int = 5, n_clusters: int = 1) -> list[MagicMock]:
        rows = []
        for c in range(n_clusters):
            for i in range(n_per_cluster):
                rows.append(
                    _fake_row({
                        "session_id": f"s{c}-{i}",
                        "query": f"Cluster {c} paraphrase {i}",
                        "answer": f"Cluster {c} canonical answer",
                        "sources": [f"topics/t{c}"],
                    })
                )
        return rows

    def test_empty_silver_triggers_early_exit(self):
        spark = _make_spark(silver_rows=[])
        ws = _make_ws()
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        dbutils.notebook.exit.assert_called_once_with("no traces to promote")
        wiki.promote_answer.assert_not_called()
        ws.serving_endpoints.query.assert_not_called()

    def test_missing_traces_table_exits_cleanly(self):
        # Opt-in guard: silver read raises → silver=[] → early exit.
        spark = _make_spark(silver_raises=True)
        ws = _make_ws()
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        dbutils.notebook.exit.assert_called_once_with("no traces to promote")
        wiki.promote_answer.assert_not_called()

    def test_happy_path_single_cluster_promotes_once(self):
        # 5 identical-embedding rows → 1 cluster → eligible → judge=5 → promote.
        spark = _make_spark(silver_rows=self._silver(5, 1))
        ws = _make_ws(judge_score="5")
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        assert wiki.promote_answer.call_count == 1
        args = wiki.promote_answer.call_args.args
        assert "Cluster 0" in args[0]
        assert args[1] == "Canonical answer."
        assert wiki.promote_answer.call_args.kwargs.get("created_by") == "batch-promote"

    def test_judge_score_below_threshold_rejects(self):
        spark = _make_spark(silver_rows=self._silver(5, 1))
        ws = _make_ws(judge_score="3")
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        wiki.promote_answer.assert_not_called()
        reject_calls = [
            c for c in wiki._log.call_args_list
            if c.args and c.args[0] == "promote_reject"
        ]
        assert len(reject_calls) == 1

    def test_dedup_hit_writes_page_instead_of_promoting(self):
        spark = _make_spark(silver_rows=self._silver(5, 1))
        ws = _make_ws(judge_score="5")
        wiki = _make_wiki(
            search_hit={"path": "promoted/vs-modes", "score": 0.95},
        )
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        wiki.promote_answer.assert_not_called()
        assert wiki.write_page.call_count == 1
        kwargs = wiki.write_page.call_args.kwargs
        assert kwargs["path"] == "promoted/vs-modes"
        assert kwargs["page_type"] == "synthesis"
        assert kwargs["created_by"] == "batch-promote"

    def test_cluster_below_member_floor_is_skipped(self):
        # Default min_cluster_members=5. 3 rows → no eligible clusters →
        # no synth/judge LLM calls (only embed calls during clustering).
        spark = _make_spark(silver_rows=self._silver(3, 1))
        ws = _make_ws()
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        wiki.promote_answer.assert_not_called()
        names = [
            c.kwargs.get("name", "")
            for c in ws.serving_endpoints.query.call_args_list
        ]
        # embed calls happen; chat (claude/sonnet) calls must not.
        assert all(("bge" in n or "embed" in n) for n in names)

    def test_checkpoint_is_advanced_after_successful_run(self):
        spark = _make_spark(silver_rows=self._silver(5, 1))
        ws = _make_ws(judge_score="5")
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        merge_calls = [s for s in spark._captured_sql if "MERGE INTO" in s]
        assert len(merge_calls) == 1
        assert "PROMOTE_CHECKPOINT_TABLE" in merge_calls[0] \
            or "promote_checkpoint" in merge_calls[0].lower()

    def test_silver_sql_uses_flatten_when_exec_runs(self):
        # Runtime confirmation of the source assertion: the SQL that
        # actually hits spark.sql must contain the flatten wrapper.
        spark = _make_spark(silver_rows=self._silver(5, 1))
        ws = _make_ws(judge_score="5")
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        silver_sqls = [
            s for s in spark._captured_sql if "GROUP BY session_id" in s
        ]
        assert len(silver_sqls) == 1
        assert "array_distinct(flatten(collect_set(retrieved_paths)))" in silver_sqls[0]

    def test_every_silver_query_is_embedded(self):
        # Guards against regressions that skip the embed step or lose
        # queries in transit. Counts distinct texts sent to the embed
        # endpoint, NOT call count - tolerates a future batching refactor
        # that would change one-per-call to one-call-many-inputs.
        spark = _make_spark(silver_rows=self._silver(5, 1))
        ws = _make_ws(judge_score="5")
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        seen: set[str] = set()
        for c in ws.serving_endpoints.query.call_args_list:
            name = c.kwargs.get("name", "")
            if "bge" in name or "embed" in name:
                for t in c.kwargs.get("input") or []:
                    seen.add(t)
        expected = {f"Cluster 0 paraphrase {i}" for i in range(5)}
        assert seen == expected

    def test_multiple_eligible_clusters_each_promote_once(self):
        # Two clusters, orthogonal embeddings via _embed_for, judge=5 for both.
        # Exercises the `for cluster in eligible` loop with len > 1 and
        # confirms each cluster triggers its own synth+judge+promote.
        spark = _make_spark(silver_rows=self._silver(5, 2))
        ws = _make_ws(judge_score="5")
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        assert wiki.promote_answer.call_count == 2
        queries = [c.args[0] for c in wiki.promote_answer.call_args_list]
        assert any("Cluster 0" in q for q in queries)
        assert any("Cluster 1" in q for q in queries)

    def test_non_numeric_judge_response_logs_parse_fail(self):
        # When the judge returns gibberish (prompt drift), the notebook logs
        # `promote_parse_fail` — NOT `promote_reject` — so an operator can
        # distinguish 'bad LLM output' from 'legitimate low score'.
        spark = _make_spark(silver_rows=self._silver(5, 1))
        ws = _make_ws(judge_score="excellent!")
        wiki = _make_wiki()
        dbutils = _make_dbutils()

        _exec_notebook(spark, ws, wiki, dbutils)

        wiki.promote_answer.assert_not_called()
        wiki.write_page.assert_not_called()

        parse_fail_calls = [
            c for c in wiki._log.call_args_list
            if c.args and c.args[0] == "promote_parse_fail"
        ]
        reject_calls = [
            c for c in wiki._log.call_args_list
            if c.args and c.args[0] == "promote_reject"
        ]
        assert len(parse_fail_calls) == 1
        assert len(reject_calls) == 0
        # The details string must surface the raw judge output so an operator
        # can see what the judge said (truncated).
        details = parse_fail_calls[0].kwargs.get("details", "")
        assert "excellent!" in details
