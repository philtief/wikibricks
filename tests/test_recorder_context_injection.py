"""Tests for the proactive context-injection path in on_user_prompt_submit."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

from wikibricks_recorder.hooks import _emit_relevant_context


def _capture(fn, *a, **kw) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


def test_no_emission_when_env_var_off(monkeypatch):
    monkeypatch.delenv("WIKIBRICKS_INJECT_CONTEXT", raising=False)
    out = _capture(_emit_relevant_context, "sid1", "How do I MERGE in Delta?")
    assert out == ""


def test_no_emission_on_short_prompt(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    out = _capture(_emit_relevant_context, "sid1", "go")
    assert out == ""


def test_emits_additional_context_for_normal_prompt(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    fake_cfg = {"user_id": "me", "catalog": "c", "schema": "s",
                "warehouse_id": "w", "profile": "p"}
    fake_hits = [
        {"path": "sessions/2026/05/04/abc/chunks/01",
         "title": "Solvd Lakebase migration scope",
         "content_text": "Bach Ha and Daniel Kroll attended the kickoff..."},
        {"path": "sessions/2026/05/08/xyz",
         "title": "Solvd control plane decisions",
         "content_text": "Discussed model serving topology and routing..."},
    ]
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=fake_cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        mock_build.return_value.search.return_value = fake_hits
        out = _capture(_emit_relevant_context, "sid-current", "Tell me about Solvd Lakebase")
    assert out != ""
    payload = json.loads(out.strip())
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Solvd Lakebase migration scope" in context
    assert "Solvd control plane decisions" in context


def test_filters_hits_from_current_session(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    fake_cfg = {"user_id": "me", "catalog": "c", "schema": "s",
                "warehouse_id": "w", "profile": "p"}
    fake_hits = [
        {"path": "sessions/2026/05/12/sid-current",
         "title": "Current session content",
         "content_text": "..."},
        {"path": "sessions/2026/05/01/old-session",
         "title": "Older relevant content",
         "content_text": "..."},
    ]
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=fake_cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        mock_build.return_value.search.return_value = fake_hits
        out = _capture(_emit_relevant_context, "sid-current", "Tell me about something")
    payload = json.loads(out.strip())
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Older relevant content" in context
    assert "Current session content" not in context


def test_silent_when_no_hits(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    fake_cfg = {"user_id": "me", "catalog": "c", "schema": "s",
                "warehouse_id": "w", "profile": "p"}
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=fake_cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        mock_build.return_value.search.return_value = []
        out = _capture(_emit_relevant_context, "sid1", "Tell me about something")
    assert out == ""


def test_swallows_exceptions(monkeypatch):
    """If the wiki call fails, the hook must not crash the user's session."""
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    with patch("wikibricks_recorder.hooks.config.load_config",
               side_effect=RuntimeError("boom")):
        out = _capture(_emit_relevant_context, "sid1", "Tell me about something")
    assert out == ""
