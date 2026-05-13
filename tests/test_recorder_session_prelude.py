"""Tests for the SessionStart 'where you left off' prelude (Idea A)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from wikibricks_recorder.hooks import _emit_cwd_prelude


def _capture_both(fn, *a, **kw) -> tuple[str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        fn(*a, **kw)
    return out.getvalue(), err.getvalue()


def test_no_emission_when_env_var_off(monkeypatch):
    monkeypatch.delenv("WIKIBRICKS_INJECT_CONTEXT", raising=False)
    out, err = _capture_both(_emit_cwd_prelude, "/Users/me/proj")
    assert out == ""
    assert err == ""


def test_no_emission_when_cwd_empty(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    out, err = _capture_both(_emit_cwd_prelude, "")
    assert out == ""
    assert err == ""


def test_no_emission_when_no_prior_sessions(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    cfg = {"user_id": "me", "catalog": "c", "schema": "s",
           "warehouse_id": "w", "profile": "p"}
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        mock_build.return_value.list_recent_by_cwd_tag.return_value = []
        out, err = _capture_both(_emit_cwd_prelude, "/Users/me/proj")
    assert out == ""
    assert err == ""


def test_emits_prelude_with_recent_sessions(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    cfg = {"user_id": "me", "catalog": "c", "schema": "s",
           "warehouse_id": "w", "profile": "p"}
    rows = [
        {"path": "sessions/2026/05/13/abc", "title": "Fixed bug X",
         "summary": "fixed", "updated_at": "2026-05-13T08:00:00Z"},
        {"path": "sessions/2026/05/12/def", "title": "Added feature Y",
         "summary": "added", "updated_at": "2026-05-12T15:00:00Z"},
    ]
    with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
         patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
        mock_build.return_value.list_recent_by_cwd_tag.return_value = rows
        out, err = _capture_both(_emit_cwd_prelude, "/Users/me/projects/my-proj")
    # cwd basename was passed to the helper
    mock_build.return_value.list_recent_by_cwd_tag.assert_called_once()
    call_args = mock_build.return_value.list_recent_by_cwd_tag.call_args
    assert call_args.args[0] == "my-proj"
    # stdout has the JSON additionalContext
    payload = json.loads(out.strip())
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "Previously in this directory" in ctx
    assert "Fixed bug X" in ctx
    assert "Added feature Y" in ctx
    assert "sessions/2026/05/13/abc" in ctx
    # stderr has the user-visible summary
    assert "wikibricks: prelude" in err
    assert "my-proj" in err


def test_swallows_exceptions(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_INJECT_CONTEXT", "1")
    with patch("wikibricks_recorder.hooks.config.load_config",
               side_effect=RuntimeError("boom")):
        out, err = _capture_both(_emit_cwd_prelude, "/Users/me/proj")
    assert out == ""
    assert err == ""
