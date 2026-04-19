"""Tests for retrieval-evaluation helpers in wikibricks.ops."""

from wikibricks.ops import (
    eval_mrr,
    eval_mrr_multi,
    eval_precision_at_k,
    eval_recall_at_k,
    eval_recall_at_k_multi,
    eval_supporting_fact_f1,
)


class TestRecallAtK:
    def test_hit_in_top_k(self):
        assert eval_recall_at_k(["a", "b", "c"], ["b"], k=3) == 1.0

    def test_miss(self):
        assert eval_recall_at_k(["a", "b"], ["x"], k=5) == 0.0

    def test_outside_top_k(self):
        assert eval_recall_at_k(["a", "b", "c", "d"], ["d"], k=2) == 0.0


class TestPrecisionAtK:
    def test_all_relevant(self):
        assert eval_precision_at_k(["a", "b"], ["a", "b"], k=2) == 1.0

    def test_none_relevant(self):
        assert eval_precision_at_k(["x", "y"], ["a"], k=2) == 0.0

    def test_empty_retrieved(self):
        assert eval_precision_at_k([], ["a"], k=5) == 0.0


class TestMRR:
    def test_first_position(self):
        assert eval_mrr(["a", "b"], ["a"]) == 1.0

    def test_second_position(self):
        assert eval_mrr(["x", "a"], ["a"]) == 0.5

    def test_no_match(self):
        assert eval_mrr(["x", "y"], ["a"]) == 0.0


class TestRecallAtKMulti:
    def test_both_in_top_k(self):
        assert eval_recall_at_k_multi(["a", "b", "c"], ["a", "b"], k=3) == 1.0

    def test_only_one_in_top_k(self):
        assert eval_recall_at_k_multi(["a", "x", "y"], ["a", "b"], k=3) == 0.5

    def test_none_in_top_k(self):
        assert eval_recall_at_k_multi(["x", "y"], ["a", "b"], k=2) == 0.0

    def test_empty_relevant_returns_one(self):
        assert eval_recall_at_k_multi(["a"], [], k=1) == 1.0


class TestMRRMulti:
    def test_returns_first_hit_rank(self):
        assert eval_mrr_multi(["x", "a", "b"], ["a", "b"]) == 0.5

    def test_no_hit(self):
        assert eval_mrr_multi(["x", "y"], ["a", "b"]) == 0.0


class TestSupportingFactF1:
    def test_perfect(self):
        assert eval_supporting_fact_f1(["a", "b"], ["a", "b"]) == 1.0

    def test_missing_one(self):
        f1 = eval_supporting_fact_f1(["a"], ["a", "b"])
        assert 0.66 < f1 < 0.67

    def test_one_extra(self):
        f1 = eval_supporting_fact_f1(["a", "b", "c"], ["a", "b"])
        assert 0.79 < f1 < 0.81

    def test_no_overlap(self):
        assert eval_supporting_fact_f1(["x"], ["a", "b"]) == 0.0

    def test_empty_both(self):
        assert eval_supporting_fact_f1([], []) == 1.0
