"""Behavioral tests for `wikibricks.curate_logic`.

Every feature exercised by `notebooks/wiki_curate.py` has a test here that runs
without a workspace, SDK, or LLM.
"""

from wikibricks.curate_logic import build_curate_summary, partition_by_confidence


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
