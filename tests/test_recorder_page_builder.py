"""Tests for recorder page_builder — title heuristic and system-prompt detector."""

from __future__ import annotations

import pytest

from wikibricks_recorder.page_builder import (
    _looks_like_system_prompt,
    session_content,
    session_tags,
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
    # v0.7.3 behaviour change: when every line of the prompt is LLM
    # instruction boilerplate, fall back to the session-id stub rather
    # than mis-titling the page with the system prompt itself.
    state = _state(
        events=[{"kind": "prompt", "prompt": "You are a memory consolidation agent."}],
        first_prompt="You are a memory consolidation agent.",
    )
    assert session_title(state).startswith("Session ")


def test_session_title_uses_first_line_and_truncates():
    long = "a" * 200
    state = _state(events=[{"kind": "prompt", "prompt": f"Line one\n{long}"}],
                   first_prompt=f"Line one\n{long}")
    assert session_title(state) == "Line one"
    state2 = _state(events=[{"kind": "prompt", "prompt": long}], first_prompt=long)
    assert len(session_title(state2)) == 120


def test_session_title_no_prompts_falls_back_to_session_id():
    assert session_title(_state(events=[])) == "Session abc12345"


# ---- customer auto-tagging (feature 2) ---------------------------------


def _tagstate(*, cwd=None, first_prompt=None, events=None, session_id="s", model=None):
    return {
        "session_id": session_id,
        "cwd": cwd,
        "first_prompt": first_prompt,
        "events": events or [],
        "model": model,
    }


def test_session_tags_no_keywords_no_customer_tag():
    tags = session_tags(_tagstate(first_prompt="Tell me about Solvd"))
    assert not any(t.startswith("customer:") for t in tags)


def test_session_tags_matches_first_prompt():
    state = _tagstate(first_prompt="Tell me about Solvd Lakebase")
    tags = session_tags(state, topic_keywords={"solvd": ["solvd"]})
    assert "customer:solvd" in tags


def test_session_tags_matches_later_event_prompt():
    state = _tagstate(
        first_prompt="Generic intro",
        events=[
            {"kind": "prompt", "prompt": "Generic intro"},
            {"kind": "prompt", "prompt": "now switch to Allianz Italy"},
        ],
    )
    tags = session_tags(state, topic_keywords={"allianz-italy": ["allianz italy"]})
    assert "customer:allianz-italy" in tags


def test_session_tags_no_match_no_tag():
    tags = session_tags(
        _tagstate(first_prompt="Tell me about something else"),
        topic_keywords={"solvd": ["solvd"]},
    )
    assert not any(t.startswith("customer:") for t in tags)


def test_session_tags_multiple_customers():
    state = _tagstate(first_prompt="Solvd vs Allianz Italy comparison")
    tags = session_tags(state, topic_keywords={
        "solvd": ["solvd"],
        "allianz-italy": ["allianz italy"],
    })
    assert "customer:solvd" in tags
    assert "customer:allianz-italy" in tags


def test_session_content_uses_dense_summary_when_provided():
    """When the recorder supplies a dense LLM-generated summary, it
    replaces the default truncated-first-prompt summary in
    content.summary. The raw transcript still flows into content.body."""
    state = {
        "session_id": "abc12345-def",
        "first_prompt": "ignore me — should not appear as summary",
        "events": [
            {"kind": "prompt", "ts": "2026-05-22T10:00:00Z", "prompt": "x"},
        ],
        "started_at": "2026-05-22T10:00:00Z",
        "cwd": "/home/u",
        "model": "claude-opus",
    }
    dense = "## Intent\n- refactor payment module\n## Outcome\n- done"
    content = session_content(state, dense_summary=dense)
    assert content["summary"] == dense
    assert "## Timeline" in content["body"]
    assert "prompt @" in content["body"]


def test_session_content_falls_back_to_default_summary_when_no_dense():
    """Without dense_summary, content.summary is the legacy truncated
    first_prompt."""
    state = {
        "session_id": "abc12345-def",
        "first_prompt": "Refactor payments",
        "events": [
            {"kind": "prompt", "ts": "2026-05-22T10:00:00Z", "prompt": "x"},
        ],
        "started_at": "2026-05-22T10:00:00Z",
        "cwd": "/home/u",
        "model": "claude-opus",
    }
    content = session_content(state)
    assert content["summary"].startswith("Refactor payments")
    assert "## Timeline" in content["body"]


def test_session_content_dense_summary_empty_string_falls_through():
    """Empty-string dense_summary is treated as 'no summary' so the
    legacy default kicks in. Avoids accidentally embedding an empty
    string when the LLM returns blank."""
    state = {
        "session_id": "abc",
        "first_prompt": "Real first prompt",
        "events": [{"kind": "prompt", "ts": "t", "prompt": "x"}],
    }
    content = session_content(state, dense_summary="")
    assert content["summary"].startswith("Real first prompt")
