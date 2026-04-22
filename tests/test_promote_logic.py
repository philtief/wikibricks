"""Behavioral tests for `wikibricks.promote_logic`.

Covers every deterministic decision `notebooks/promote_from_traces.py` makes:
clustering, eligibility filtering, judge-score parsing, dedup detection.
"""

from datetime import datetime, timedelta, timezone

import pytest

from wikibricks.promote_logic import (
    cluster_by_cosine,
    cosine,
    filter_eligible_clusters,
    get_promote_window,
    is_duplicate_hit,
    parse_judge_score,
)


class TestCosine:
    def test_identical_vectors_return_one(self):
        assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_return_zero(self):
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_return_minus_one(self):
        assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero_not_nan(self):
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
        assert cosine([1.0, 1.0], [0.0, 0.0]) == 0.0

    def test_is_scale_invariant(self):
        a = [1.0, 2.0, 3.0]
        b = [2.0, 4.0, 6.0]
        assert cosine(a, b) == pytest.approx(1.0)


class TestClusterByCosine:
    def test_all_identical_embeddings_collapse_to_one_cluster(self):
        rows = [{"id": i, "embedding": [1.0, 0.0]} for i in range(5)]
        clusters = cluster_by_cosine(rows, threshold=0.9)
        assert len(clusters) == 1
        assert len(clusters[0]) == 5

    def test_orthogonal_embeddings_stay_separate(self):
        rows = [
            {"id": 1, "embedding": [1.0, 0.0]},
            {"id": 2, "embedding": [0.0, 1.0]},
            {"id": 3, "embedding": [-1.0, 0.0]},
        ]
        clusters = cluster_by_cosine(rows, threshold=0.5)
        assert len(clusters) == 3

    def test_threshold_controls_grouping(self):
        rows = [
            {"id": 1, "embedding": [1.0, 0.0]},
            {"id": 2, "embedding": [1.0, 0.1]},   # cos ~ 0.995
            {"id": 3, "embedding": [1.0, 2.0]},   # cos ~ 0.447
        ]
        tight = cluster_by_cosine(rows, threshold=0.99)
        loose = cluster_by_cosine(rows, threshold=0.4)
        assert len(tight) == 2
        assert len(loose) == 1

    def test_empty_input(self):
        assert cluster_by_cosine([], threshold=0.5) == []

    def test_rows_join_the_first_matching_cluster(self):
        rows = [
            {"id": "a", "embedding": [1.0, 0.0]},
            {"id": "b", "embedding": [0.0, 1.0]},
            {"id": "c", "embedding": [0.99, 0.01]},  # close to 'a'
        ]
        clusters = cluster_by_cosine(rows, threshold=0.9)
        ids = [[r["id"] for r in c] for c in clusters]
        assert ["a", "c"] in ids


class TestFilterEligibleClusters:
    def _cluster(self, session_ids):
        return [{"session_id": s, "query": "q"} for s in session_ids]

    def test_drops_clusters_below_member_floor(self):
        small = self._cluster(["s1", "s2"])
        big = self._cluster(["s1", "s2", "s3", "s4", "s5"])
        eligible = filter_eligible_clusters(
            [small, big], min_members=5, min_distinct_sessions=1, max_clusters=10
        )
        assert eligible == [big]

    def test_drops_clusters_below_distinct_session_floor(self):
        # 6 members but only 2 distinct sessions
        cluster = self._cluster(["s1", "s1", "s1", "s2", "s2", "s2"])
        eligible = filter_eligible_clusters(
            [cluster], min_members=5, min_distinct_sessions=3, max_clusters=10
        )
        assert eligible == []

    def test_caps_at_max_clusters(self):
        clusters = [self._cluster([f"s{i}_{j}" for j in range(5)]) for i in range(10)]
        eligible = filter_eligible_clusters(
            clusters, min_members=5, min_distinct_sessions=3, max_clusters=3
        )
        assert len(eligible) == 3

    def test_preserves_order(self):
        a = self._cluster(["a1", "a2", "a3", "a4", "a5"])
        b = self._cluster(["b1", "b2", "b3", "b4", "b5"])
        eligible = filter_eligible_clusters(
            [a, b], min_members=5, min_distinct_sessions=3, max_clusters=10
        )
        assert eligible == [a, b]


class TestParseJudgeScore:
    @pytest.mark.parametrize("text,expected", [
        ("5", 5.0),
        ("4", 4.0),
        ("1", 1.0),
        ("5.", 5.0),
        ("  3 ", 3.0),
        ("4/5", 4.0),
        ("", 0.0),
        ("   ", 0.0),
        ("five", 0.0),
        ("I think 5", 0.0),  # must start with digit
        ("-3", 0.0),  # leading minus is not a digit
    ])
    def test_parses_expected_value(self, text, expected):
        assert parse_judge_score(text) == expected


class TestGetPromoteWindow:
    def _t(self, h):
        return datetime(2026, 4, 22, h, tzinfo=timezone.utc)

    def test_first_run_reads_max_lookback(self):
        now = self._t(12)
        start, end = get_promote_window(None, now, max_lookback=timedelta(days=7))
        assert end == now
        assert start == now - timedelta(days=7)

    def test_steady_state_reads_from_last_watermark(self):
        last = self._t(6)
        now = self._t(12)
        start, end = get_promote_window(last, now, max_lookback=timedelta(days=7))
        assert start == last
        assert end == now

    def test_large_gap_is_capped_at_max_lookback(self):
        # Last run was 90 days ago; cap the catch-up to 7 days.
        now = datetime(2026, 4, 22, 12, tzinfo=timezone.utc)
        last = now - timedelta(days=90)
        start, end = get_promote_window(last, now, max_lookback=timedelta(days=7))
        assert start == now - timedelta(days=7)
        assert end == now

    def test_future_watermark_returns_zero_width(self):
        # Clock skew or replay: watermark is ahead of now. Window must be empty.
        now = self._t(6)
        future = self._t(12)
        start, end = get_promote_window(future, now)
        assert start == end == now


class TestIsDuplicateHit:
    def test_none_is_not_duplicate(self):
        assert is_duplicate_hit(None) is False

    def test_empty_dict_is_not_duplicate(self):
        assert is_duplicate_hit({}) is False

    def test_matching_prefix_and_high_score_is_duplicate(self):
        hit = {"path": "promoted/what-is-vector-search", "score": 0.95}
        assert is_duplicate_hit(hit) is True

    def test_wrong_prefix_not_duplicate_even_with_high_score(self):
        hit = {"path": "topics/vector-search", "score": 0.99}
        assert is_duplicate_hit(hit) is False

    def test_right_prefix_but_low_score_not_duplicate(self):
        hit = {"path": "promoted/x", "score": 0.50}
        assert is_duplicate_hit(hit) is False

    def test_score_at_threshold_is_not_duplicate_strict_greater(self):
        # Implementation uses > 0.9, so exactly 0.9 must NOT qualify.
        hit = {"path": "promoted/x", "score": 0.9}
        assert is_duplicate_hit(hit) is False

    def test_configurable_threshold_and_prefix(self):
        hit = {"path": "canonical/foo", "score": 0.81}
        assert is_duplicate_hit(hit, score_threshold=0.8, path_prefix="canonical/") is True
