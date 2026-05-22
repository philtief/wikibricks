"""Unit tests for the auto_summary module.

Pure helpers tested first; LLM-call path (generate_summary) tested
separately at the bottom by monkey-patching workspace_client.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from wikibricks_recorder import auto_summary

# --- Pure helpers -----------------------------------------------------------


def test_is_enabled_default_false():
    assert auto_summary.is_enabled({}) is False
    assert auto_summary.is_enabled({"enabled": False}) is False
    assert auto_summary.is_enabled({"enabled": True}) is True


def test_sample_transcript_includes_first_prompt_and_recent_events():
    state = {
        "first_prompt": "build a thing",
        "events": [
            {"kind": "prompt", "prompt": "now refine it", "ts": "2026-05-22T10:00:00Z"},
            {"kind": "tool", "tool_name": "Read", "ts": "2026-05-22T10:01:00Z"},
            {"kind": "tool", "tool_name": "Edit", "ts": "2026-05-22T10:02:00Z"},
        ],
    }
    sample = auto_summary._sample_transcript(state, max_chars=2000)
    assert "build a thing" in sample
    assert "now refine it" in sample
    assert "Read" in sample
    assert "Edit" in sample


def test_sample_transcript_truncates_to_max_chars():
    state = {"first_prompt": "x" * 5000, "events": []}
    sample = auto_summary._sample_transcript(state, max_chars=100)
    assert len(sample) <= 100


def test_sample_transcript_returns_empty_for_empty_state():
    assert auto_summary._sample_transcript({"events": []}, max_chars=100) == ""


def test_sample_transcript_excludes_first_prompt_duplicate_from_later_section():
    """If the first event happens to repeat first_prompt verbatim, the
    later-prompts section shouldn't duplicate it."""
    fp = "refactor the foo module"
    state = {
        "first_prompt": fp,
        "events": [
            {"kind": "prompt", "prompt": fp, "ts": "x"},
            {"kind": "prompt", "prompt": "now add a test", "ts": "y"},
        ],
    }
    sample = auto_summary._sample_transcript(state, max_chars=2000)
    # first_prompt section + later-prompts section — the duplicate
    # should only appear once.
    assert sample.count(fp) == 1
    assert "now add a test" in sample


def test_clean_summary_strips_code_fences():
    raw = "```markdown\n## Intent\n- build\n```"
    assert auto_summary._clean_summary(raw) == "## Intent\n- build"


def test_clean_summary_strips_generic_code_fence():
    raw = "```\n## Intent\n- build\n```"
    assert auto_summary._clean_summary(raw) == "## Intent\n- build"


def test_clean_summary_returns_none_for_empty_and_whitespace():
    assert auto_summary._clean_summary("   \n  \n") is None
    assert auto_summary._clean_summary("") is None
    assert auto_summary._clean_summary(None) is None


def test_clean_summary_caps_length():
    raw = "## Intent\n" + ("x" * 10_000)
    cleaned = auto_summary._clean_summary(raw)
    assert cleaned is not None
    assert len(cleaned) <= auto_summary._SUMMARY_MAX_CHARS


def test_should_summarize_short_session_returns_false():
    state = {"first_prompt": "hi", "events": [{"kind": "prompt", "prompt": "hi"}]}
    assert auto_summary._should_summarize(state) is False


def test_should_summarize_long_session_returns_true():
    long_prompt = "x" * 2500
    state = {
        "first_prompt": long_prompt,
        "events": [
            {"kind": "prompt", "prompt": long_prompt},
            {"kind": "tool", "tool_name": "Read"},
        ],
    }
    assert auto_summary._should_summarize(state) is True


# --- LLM-call helpers -------------------------------------------------------


def _mock_ws(content: str | None):
    """Build a workspace_client mock whose serving_endpoints.query returns
    a fake response with the given content (or simulates failure)."""
    ws = MagicMock()
    if content is None:
        ws.serving_endpoints.query.side_effect = RuntimeError("endpoint down")
    else:
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=content))]
        ws.serving_endpoints.query.return_value = resp
    return ws


