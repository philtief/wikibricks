"""Tests for the proactive context-injection path in on_user_prompt_submit."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from wikibricks_recorder.hooks import _emit_relevant_context


def _capture(fn, *a, **kw) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


def _capture_both(fn, *a, **kw) -> tuple[str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        fn(*a, **kw)
    return out.getvalue(), err.getvalue()


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


def test_emits_stderr_summary_when_context_injected(monkeypatch):
    """User-visible: when hits are injected, emit a one-line summary to stderr."""
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    fake_cfg = {"user_id": "me", "catalog": "c", "schema": "s",
                "warehouse_id": "w", "profile": "p"}
    fake_hits = [
        {"path": "sessions/2026/05/08/abc",
         "title": "Solvd kickoff",
         "content_text": "..."},
        {"path": "sessions/2026/04/30/xyz",
         "title": "AZ CH workshop",
         "content_text": "..."},
    ]
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=fake_cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        mock_build.return_value.search.return_value = fake_hits
        out, err = _capture_both(_emit_relevant_context, "sid-current", "What about Solvd?")
    assert "wikibricks: injected 2 pages" in err
    assert "sessions/2026/05/08/abc" in err
    assert "sessions/2026/04/30/xyz" in err
    # stdout JSON for the model still emits as before
    assert json.loads(out.strip())["hookSpecificOutput"]["additionalContext"]


def test_no_stderr_when_no_hits(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    fake_cfg = {"user_id": "me", "catalog": "c", "schema": "s",
                "warehouse_id": "w", "profile": "p"}
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=fake_cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        mock_build.return_value.search.return_value = []
        _out, err = _capture_both(_emit_relevant_context, "sid1", "Tell me something")
    assert err == ""


def test_no_stderr_when_env_var_off(monkeypatch):
    monkeypatch.delenv("WIKIBRICKS_INJECT_CONTEXT", raising=False)
    _out, err = _capture_both(_emit_relevant_context, "sid1", "Tell me about Solvd")
    assert err == ""


def test_additional_context_includes_citation_directive(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    fake_cfg = {"user_id": "me", "catalog": "c", "schema": "s",
                "warehouse_id": "w", "profile": "p"}
    fake_hits = [{"path": "sessions/abc", "title": "T", "content_text": "..."}]
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=fake_cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        mock_build.return_value.search.return_value = fake_hits
        out = _capture(_emit_relevant_context, "sid", "Tell me about Solvd Lakebase")
    ctx = json.loads(out.strip())["hookSpecificOutput"]["additionalContext"]
    assert "[wb:<path>]" in ctx
    assert "trace the source" in ctx
