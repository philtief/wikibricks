"""Tests for the LLM-based topic-slug extraction in the recorder."""

from __future__ import annotations

from unittest.mock import MagicMock

from wikibricks_recorder.auto_tag import (
    _parse_response,
    _sample_prompts,
    extract_topic_slugs,
    is_enabled,
)

# -------- _sample_prompts -----------------------------------------------

def test_sample_concats_prompt_events_only():
    state = {
        "events": [
            {"kind": "prompt", "prompt": "first"},
            {"kind": "tool", "tool_name": "Read"},
            {"kind": "prompt", "prompt": "second"},
        ]
    }
    assert "first" in _sample_prompts(state)
    assert "second" in _sample_prompts(state)
    assert "Read" not in _sample_prompts(state)


def test_sample_truncates_at_max_chars():
    state = {"events": [{"kind": "prompt", "prompt": "x" * 5000}]}
    out = _sample_prompts(state, max_chars=100)
    assert len(out) <= 100


def test_sample_empty_when_no_prompts():
    assert _sample_prompts({"events": []}) == ""


# -------- _parse_response -----------------------------------------------

def test_parse_valid_json_array():
    assert _parse_response('["solvd", "az-ch"]') == ["solvd", "az-ch"]


def test_parse_with_markdown_code_fence():
    assert _parse_response('```json\n["solvd"]\n```') == ["solvd"]


def test_parse_with_bare_code_fence():
    assert _parse_response('```\n["solvd"]\n```') == ["solvd"]


def test_parse_returns_empty_on_malformed():
    assert _parse_response("not json") == []
    assert _parse_response('{"not": "array"}') == []
    assert _parse_response("") == []


def test_parse_drops_non_strings():
    assert _parse_response('["solvd", 42, null, "az-ch"]') == ["solvd", "az-ch"]


# -------- is_enabled ----------------------------------------------------

def test_is_enabled_default_false():
    assert is_enabled({}) is False


def test_is_enabled_explicit_true():
    assert is_enabled({"enabled": True}) is True


def test_is_enabled_explicit_false():
    assert is_enabled({"enabled": False}) is False


# -------- extract_topic_slugs (orchestration) ---------------------------

def _make_ws_with_response(content: str):
    ws = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    ws.serving_endpoints.query.return_value = response
    return ws


def test_returns_empty_when_disabled():
    ws = _make_ws_with_response('["solvd"]')
    state = {"events": [{"kind": "prompt", "prompt": "Tell me about Solvd"}]}
    assert extract_topic_slugs(state, {"enabled": False}, ws) == []
    ws.serving_endpoints.query.assert_not_called()


def test_returns_empty_when_no_prompts():
    ws = _make_ws_with_response('["solvd"]')
    assert extract_topic_slugs({"events": []}, {"enabled": True}, ws) == []
    ws.serving_endpoints.query.assert_not_called()


def test_returns_extracted_slugs_on_success():
    ws = _make_ws_with_response('["solvd", "az-ch"]')
    state = {"events": [{"kind": "prompt", "prompt": "Tell me about Solvd and AZ CH"}]}
    out = extract_topic_slugs(state, {"enabled": True}, ws)
    assert out == ["solvd", "az-ch"]


def test_passes_configured_endpoint():
    ws = _make_ws_with_response('[]')
    state = {"events": [{"kind": "prompt", "prompt": "p"}]}
    extract_topic_slugs(state, {"enabled": True, "endpoint": "my-custom-endpoint"}, ws)
    assert ws.serving_endpoints.query.call_args.kwargs["name"] == "my-custom-endpoint"


def test_returns_empty_on_endpoint_error():
    ws = MagicMock()
    ws.serving_endpoints.query.side_effect = RuntimeError("boom")
    state = {"events": [{"kind": "prompt", "prompt": "p"}]}
    assert extract_topic_slugs(state, {"enabled": True}, ws) == []
