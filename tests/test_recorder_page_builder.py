"""Tests for recorder page_builder — title heuristic and system-prompt detector."""

from __future__ import annotations

from wikibricks_recorder.page_builder import (
    _looks_like_system_prompt,
    session_title,
)


class TestLooksLikeSystemPrompt:
    def test_you_are_prefix_matches(self):
        assert _looks_like_system_prompt("You are a memory consolidation agent. Compress.")

    def test_apply_maximum_prefix_matches(self):
        assert _looks_like_system_prompt("Apply maximum non-destructive compression. Rules:")

    def test_apply_minimum_prefix_matches(self):
        assert _looks_like_system_prompt("Apply minimum non-destructive compression. Rules:")

    def test_user_question_does_not_match(self):
        assert not _looks_like_system_prompt("Help me fix the Asana posting script")

    def test_empty_string_does_not_match(self):
        assert not _looks_like_system_prompt("")

    def test_whitespace_only_does_not_match(self):
        assert not _looks_like_system_prompt("   \n  ")

    def test_leading_whitespace_is_stripped(self):
        assert _looks_like_system_prompt("\n\n  You are a code reviewer.")


class TestSessionTitle:
    def _state(self, events, first_prompt=None, session_id="abc12345-0000-0000-0000-000000000000"):
        return {
            "session_id": session_id,
            "events": events,
            "first_prompt": first_prompt,
        }

    def test_prefers_first_user_prompt_skipping_system_prompts(self):
        state = self._state(
            events=[
                {"kind": "prompt", "prompt": "You are a code reviewer."},
                {"kind": "tool", "tool_name": "Read"},
                {"kind": "prompt", "prompt": "How do I add row-level security?"},
            ],
            first_prompt="You are a code reviewer.",
        )
        assert session_title(state) == "How do I add row-level security?"

    def test_falls_back_to_first_prompt_when_all_are_system_prompts(self):
        state = self._state(
            events=[
                {"kind": "prompt", "prompt": "You are a memory consolidation agent."},
            ],
            first_prompt="You are a memory consolidation agent.",
        )
        assert session_title(state) == "You are a memory consolidation agent."

    def test_uses_first_line_only(self):
        state = self._state(
            events=[
                {"kind": "prompt", "prompt": "First line\nsecond line\nthird"},
            ],
            first_prompt="First line\nsecond line\nthird",
        )
        assert session_title(state) == "First line"

    def test_truncates_long_titles_to_120_chars(self):
        long_prompt = "a" * 200
        state = self._state(
            events=[{"kind": "prompt", "prompt": long_prompt}],
            first_prompt=long_prompt,
        )
        assert len(session_title(state)) == 120

    def test_no_prompts_falls_back_to_session_id(self):
        state = self._state(events=[], first_prompt=None)
        assert session_title(state) == "Session abc12345"

    def test_ignores_tool_events(self):
        state = self._state(
            events=[
                {"kind": "tool", "tool_name": "Read"},
                {"kind": "tool", "tool_name": "Bash"},
            ],
            first_prompt=None,
        )
        assert session_title(state) == "Session abc12345"
