"""Tests for recorder page_builder — title heuristic and system-prompt detector."""

from __future__ import annotations

import pytest

from wikibricks_recorder.page_builder import (
    _looks_like_system_prompt,
    session_title,
)


@pytest.mark.parametrize("text,expected", [
    ("You are a memory consolidation agent.", True),
    ("Apply maximum non-destructive compression. Rules:", True),
    ("\n\n  You are a code reviewer.", True),
    ("Help me fix the Asana posting script", False),
    ("", False),
    ("   \n  ", False),
])
def test_looks_like_system_prompt(text, expected):
    assert _looks_like_system_prompt(text) is expected


def _state(events, first_prompt=None, session_id="abc12345-x-x-x-x"):
    return {"session_id": session_id, "events": events, "first_prompt": first_prompt}


def test_session_title_prefers_first_user_prompt():
    state = _state(
        events=[
            {"kind": "prompt", "prompt": "You are a code reviewer."},
            {"kind": "tool", "tool_name": "Read"},
            {"kind": "prompt", "prompt": "How do I add row-level security?"},
        ],
        first_prompt="You are a code reviewer.",
    )
    assert session_title(state) == "How do I add row-level security?"


def test_session_title_falls_back_when_all_prompts_are_system():
    state = _state(
        events=[{"kind": "prompt", "prompt": "You are a memory consolidation agent."}],
        first_prompt="You are a memory consolidation agent.",
    )
    assert session_title(state) == "You are a memory consolidation agent."


def test_session_title_uses_first_line_and_truncates():
    long = "a" * 200
    state = _state(events=[{"kind": "prompt", "prompt": f"Line one\n{long}"}],
                   first_prompt=f"Line one\n{long}")
    assert session_title(state) == "Line one"
    state2 = _state(events=[{"kind": "prompt", "prompt": long}], first_prompt=long)
    assert len(session_title(state2)) == 120


def test_session_title_no_prompts_falls_back_to_session_id():
    assert session_title(_state(events=[])) == "Session abc12345"