def _long_state():
    """A state long enough to trip the min-chars threshold."""
    return {
        "first_prompt": "Refactor the payment module" + ("x" * 3000),
        "events": [
            {"kind": "prompt", "prompt": "also add a test"},
            {"kind": "tool", "tool_name": "Read"},
            {"kind": "tool", "tool_name": "Edit"},
        ],
    }


def test_generate_summary_returns_none_when_disabled():
    result = auto_summary.generate_summary(
        _long_state(), {"enabled": False}, _mock_ws("ok")
    )
    assert result is None


def test_generate_summary_returns_none_for_short_session():
    short = {"first_prompt": "hi", "events": []}
    result = auto_summary.generate_summary(
        short, {"enabled": True}, _mock_ws("## Intent\n- x")
    )
    assert result is None


def test_generate_summary_happy_path():
    ws = _mock_ws("## Intent\n- refactor payment\n## Approach\n- edit foo.py")
    result = auto_summary.generate_summary(
        _long_state(), {"enabled": True}, ws
    )
    assert result == "## Intent\n- refactor payment\n## Approach\n- edit foo.py"
    call = ws.serving_endpoints.query.call_args
    assert call.kwargs["name"] == "databricks-claude-haiku-4-5"
    assert len(call.kwargs["messages"]) == 2


def test_generate_summary_uses_custom_endpoint():
    ws = _mock_ws("## Intent\n- x")
    auto_summary.generate_summary(
        _long_state(),
        {"enabled": True, "endpoint": "my-haiku"},
        ws,
    )
    assert ws.serving_endpoints.query.call_args.kwargs["name"] == "my-haiku"


def test_generate_summary_swallows_endpoint_errors():
    ws = _mock_ws(None)  # raises RuntimeError
    result = auto_summary.generate_summary(
        _long_state(), {"enabled": True}, ws
    )
    assert result is None


def test_generate_summary_swallows_malformed_response():
    ws = MagicMock()
    ws.serving_endpoints.query.return_value = MagicMock(choices=[])
    result = auto_summary.generate_summary(
        _long_state(), {"enabled": True}, ws
    )
    assert result is None


def test_generate_summary_strips_code_fences():
    ws = _mock_ws("```markdown\n## Intent\n- x\n```")
    result = auto_summary.generate_summary(
        _long_state(), {"enabled": True}, ws
    )
    assert result == "## Intent\n- x"


def test_generate_summary_returns_none_for_blank_response():
    ws = _mock_ws("   \n\n   ")
    result = auto_summary.generate_summary(
        _long_state(), {"enabled": True}, ws
    )
    assert result is None


# --- build_content_text_override (v0.7.9 intent-tail composition) ----------


def test_build_override_combines_summary_and_first_prompt():
    state = {"first_prompt": "refactor payments to use stripe.Webhook.construct_event"}
    summary = "## Intent\n- refactor"
    override = auto_summary.build_content_text_override(state, summary)
    assert override.startswith(summary)
    assert "## Raw intent" in override
    assert "stripe.Webhook.construct_event" in override


def test_build_override_caps_first_prompt_at_2000_chars():
    state = {"first_prompt": "x" * 5000}
    summary = "## Intent\n- y"
    override = auto_summary.build_content_text_override(state, summary)
    # summary + "\n\n## Raw intent\n" header + capped 2000 chars
    assert override.endswith("x" * 2000)
    # total length: summary (~14) + header (~17) + 2000 ≈ 2031
    assert len(override) <= len(summary) + 17 + 2000


def test_build_override_returns_summary_when_no_first_prompt():
    state = {"first_prompt": ""}
    summary = "## Intent\n- y"
    assert auto_summary.build_content_text_override(state, summary) == summary


def test_build_override_returns_empty_when_summary_empty():
    state = {"first_prompt": "anything"}
    assert auto_summary.build_content_text_override(state, "") == ""


def test_build_override_strips_whitespace_in_first_prompt():
    """Leading/trailing whitespace on first_prompt shouldn't waste tail budget."""
    state = {"first_prompt": "   refactor payments   \n\n   "}
    summary = "S"
    override = auto_summary.build_content_text_override(state, summary)
    assert "refactor payments" in override
    # The header should still appear exactly once
    assert override.count("## Raw intent") == 1
