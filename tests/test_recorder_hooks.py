"""Tests for the utility-session filter and the _flush short-circuit."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wikibricks_recorder.hooks import _flush, _is_utility_session


def _state(*, cwd=None, events=None, first_prompt=None):
    return {
        "session_id": "abc12345-x-x-x-x",
        "cwd": cwd,
        "events": events or [],
        "first_prompt": first_prompt,
    }


@pytest.mark.parametrize("cwd", [
    "/private/var/folders/1q/abc/T",
    "/var/folders/abc/T",
    "/tmp/working",
    "/private/tmp",
    "/private/tmp/somework",
    "/tmp",
])
def test_tmp_cwd_is_utility(cwd):
    assert _is_utility_session(_state(cwd=cwd, events=[{"kind": "prompt"}]))


def test_single_system_prompt_is_utility():
    state = _state(
        cwd="/Users/me/proj",
        events=[{"kind": "prompt", "prompt": "You are a code reviewer."}],
        first_prompt="You are a code reviewer.",
    )
    assert _is_utility_session(state)


def test_single_user_prompt_is_not_utility():
    state = _state(
        cwd="/Users/me/proj",
        events=[{"kind": "prompt", "prompt": "How do I MERGE in Delta?"}],
        first_prompt="How do I MERGE in Delta?",
    )
    assert not _is_utility_session(state)


def test_multi_turn_session_is_not_utility():
    state = _state(
        cwd="/Users/me/proj",
        events=[
            {"kind": "prompt", "prompt": "You are a code reviewer."},
            {"kind": "tool"},
            {"kind": "prompt", "prompt": "review the new function"},
        ],
        first_prompt="You are a code reviewer.",
    )
    assert not _is_utility_session(state)


def test_empty_state_is_not_utility():
    assert not _is_utility_session(_state())


def test_flush_skips_utility_session():
    state = _state(
        cwd="/private/var/folders/1q/abc/T",
        events=[{"kind": "prompt", "prompt": "You are a memory consolidation agent."}],
        first_prompt="You are a memory consolidation agent.",
    )
    with patch("wikibricks_recorder.hooks.config.load_config") as mock_load:
        _flush(state)
        mock_load.assert_not_called()


def test_flush_skips_empty_session():
    with patch("wikibricks_recorder.hooks.config.load_config") as mock_load:
        _flush(_state(cwd="/Users/me/proj"))
        mock_load.assert_not_called()


def test_flush_writes_real_session():
    state = {
        "session_id": "abc",
        "cwd": "/Users/me/proj",
        "events": [
            {"kind": "prompt", "prompt": "How do I add a Lakeflow Job?", "ts": "2026-05-12T10:00:00Z"},
            {"kind": "tool", "tool_name": "Read", "ts": "2026-05-12T10:00:05Z"},
        ],
        "first_prompt": "How do I add a Lakeflow Job?",
        "started_at": "2026-05-12T10:00:00Z",
        "model": "claude-opus-4-7",
    }
    cfg = {
        "user_id": "me",
        "catalog": "c",
        "schema": "s",
        "warehouse_id": "w",
        "profile": "p",
    }
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        _flush(state)
        mock_build.assert_called_once_with(cfg)
        mock_build.return_value.write_page.assert_called_once()
        assert mock_build.return_value.write_page.call_args.kwargs["title"] \
            == "How do I add a Lakeflow Job?"


def test_flush_with_auto_tag_persists_slugs_and_adds_customer_tags(monkeypatch):
    """When auto_tag is enabled and the LLM returns slugs, those slugs are
    persisted to wiki_vocabulary AND attached as customer:<slug> tags."""
    # v0.7.3+: is_ephemeral skips <2-event sessions; relax for this fixture.
    monkeypatch.setenv("WIKIBRICKS_RECORDER_MIN_EVENTS", "0")
    state = {
        "session_id": "abc",
        "cwd": "/Users/me/proj",
        "events": [{"kind": "prompt", "prompt": "Tell me about Solvd and AZ CH"}],
        "first_prompt": "Tell me about Solvd and AZ CH",
        "started_at": "2026-05-12T10:00:00Z",
        "model": "m",
    }
    cfg = {"user_id": "me", "catalog": "c", "schema": "s",
           "warehouse_id": "w", "profile": "p"}
    auto_cfg = {"enabled": True, "endpoint": "databricks-claude-haiku-4-5"}
    extracted = ["Solvd Group", "AZ CH"]  # raw LLM output; gets normalized
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks.config.load_auto_tag_config", return_value=auto_cfg), \
         patch("wikibricks_recorder.hooks.config.load_topic_keywords", return_value={}), \
         patch("wikibricks_recorder.hooks.auto_tag.extract_topic_slugs", return_value=extracted), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        client = mock_build.return_value
        client._normalize_slug.side_effect = lambda s: s.lower().replace(" ", "-")
        _flush(state)
    # vocabulary upsert called with the raw slugs (normalization happens server-side)
    client.upsert_vocabulary_slugs.assert_called_once_with(extracted, source="llm")
    # write_page called with customer tags
    tags = client.write_page.call_args.kwargs["tags"]
    assert "customer:solvd-group" in tags
    assert "customer:az-ch" in tags


def test_flush_auto_tag_disabled_does_not_call_extract():
    state = {
        "session_id": "abc",
        "cwd": "/Users/me/proj",
        "events": [{"kind": "prompt", "prompt": "p"}],
        "first_prompt": "p",
        "started_at": "2026-05-12T10:00:00Z",
        "model": "m",
    }
    cfg = {"user_id": "me", "catalog": "c", "schema": "s",
           "warehouse_id": "w", "profile": "p"}
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks.config.load_auto_tag_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_topic_keywords", return_value={}), \
         patch("wikibricks_recorder.hooks.auto_tag.extract_topic_slugs") as mock_extract, \
         patch("wikibricks_recorder.hooks._build_wiki_client"):
        _flush(state)
    mock_extract.assert_not_called()
