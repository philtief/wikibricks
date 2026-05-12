"""Tests for the title-repair parser used by scripts/repair_titles.py."""

from __future__ import annotations

import pytest

from wikibricks.title_repair import (
    extract_repaired_title,
    looks_like_system_prompt,
)


@pytest.mark.parametrize("text,expected", [
    ("You are a code reviewer.", True),
    ("Apply maximum compression. Rules:", True),
    ("\n\n  You are a memory consolidation agent.", True),
    ("How do I MERGE in Delta?", False),
    ("", False),
])
def test_looks_like_system_prompt(text, expected):
    assert looks_like_system_prompt(text) is expected


def test_extract_repaired_title_skips_system_prompt():
    body = (
        "# Session abc\n\n"
        "## Timeline\n"
        "### prompt @ 2026-05-12T10:00:00Z\n"
        "> You are a memory consolidation agent.\n\n"
        "### Read @ 2026-05-12T10:00:05Z\n\n"
        "### prompt @ 2026-05-12T10:00:10Z\n"
        "> Help me draft the Solvd architecture brief.\n\n"
    )
    assert extract_repaired_title(body) == "Help me draft the Solvd architecture brief."


def test_extract_repaired_title_returns_none_when_all_system():
    body = (
        "# Session abc\n\n"
        "### prompt @ 2026-05-12T10:00:00Z\n"
        "> You are a memory consolidation agent.\n\n"
        "### prompt @ 2026-05-12T10:00:10Z\n"
        "> Apply maximum compression. Rules:\n\n"
    )
    assert extract_repaired_title(body) is None


def test_extract_repaired_title_returns_none_when_no_prompts():
    assert extract_repaired_title("# Session abc\n\n## Timeline\n") is None
    assert extract_repaired_title("") is None


def test_extract_repaired_title_uses_first_line_of_multiline_prompt():
    body = (
        "### prompt @ 2026-05-12T10:00:00Z\n"
        "> First line of the prompt\n"
    )
    assert extract_repaired_title(body) == "First line of the prompt"


def test_extract_repaired_title_truncates_at_120_chars():
    long_prompt = "a" * 200
    body = f"### prompt @ 2026-05-12T10:00:00Z\n> {long_prompt}\n"
    assert len(extract_repaired_title(body)) == 120


def test_extract_repaired_title_picks_first_real_prompt():
    body = (
        "### prompt @ ts1\n"
        "> You are a code reviewer.\n\n"
        "### prompt @ ts2\n"
        "> First real question\n\n"
        "### prompt @ ts3\n"
        "> Second real question\n"
    )
    assert extract_repaired_title(body) == "First real question"
