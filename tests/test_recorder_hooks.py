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


# --- auto_summary integration ---------------------------------------------


def _flushable_state():
    """A state long enough not to be ephemeral and not utility."""
    return {
        "session_id": "abc",
        "cwd": "/Users/me/proj",
        "events": [
            {"kind": "prompt", "prompt": "refactor payments"},
            {"kind": "tool", "tool_name": "Read"},
            {"kind": "tool", "tool_name": "Edit"},
        ],
        "first_prompt": "refactor payments" + ("x" * 3000),
        "started_at": "2026-05-22T10:00:00Z",
        "model": "claude-opus",
    }


def _base_cfg():
    return {"user_id": "me", "catalog": "c", "schema": "s",
            "warehouse_id": "w", "profile": "p"}


def test_flush_passes_dense_summary_as_content_text_override():
    """When auto_summary is enabled and returns text, _flush passes it as
    content_text_override to write_page AND uses it as content.summary."""
    state = _flushable_state()
    cfg = _base_cfg()
    summary = "## Intent\n- refactor payments module"
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks.config.load_auto_tag_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_topic_keywords", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_title_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_summary_config",
               return_value={"enabled": True}), \
         patch("wikibricks_recorder.hooks.auto_summary.generate_summary",
               return_value=summary), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        client = mock_build.return_value
        _flush(state)
        kwargs = client.write_page.call_args.kwargs
        assert kwargs["content_text_override"] == summary
        assert kwargs["content_json"]["summary"] == summary
        assert "## Timeline" in kwargs["content_json"]["body"]


def test_flush_falls_back_when_summary_returns_none():
    """When auto_summary returns None (failure / short session), no
    override is passed and write_page goes through the default concat path."""
    state = _flushable_state()
    cfg = _base_cfg()
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks.config.load_auto_tag_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_topic_keywords", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_title_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_summary_config",
               return_value={"enabled": True}), \
         patch("wikibricks_recorder.hooks.auto_summary.generate_summary",
               return_value=None), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        client = mock_build.return_value
        _flush(state)
        kwargs = client.write_page.call_args.kwargs
        assert kwargs.get("content_text_override") is None


def test_flush_disabled_auto_summary_skips_llm_call():
    """When auto_summary is disabled in config, generate_summary is NOT
    called, and content_text_override stays None."""
    state = _flushable_state()
    cfg = _base_cfg()
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks.config.load_auto_tag_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_topic_keywords", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_title_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_summary_config", return_value={}), \
         patch("wikibricks_recorder.hooks.auto_summary.generate_summary") as mock_gen, \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        client = mock_build.return_value
        _flush(state)
        mock_gen.assert_not_called()
        assert client.write_page.call_args.kwargs.get("content_text_override") is None


def test_flush_swallows_summary_exception_and_falls_back():
    """If generate_summary raises, _flush logs and falls back; write still
    happens with no override."""
    state = _flushable_state()
    cfg = _base_cfg()
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks.config.load_auto_tag_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_topic_keywords", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_title_config", return_value={}), \
         patch("wikibricks_recorder.hooks.config.load_auto_summary_config",
               return_value={"enabled": True}), \
         patch("wikibricks_recorder.hooks.auto_summary.generate_summary",
               side_effect=RuntimeError("boom")), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        client = mock_build.return_value
        _flush(state)
        client.write_page.assert_called_once()
        assert client.write_page.call_args.kwargs.get("content_text_override") is None
