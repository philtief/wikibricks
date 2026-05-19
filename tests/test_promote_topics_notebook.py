"""Drift-guard tests for notebooks/promote_topics.py.

Asserts the contract between the notebook and the library: clustering by
community_id, judge threshold gate, opt-in via min_corpus_size, idempotent
write path.
"""

from __future__ import annotations

import pathlib

_NB = pathlib.Path(__file__).parent.parent / "notebooks" / "promote_topics.py"


def _src() -> str:
    return _NB.read_text()


def test_notebook_exists():
    assert _NB.exists(), f"missing {_NB}"


def test_uses_cluster_pages_by_community():
    src = _src()
    assert "cluster_pages_by_community" in src


def test_uses_topic_slug_from_titles():
    src = _src()
    assert "topic_slug_from_titles" in src


def test_filters_pages_with_null_community():
    src = _src()
    assert "community_id IS NOT NULL" in src


def test_excludes_ephemeral_stub_pages():
    src = _src()
    assert "ephemeral:stub" in src


def test_writes_to_topics_path():
    src = _src()
    assert 'f"topics/{slug}"' in src or "topics/{slug}" in src


def test_logs_promote_topic_op_type():
    src = _src()
    assert "promote_topic" in src
    assert "promote_topic_reject" in src


def test_corpus_size_guard():
    src = _src()
    assert "min_corpus_size" in src
    assert "dbutils.notebook.exit" in src


def test_judge_threshold_gate():
    src = _src()
    assert "judge_threshold" in src
    assert "score < judge_threshold" in src or "score >= judge_threshold" in src


def test_max_topics_per_run_caps_writes():
    src = _src()
    assert "max_topics_per_run" in src


def test_uses_chat_message_pattern():
    """LLM calls should mirror auto_tag.py's pattern (ChatMessage + serving_endpoints.query)."""
    src = _src()
    assert "ChatMessage" in src
    assert "serving_endpoints.query" in src


def test_writes_topic_tag():
    src = _src()
    assert '"topic"' in src or "'topic'" in src
    assert "synthesised" in src
