"""Behavioral tests for `wikibricks.curate_logic`.

Every feature exercised by `notebooks/wiki_curate.py` has a test here that runs
without a workspace, SDK, or LLM.
"""

import threading
from unittest.mock import MagicMock

from wikibricks.curate_logic import (
    BODY_OVERSIZE_THRESHOLD,
    assess_index_drift,
    build_curate_summary,
    build_health_summary,
    classify_page_health,
    find_duplicate_paths,
    partition_by_confidence,
    run_connect_phase,
)


class TestAssessIndexDrift:
    def test_healthy_when_counts_match(self):
        d = assess_index_drift(pages=2019, vs_source=2019, indexed=2019)
        assert d["drifted"] is False
        assert d["severity"] == "ok"

    def test_within_tolerance_is_not_drift(self):
        # Small lag between source and index is normal for a TRIGGERED index.
        d = assess_index_drift(pages=2020, vs_source=2019, indexed=2018, tolerance=5)
        assert d["drifted"] is False

    def test_orphans_flagged_as_drift(self):
        # vs_source larger than pages => orphaned rows (deleted pages linger).
        d = assess_index_drift(pages=2000, vs_source=3675, indexed=3400)
        assert d["drifted"] is True
        assert d["vs_source_orphans"] == 1675
        assert d["severity"] == "orphans"

    def test_frozen_index_flagged_as_drift(self):
        # Index far from source => DLT pipeline likely frozen/failed.
        d = assess_index_drift(pages=2020, vs_source=2020, indexed=3400)
        assert d["drifted"] is True
        assert d["severity"] == "index_stale"
        assert d["index_gap"] == 1380

    def test_orphans_take_precedence_over_index_gap(self):
        d = assess_index_drift(pages=2000, vs_source=3675, indexed=3400)
        assert d["severity"] == "orphans"

    def test_indexed_none_skips_index_check(self):
        # get_index may not report a row count; don't false-alarm on that.
        d = assess_index_drift(pages=2019, vs_source=2019, indexed=None)
        assert d["drifted"] is False
        assert d["severity"] == "ok"


class TestPartitionByConfidence:
    def test_splits_strictly_at_threshold(self):
        edges = [
            {"target_path": "a", "confidence": 0.90},
            {"target_path": "b", "confidence": 0.85},  # exactly at threshold -> high
            {"target_path": "c", "confidence": 0.84},
            {"target_path": "d", "confidence": 0.50},
        ]
        high, low = partition_by_confidence(edges, 0.85)
        assert [e["target_path"] for e in high] == ["a", "b"]
        assert [e["target_path"] for e in low] == ["c", "d"]

    def test_empty_input_returns_two_empty_lists(self):
        high, low = partition_by_confidence([], 0.5)
        assert high == []
        assert low == []

    def test_missing_confidence_treated_as_zero(self):
        edges = [{"target_path": "a"}]
        high, low = partition_by_confidence(edges, 0.5)
        assert high == []
        assert low == edges

    def test_iterator_input_is_consumed_correctly(self):
        edges = iter([
            {"confidence": 0.95},
            {"confidence": 0.10},
        ])
        high, low = partition_by_confidence(edges, 0.8)
        assert len(high) == 1 and len(low) == 1

    def test_threshold_above_all_sends_everything_to_low(self):
        edges = [{"confidence": 0.5}, {"confidence": 0.7}]
        high, low = partition_by_confidence(edges, 0.99)
        assert high == []
        assert len(low) == 2


