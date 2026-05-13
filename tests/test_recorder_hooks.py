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
