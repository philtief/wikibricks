"""Tests for recorder hook orchestration — focuses on the utility-session filter.

Hook-level integration (SessionStart / UserPromptSubmit / Stop) is exercised
indirectly via _is_utility_session, which is the new decision point.
"""

from __future__ import annotations

from unittest.mock import patch

from wikibricks_recorder.hooks import _flush, _is_utility_session


class TestIsUtilitySession:
    def _state(self, *, cwd=None, events=None, first_prompt=None):
        return {
            "session_id": "abc12345-0000-0000-0000-000000000000",
            "cwd": cwd,
            "events": events or [],
            "first_prompt": first_prompt,
        }

    def test_tmp_cwd_private_var_folders_is_utility(self):
        state = self._state(cwd="/private/var/folders/1q/abc/T", events=[{"kind": "prompt"}])
        assert _is_utility_session(state)

    def test_tmp_cwd_var_folders_is_utility(self):
        state = self._state(cwd="/var/folders/abc/T", events=[{"kind": "prompt"}])
        assert _is_utility_session(state)

    def test_tmp_cwd_tmp_is_utility(self):
        state = self._state(cwd="/tmp/working", events=[{"kind": "prompt"}])
        assert _is_utility_session(state)

    def test_real_project_cwd_is_not_utility(self):
        state = self._state(
            cwd="/Users/philipp.tiefenbacher/code/wikibricks",
            events=[
                {"kind": "prompt", "prompt": "Fix the test"},
                {"kind": "tool"},
                {"kind": "prompt", "prompt": "now run pytest"},
            ],
            first_prompt="Fix the test",
        )
        assert not _is_utility_session(state)

    def test_single_system_prompt_is_utility(self):
        state = self._state(
            cwd="/Users/philipp.tiefenbacher/home",
            events=[{"kind": "prompt", "prompt": "You are a code reviewer."}],
            first_prompt="You are a code reviewer.",
        )
        assert _is_utility_session(state)

    def test_single_user_prompt_is_not_utility(self):
        state = self._state(
            cwd="/Users/philipp.tiefenbacher/home",
            events=[{"kind": "prompt", "prompt": "How do I MERGE in Delta?"}],
            first_prompt="How do I MERGE in Delta?",
        )
        assert not _is_utility_session(state)

    def test_multi_turn_with_system_prompt_first_is_not_utility(self):
        state = self._state(
            cwd="/Users/philipp.tiefenbacher/home",
            events=[
                {"kind": "prompt", "prompt": "You are a code reviewer."},
                {"kind": "tool"},
                {"kind": "prompt", "prompt": "review the new function"},
            ],
            first_prompt="You are a code reviewer.",
        )
        assert not _is_utility_session(state)

    def test_no_cwd_no_prompts_is_not_utility(self):
        state = self._state(cwd=None, events=[], first_prompt=None)
        assert not _is_utility_session(state)


class TestFlushSkipsUtilitySessions:
    """_flush must short-circuit before any wiki client work for utility sessions."""

    def test_flush_skips_utility_session_no_config_load(self):
        state = {
            "session_id": "abc",
            "cwd": "/private/var/folders/1q/abc/T",
            "events": [{"kind": "prompt", "prompt": "You are a memory consolidation agent."}],
            "first_prompt": "You are a memory consolidation agent.",
        }
        with patch("wikibricks_recorder.hooks.config.load_config") as mock_load:
            _flush(state)
            mock_load.assert_not_called()

    def test_flush_skips_empty_session(self):
        state = {"session_id": "abc", "events": [], "cwd": "/Users/me/proj"}
        with patch("wikibricks_recorder.hooks.config.load_config") as mock_load:
            _flush(state)
            mock_load.assert_not_called()

    def test_flush_invokes_client_for_real_session(self):
        state = {
            "session_id": "abc",
            "cwd": "/Users/philipp.tiefenbacher/code/wikibricks",
            "events": [
                {"kind": "prompt", "prompt": "How do I add a Lakeflow Job?", "ts": "2026-05-12T10:00:00Z"},
                {"kind": "tool", "tool_name": "Read", "ts": "2026-05-12T10:00:05Z"},
            ],
            "first_prompt": "How do I add a Lakeflow Job?",
            "started_at": "2026-05-12T10:00:00Z",
            "model": "claude-opus-4-7",
        }
        cfg = {
            "user_id": "philipp.tiefenbacher-at-databricks.com",
            "catalog": "agent_marketplace_catalog",
            "schema": "wikibricks_personal_philipp",
            "warehouse_id": "41754a8563a43a49",
            "profile": "fe-vm-agent-marketplace",
        }
        with patch("wikibricks_recorder.hooks.config.load_config", return_value=cfg), \
             patch("wikibricks_recorder.hooks._build_wiki_client") as mock_build:
            mock_client = mock_build.return_value
            _flush(state)
            mock_build.assert_called_once_with(cfg)
            mock_client.write_page.assert_called_once()
            call_kwargs = mock_client.write_page.call_args.kwargs
            assert call_kwargs["title"] == "How do I add a Lakeflow Job?"