class TestBuildCurateSummary:
    def test_all_four_lint_categories_present_even_when_zero(self):
        s = build_curate_summary(
            paths_scanned=0,
            edges_proposed=0,
            edges_committed=0,
            deferred_low_confidence=0,
            auto_commit_threshold=0.85,
            lint_issues=[],
            broken_links_deleted=0,
        )
        assert set(s["lint"]["by_check"]) == {
            "orphan", "stale", "duplicate_path", "broken_link"
        }
        assert all(v == 0 for v in s["lint"]["by_check"].values())

    def test_counts_lint_issues_by_check_correctly(self):
        issues = [
            {"check": "orphan"},
            {"check": "orphan"},
            {"check": "stale"},
            {"check": "broken_link"},
            {"check": "unknown_check"},  # should be ignored
        ]
        s = build_curate_summary(
            paths_scanned=3,
            edges_proposed=5,
            edges_committed=2,
            deferred_low_confidence=3,
            auto_commit_threshold=0.85,
            lint_issues=issues,
            broken_links_deleted=1,
        )
        assert s["lint"]["issues"] == 5
        assert s["lint"]["by_check"]["orphan"] == 2
        assert s["lint"]["by_check"]["stale"] == 1
        assert s["lint"]["by_check"]["broken_link"] == 1
        assert s["lint"]["by_check"]["duplicate_path"] == 0

    def test_connect_block_echoes_all_inputs(self):
        s = build_curate_summary(
            paths_scanned=7,
            edges_proposed=20,
            edges_committed=12,
            deferred_low_confidence=8,
            auto_commit_threshold=0.9,
            lint_issues=[],
            broken_links_deleted=None,
        )
        assert s["connect"] == {
            "pages_scanned": 7,
            "edges_proposed": 20,
            "edges_committed": 12,
            "deferred_low_confidence": 8,
            "auto_commit_threshold": 0.9,
        }

    def test_broken_links_deleted_none_when_repair_skipped(self):
        s = build_curate_summary(
            paths_scanned=0,
            edges_proposed=0,
            edges_committed=0,
            deferred_low_confidence=0,
            auto_commit_threshold=0.85,
            lint_issues=[],
            broken_links_deleted=None,
        )
        assert s["repair"]["broken_links_deleted"] is None

    def test_broken_links_deleted_zero_when_repair_found_nothing(self):
        s = build_curate_summary(
            paths_scanned=0,
            edges_proposed=0,
            edges_committed=0,
            deferred_low_confidence=0,
            auto_commit_threshold=0.85,
            lint_issues=[],
            broken_links_deleted=0,
        )
        assert s["repair"]["broken_links_deleted"] == 0

    def test_health_block_excluded_when_not_provided(self):
        s = build_curate_summary(
            paths_scanned=0,
            edges_proposed=0,
            edges_committed=0,
            deferred_low_confidence=0,
            auto_commit_threshold=0.85,
            lint_issues=[],
            broken_links_deleted=None,
        )
        assert "health" not in s

    def test_health_block_included_when_provided(self):
        health = {"pages_checked": 10, "by_status": {"ok": 8, "oversize": 2},
                  "duplicates": 0}
        s = build_curate_summary(
            paths_scanned=0,
            edges_proposed=0,
            edges_committed=0,
            deferred_low_confidence=0,
            auto_commit_threshold=0.85,
            lint_issues=[],
            broken_links_deleted=None,
            health=health,
        )
        assert s["health"] == health


# ---------------------------------------------------------------------------
# health analysis helpers — deterministic, no LLM
# ---------------------------------------------------------------------------


class TestClassifyPageHealth:
    def test_normal_body_is_ok(self):
        status, score = classify_page_health({"body": "a normal page body"})
        assert status == "ok"
        assert score == 1.0

    def test_empty_body_is_empty(self):
        status, score = classify_page_health({"body": ""})
        assert status == "empty"
        assert score == 0.0

    def test_whitespace_only_body_is_empty(self):
        status, score = classify_page_health({"body": "   \n\t  "})
        assert status == "empty"

    def test_missing_body_field_is_empty(self):
        status, _ = classify_page_health({})
        assert status == "empty"

    def test_oversize_body_flagged(self):
        big = "x" * (BODY_OVERSIZE_THRESHOLD + 1)
        status, score = classify_page_health({"body": big})
        assert status == "oversize"
        assert 0.0 < score < 1.0

    def test_body_at_threshold_is_ok(self):
        body = "x" * BODY_OVERSIZE_THRESHOLD
        status, _ = classify_page_health({"body": body})
        assert status == "ok"

    def test_custom_threshold_overrides_default(self):
        status, _ = classify_page_health({"body": "x" * 11}, body_max=10)
        assert status == "oversize"


class TestFindDuplicatePaths:
    def test_no_duplicates_returns_empty(self):
        pages = [{"id": "1", "path": "a"}, {"id": "2", "path": "b"}]
        assert find_duplicate_paths(pages) == []

    def test_two_pages_same_path_flagged(self):
        pages = [
            {"id": "1", "path": "a"},
            {"id": "2", "path": "a"},
            {"id": "3", "path": "b"},
        ]
        dups = find_duplicate_paths(pages)
        assert len(dups) == 1
        assert dups[0]["path"] == "a"
        assert dups[0]["count"] == 2
        assert set(dups[0]["page_ids"]) == {"1", "2"}

    def test_three_pages_same_path_count_three(self):
        pages = [{"id": str(i), "path": "a"} for i in range(3)]
        dups = find_duplicate_paths(pages)
        assert dups[0]["count"] == 3

    def test_pages_without_path_skipped(self):
        pages = [{"id": "1"}, {"id": "2", "path": "a"}]
        assert find_duplicate_paths(pages) == []


