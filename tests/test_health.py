"""Behavioral tests for `wikibricks.health`."""

from wikibricks.health import (
    HealthCheck,
    all_passing,
    default_checks,
    evaluate,
    format_report,
    run_health_checks,
)


def _check(name: str = "x", op: str = "ge", threshold: float = 1.0) -> HealthCheck:
    return HealthCheck(
        name=name,
        sql="SELECT 1",
        threshold_op=op,
        threshold_value=threshold,
        description="test",
    )


class TestEvaluate:
    def test_ge_passes_when_value_equals_threshold(self):
        assert evaluate(_check(op="ge", threshold=5.0), 5.0) is True

    def test_ge_passes_when_value_above_threshold(self):
        assert evaluate(_check(op="ge", threshold=5.0), 6.0) is True

    def test_ge_fails_when_value_below_threshold(self):
        assert evaluate(_check(op="ge", threshold=5.0), 4.999) is False

    def test_le_passes_when_value_equals_threshold(self):
        assert evaluate(_check(op="le", threshold=600.0), 600.0) is True

    def test_le_fails_when_value_above_threshold(self):
        assert evaluate(_check(op="le", threshold=600.0), 600.001) is False

    def test_unknown_op_raises(self):
        try:
            evaluate(_check(op="eq", threshold=1.0), 1.0)
        except ValueError as e:
            assert "threshold_op" in str(e)
        else:
            raise AssertionError("expected ValueError")


class TestRunHealthChecks:
    def test_runner_called_once_per_check(self):
        calls = []

        def runner(sql: str) -> float:
            calls.append(sql)
            return 10.0

        checks = [_check(name="a"), _check(name="b"), _check(name="c")]
        results = run_health_checks(runner, checks)
        assert len(calls) == 3
        assert [r.check.name for r in results] == ["a", "b", "c"]

    def test_passing_flag_reflects_evaluate(self):
        def runner(sql: str) -> float:
            return 0.5

        checks = [_check(op="ge", threshold=1.0), _check(op="le", threshold=1.0)]
        results = run_health_checks(runner, checks)
        assert results[0].passing is False
        assert results[1].passing is True

    def test_nan_value_is_treated_as_failing(self):
        def runner(sql: str) -> float:
            return float("nan")

        results = run_health_checks(runner, [_check(op="ge", threshold=0.0)])
        assert results[0].passing is False
        # NaN is propagated to the result so the caller can see the bad value
        assert results[0].value != results[0].value


class TestAllPassing:
    def test_returns_true_when_every_check_passes(self):
        def runner(sql: str) -> float:
            return 1.0

        results = run_health_checks(runner, [_check(op="ge", threshold=0.5)] * 3)
        assert all_passing(results) is True

    def test_returns_false_when_any_check_fails(self):
        def runner(sql: str) -> float:
            return 0.0

        results = run_health_checks(
            runner,
            [
                _check(name="a", op="ge", threshold=0.0),
                _check(name="b", op="ge", threshold=1.0),
            ],
        )
        assert all_passing(results) is False


class TestFormatReport:
    def test_summary_line_present(self):
        def runner(sql: str) -> float:
            return 1.0

        results = run_health_checks(runner, [_check(op="ge", threshold=0.5)])
        report = format_report(results)
        assert "1/1 passing" in report

    def test_failing_row_marked_fail(self):
        def runner(sql: str) -> float:
            return 0.1

        results = run_health_checks(
            runner, [_check(name="auto_tag", op="ge", threshold=0.8)]
        )
        report = format_report(results)
        assert "FAIL" in report
        assert "auto_tag" in report

    def test_passing_row_marked_pass(self):
        def runner(sql: str) -> float:
            return 0.9

        results = run_health_checks(
            runner, [_check(name="auto_tag", op="ge", threshold=0.8)]
        )
        report = format_report(results)
        assert "PASS" in report


class TestDefaultChecks:
    def test_returns_six_checks(self):
        assert len(default_checks("c", "s")) == 6

    def test_check_names_are_distinct(self):
        names = [c.name for c in default_checks("c", "s")]
        assert len(set(names)) == len(names)

    def test_sql_references_provided_catalog_and_schema(self):
        for check in default_checks("mycat", "mysch"):
            assert "mycat.mysch" in check.sql, (
                f"{check.name}: SQL must reference fully-qualified catalog.schema"
            )

    def test_thresholds_match_north_star_table(self):
        # Locks the contract documented in the deep plan.
        by_name = {c.name: c for c in default_checks("c", "s")}
        assert by_name["auto_tag_coverage"].threshold_value == 0.8
        assert by_name["vocab_growth"].threshold_value == 5.0
        assert by_name["page_tag_coverage"].threshold_value == 0.7
        assert by_name["citations_logged"].threshold_value == 10.0
        assert by_name["promote_end_to_end"].threshold_value == 1.0
        assert by_name["curate_recent"].threshold_value == 86400.0

    def test_curate_recent_uses_le(self):
        by_name = {c.name: c for c in default_checks("c", "s")}
        assert by_name["curate_recent"].threshold_op == "le"
        for name in ("auto_tag_coverage", "vocab_growth", "page_tag_coverage",
                     "citations_logged", "promote_end_to_end"):
            assert by_name[name].threshold_op == "ge"

    def test_page_tag_coverage_filters_for_llm_prefix(self):
        # The recorder writes mechanical tags (session, cwd:..., model:...) to
        # pages.tags. Only "llm:" prefixed tags count as semantic auto-tags.
        sql = next(c.sql for c in default_checks("c", "s")
                   if c.name == "page_tag_coverage")
        assert "llm:" in sql

    def test_promote_probe_checks_agent_traces_v(self):
        # v0.5.0 correctness check: the promote source pipeline is wired.
        # Whether promote actually clusters depends on traffic + thresholds.
        sql = next(c.sql for c in default_checks("c", "s")
                   if c.name == "promote_end_to_end")
        assert "agent_traces_v" in sql

    def test_vocab_probe_counts_all_terms(self):
        # Don't gate on status='approved' — that's a downstream usage signal,
        # not a v0.5.0 mechanism check.
        sql = next(c.sql for c in default_checks("c", "s")
                   if c.name == "vocab_growth")
        assert "wiki_vocabulary" in sql
        assert "WHERE status" not in sql
