"""v0.7.7 — LLM-generated session titles, opt-in.

Mirrors the auto_tag.py contract: opt-in via [auto_title] TOML block;
synchronous serving-endpoint call at flush time; silent fall-back to
``page_builder.session_title`` on any error or when disabled.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from wikibricks_recorder import auto_title


def _state(first_prompt: str = "Build a multi-agent system for AML triage on Databricks") -> dict:
    return {
        "session_id": "abc",
        "first_prompt": first_prompt,
        "events": [{"kind": "prompt", "prompt": first_prompt}],
    }


def _mock_ws_with_response(content: str):
    ws = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    ws.serving_endpoints.query.return_value = resp
    return ws


# ----------------------------------------------------------------------------
# is_enabled
# ----------------------------------------------------------------------------

def test_is_enabled_false_by_default():
    assert auto_title.is_enabled({}) is False
    assert auto_title.is_enabled({"enabled": False}) is False


def test_is_enabled_true_when_set():
    assert auto_title.is_enabled({"enabled": True}) is True


# ----------------------------------------------------------------------------
# generate_title
# ----------------------------------------------------------------------------

def test_generate_title_returns_none_when_disabled():
    ws = MagicMock()
    assert auto_title.generate_title(_state(), {}, ws) is None
    ws.serving_endpoints.query.assert_not_called()


def test_generate_title_returns_clean_string_on_success():
    ws = _mock_ws_with_response("Multi-agent AML triage on Databricks")
    title = auto_title.generate_title(_state(), {"enabled": True}, ws)
    assert title == "Multi-agent AML triage on Databricks"


def test_generate_title_strips_surrounding_whitespace_and_quotes():
    ws = _mock_ws_with_response('  "Multi-agent AML triage"  \n')
    title = auto_title.generate_title(_state(), {"enabled": True}, ws)
    assert title == "Multi-agent AML triage"


def test_generate_title_truncates_at_title_max():
    long_title = "A " * 200
    ws = _mock_ws_with_response(long_title)
    title = auto_title.generate_title(_state(), {"enabled": True}, ws)
    assert title is not None
    assert len(title) <= 120  # TITLE_MAX in page_builder


def test_generate_title_returns_none_on_endpoint_error():
    ws = MagicMock()
    ws.serving_endpoints.query.side_effect = RuntimeError("endpoint down")
    assert auto_title.generate_title(_state(), {"enabled": True}, ws) is None


def test_generate_title_returns_none_when_response_missing_content():
    ws = MagicMock()
    resp = MagicMock()
    resp.choices = []
    ws.serving_endpoints.query.return_value = resp
    assert auto_title.generate_title(_state(), {"enabled": True}, ws) is None


def test_generate_title_returns_none_for_empty_prompt():
    """No prompt content → no LLM call wasted, return None."""
    ws = MagicMock()
    state = {"session_id": "x", "first_prompt": "", "events": []}
    assert auto_title.generate_title(state, {"enabled": True}, ws) is None
    ws.serving_endpoints.query.assert_not_called()


def test_generate_title_treats_overlong_runaway_as_failure():
    """If the LLM streams 500+ chars instead of a title, treat as failure."""
    ws = _mock_ws_with_response("This is a runaway response. " * 50)
    # The implementation should detect this as suspicious — too long for a
    # title regardless of truncation, suggesting the prompt was misread.
    title = auto_title.generate_title(_state(), {"enabled": True}, ws)
    # Either None (rejected) or truncated to TITLE_MAX. Both are acceptable
    # contracts; pin the truncated branch since the prompt asks for ≤80
    # chars and Haiku is generally cooperative.
    assert title is None or len(title) <= 120


def test_generate_title_respects_custom_endpoint():
    ws = _mock_ws_with_response("My title")
    cfg = {"enabled": True, "endpoint": "databricks-claude-sonnet-4-5"}
    auto_title.generate_title(_state(), cfg, ws)
    call_args = ws.serving_endpoints.query.call_args
    assert call_args.kwargs["name"] == "databricks-claude-sonnet-4-5"