class TestBuildHealthSummary:
    def test_echoes_inputs(self):
        s = build_health_summary(
            pages_checked=10,
            by_status={"ok": 7, "oversize": 2, "empty": 1},
            duplicates=2,
        )
        assert s == {
            "pages_checked": 10,
            "by_status": {"ok": 7, "oversize": 2, "empty": 1},
            "duplicates": 2,
        }

    def test_empty_by_status_ok(self):
        s = build_health_summary(pages_checked=0, by_status={}, duplicates=0)
        assert s["pages_checked"] == 0
        assert s["by_status"] == {}


class TestRunConnectPhase:
    def _commit_recorder(self):
        commits: list[list[dict]] = []

        def commit(edges):
            commits.append(list(edges))
            return len(edges)

        return commit, commits

    def test_propose_called_once_per_path(self):
        propose = MagicMock(return_value=[])
        commit, _ = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "b", "c"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert propose.call_count == 3
        called_paths = sorted(c.args[0] for c in propose.call_args_list)
        assert called_paths == ["a", "b", "c"]
        assert result["edges_proposed"] == 0

    def test_commit_called_exactly_once_with_aggregated_high_edges(self):
        def propose(path):
            return [{"source_page_id": path, "target_page_id": "t",
                     "link_type": "related", "confidence": 0.9, "origin": "auto-vs"}]
        commit, commits = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "b", "c"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert len(commits) == 1, "commit_fn must be called exactly once"
        assert len(commits[0]) == 3
        assert result["edges_committed"] == 3
        assert result["edges_proposed"] == 3
        assert result["deferred_low_confidence"] == []

    def test_commit_not_called_when_no_high_confidence_edges(self):
        def propose(path):
            return [{"source_page_id": path, "target_page_id": "t",
                     "link_type": "related", "confidence": 0.5, "origin": "auto-vs"}]
        commit, commits = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "b"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=2,
        )
        assert commits == []
        assert result["edges_committed"] == 0
        assert result["edges_proposed"] == 2
        assert len(result["deferred_low_confidence"]) == 2

    def test_deferred_edges_tagged_with_source_path(self):
        def propose(path):
            return [{"source_page_id": path, "target_page_id": f"t-{path}",
                     "link_type": "related", "confidence": 0.5, "origin": "auto-vs"}]
        commit, _ = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "b"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=2,
        )
        deferred_paths = sorted(d["path"] for d in result["deferred_low_confidence"])
        assert deferred_paths == ["a", "b"]

    def test_propose_exception_does_not_crash_and_counts_as_zero(self):
        def propose(path):
            if path == "bad":
                raise RuntimeError("boom")
            return [{"source_page_id": path, "target_page_id": "t",
                     "link_type": "related", "confidence": 0.9, "origin": "auto-vs"}]
        commit, commits = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "bad", "c"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert result["edges_proposed"] == 2  # bad page contributed nothing
        assert result["failed_paths"] == ["bad"]
        assert len(commits[0]) == 2

    def test_max_workers_one_runs_sequentially(self):
        propose = MagicMock(return_value=[])
        commit, _ = self._commit_recorder()
        run_connect_phase(
            paths=["a", "b"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=1,
        )
        assert propose.call_count == 2

    def test_propose_runs_concurrently_when_max_workers_gt_one(self):
        # Barrier of size 4 only releases when 4 threads enter — proves
        # concurrency. With max_workers=1 the barrier would time out.
        barrier = threading.Barrier(4, timeout=2.0)
        seen_threads: set[int] = set()
        lock = threading.Lock()

        def propose(path):
            with lock:
                seen_threads.add(threading.get_ident())
            barrier.wait()
            return []

        commit, _ = self._commit_recorder()
        run_connect_phase(
            paths=[f"p{i}" for i in range(4)],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert len(seen_threads) >= 2, \
            f"expected concurrent execution, only saw {len(seen_threads)} thread(s)"

    def test_empty_paths_calls_nothing(self):
        propose = MagicMock(return_value=[])
        commit, commits = self._commit_recorder()
        result = run_connect_phase(
            paths=[],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert propose.call_count == 0
        assert commits == []
        assert result == {
            "edges_proposed": 0,
            "edges_committed": 0,
            "deferred_low_confidence": [],
            "failed_paths": [],
        }
