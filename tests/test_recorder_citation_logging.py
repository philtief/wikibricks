"""Tests for citation logging — on_stop reads the transcript, parses
[wb:<path>] markers from the agent's last reply, and emits one cited
row per unique path so downstream curation can compute helpful_score.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr
from unittest.mock import MagicMock, patch

from wikibricks.client import WikiClient
from wikibricks_recorder.hooks import _log_citations


def _capture_stderr(fn, *a, **kw) -> str:
    buf = io.StringIO()
    with redirect_stderr(buf):
        fn(*a, **kw)
    return buf.getvalue()


def _write_transcript(tmp_path, last_assistant_text: str):
    f = tmp_path / "transcript.jsonl"
    with f.open("w") as fh:
        fh.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "q"}]},
        }) + "\n")
        fh.write(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": last_assistant_text}
            ]},
        }) + "\n")
    return str(f)


def test_logs_one_row_per_unique_cited_path(tmp_path):
    tp = _write_transcript(
        tmp_path,
        "I built on [wb:sessions/abc] and [wb:topics/solvd]. "
        "Also see [wb:sessions/abc] again."
    )
    client = MagicMock(spec_set=WikiClient)
    _log_citations(session_id="sid", transcript_path=tp, client=client)
    assert client._log.call_count == 2
    for c in client._log.call_args_list:
        assert c.args[0] == "cited"
        assert "session_id" in (c.kwargs.get("details") or "")
    cited_paths = sorted(c.kwargs["path"] for c in client._log.call_args_list)
    assert cited_paths == ["sessions/abc", "topics/solvd"]


def test_emits_stderr_summary_when_citations_found(tmp_path):
    tp = _write_transcript(tmp_path, "see [wb:sessions/abc]")
    client = MagicMock(spec_set=WikiClient)
    err = _capture_stderr(_log_citations, session_id="sid", transcript_path=tp, client=client)
    assert "wikibricks: cited 1 page" in err
    assert "sessions/abc" in err


def test_silent_when_no_citations(tmp_path):
    tp = _write_transcript(tmp_path, "I had no useful prior context.")
    client = MagicMock(spec_set=WikiClient)
    err = _capture_stderr(_log_citations, session_id="sid", transcript_path=tp, client=client)
    client._log.assert_not_called()
    assert err == ""


def test_silent_when_transcript_path_missing(tmp_path):
    client = MagicMock(spec_set=WikiClient)
    err = _capture_stderr(_log_citations, session_id="sid",
                          transcript_path=str(tmp_path / "nope.jsonl"), client=client)
    client._log.assert_not_called()
    assert err == ""


def test_silent_when_transcript_path_empty():
    client = MagicMock(spec_set=WikiClient)
    err = _capture_stderr(_log_citations, session_id="sid", transcript_path="", client=client)
    client._log.assert_not_called()
    assert err == ""


def test_swallows_log_exceptions(tmp_path):
    """If client._log raises, _log_citations must not propagate."""
    tp = _write_transcript(tmp_path, "[wb:sessions/abc]")
    client = MagicMock(spec_set=WikiClient)
    client._log.side_effect = RuntimeError("boom")
    err = _capture_stderr(_log_citations, session_id="sid", transcript_path=tp, client=client)
    # No assertion needed beyond "does not raise"
    assert "boom" not in err  # error swallowed silently


def test_on_stop_invokes_log_citations(tmp_path):
    """End-to-end: on_stop, given a payload with transcript_path, calls _log_citations."""
    from wikibricks_recorder import hooks
    tp = _write_transcript(tmp_path, "[wb:sessions/abc]")
    payload = {"hook_event_name": "Stop", "session_id": "sid",
               "transcript_path": tp}
    state = {"session_id": "sid", "cwd": "/Users/me/proj",
             "events": [{"kind": "prompt", "prompt": "How do I do X?"}],
             "first_prompt": "How do I do X?",
             "started_at": "2026-05-13T00:00:00Z"}
    cfg = {"user_id": "me", "catalog": "c", "schema": "s",
           "warehouse_id": "w", "profile": "p"}
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        with patch("wikibricks_recorder.hooks.session.load", return_value=state), \
             patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
             patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
            hooks.on_stop()
            # One write_page (flush) plus one _log("cited") call
            assert mock_build.return_value.write_page.called
            assert mock_build.return_value._log.called
            cited_calls = [c for c in mock_build.return_value._log.call_args_list
                           if c.args and c.args[0] == "cited"]
            assert len(cited_calls) == 1
            assert cited_calls[0].kwargs["path"] == "sessions/abc"
    finally:
        sys.stdin = sys.__stdin__
