"""Tests for the [wb:<path>] citation parser used at Stop time."""

from __future__ import annotations

import json
from pathlib import Path

from wikibricks_recorder.citations import extract_cited_paths


def _write_transcript(tmp_path: Path, messages: list[dict]) -> Path:
    f = tmp_path / "transcript.jsonl"
    with f.open("w") as fh:
        for m in messages:
            fh.write(json.dumps(m) + "\n")
    return f


def _assistant(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _user(text: str) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def test_extracts_single_citation(tmp_path):
    t = _write_transcript(tmp_path, [
        _user("question"),
        _assistant("Per [wb:sessions/abc] you already shipped this."),
    ])
    assert extract_cited_paths(str(t)) == {"sessions/abc"}


def test_extracts_multiple_citations(tmp_path):
    t = _write_transcript(tmp_path, [
        _assistant("See [wb:sessions/abc] and [wb:topics/solvd]."),
    ])
    assert extract_cited_paths(str(t)) == {"sessions/abc", "topics/solvd"}


def test_deduplicates_repeated_citations(tmp_path):
    t = _write_transcript(tmp_path, [
        _assistant("[wb:sessions/abc] [wb:sessions/abc] [wb:sessions/abc]"),
    ])
    assert extract_cited_paths(str(t)) == {"sessions/abc"}


def test_only_parses_last_assistant_message(tmp_path):
    t = _write_transcript(tmp_path, [
        _assistant("Earlier I cited [wb:sessions/old]."),
        _user("ok"),
        _assistant("Now I cite [wb:sessions/new]."),
    ])
    assert extract_cited_paths(str(t)) == {"sessions/new"}


def test_handles_missing_file(tmp_path):
    assert extract_cited_paths(str(tmp_path / "does-not-exist.jsonl")) == set()


def test_handles_empty_path():
    assert extract_cited_paths("") == set()
    assert extract_cited_paths(None) == set()


def test_handles_no_assistant_message(tmp_path):
    t = _write_transcript(tmp_path, [_user("question"), _user("follow-up")])
    assert extract_cited_paths(str(t)) == set()


def test_ignores_malformed_markers(tmp_path):
    t = _write_transcript(tmp_path, [
        _assistant("[wb:] no path. [wb:foo missing close. bare wb:bar. "
                   "[wb:sessions/good] this one counts."),
    ])
    assert extract_cited_paths(str(t)) == {"sessions/good"}


def test_handles_malformed_json_lines(tmp_path):
    f = tmp_path / "transcript.jsonl"
    f.write_text(
        'not json\n'
        + json.dumps(_assistant("[wb:sessions/abc]")) + "\n"
        + 'also not json\n'
    )
    assert extract_cited_paths(str(f)) == {"sessions/abc"}


def test_handles_assistant_with_no_text_blocks(tmp_path):
    t = _write_transcript(tmp_path, [
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "tool_use", "id": "x"}]}},
    ])
    assert extract_cited_paths(str(t)) == set()


def test_handles_paths_with_slashes_and_dashes(tmp_path):
    t = _write_transcript(tmp_path, [
        _assistant("[wb:sessions/2026/05/13/abc-def-123/chunks/01]"),
    ])
    assert extract_cited_paths(str(t)) == {"sessions/2026/05/13/abc-def-123/chunks/01"}
