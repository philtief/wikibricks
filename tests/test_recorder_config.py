"""Tests for wikibricks_recorder — Claude Code session → WikiBricks page bridge.

The recorder runs locally on the user's Mac, called by Claude Code hooks, and
synchronously writes session pages to WikiBricks (Topic 4 decision). All
WikiClient interactions are mocked here; SQL/network paths are exercised in
the smoke test.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from wikibricks_recorder import hooks, page_builder, session

# ---------------------------------------------------------------------------
# session state on disk
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_RECORDER_DIR", str(tmp_path))
    # session module reads the env var lazily through state_path()
    return tmp_path


class TestSession:
    def test_load_unknown_session_returns_empty_state(self, tmp_state_dir):
        state = session.load("sess-1")
        assert state["session_id"] == "sess-1"
        assert state["events"] == []
        assert state["first_prompt"] is None
        assert state["started_at"] is None
        assert state["cwd"] is None

    def test_save_then_load_roundtrip(self, tmp_state_dir):
        state = session.load("sess-2")
        state["first_prompt"] = "fix the bug"
        state["events"].append({"kind": "prompt", "ts": "t0", "prompt": "p"})
        session.save(state)
        loaded = session.load("sess-2")
        assert loaded["first_prompt"] == "fix the bug"
        assert loaded["events"][0]["prompt"] == "p"

    def test_append_event_persists(self, tmp_state_dir):
        session.append_event("sess-3", {"kind": "tool", "ts": "t1", "tool_name": "Bash"})
        state = session.load("sess-3")
        assert len(state["events"]) == 1
        assert state["events"][0]["tool_name"] == "Bash"

    def test_state_path_under_recorder_dir(self, tmp_state_dir):
        p = session.state_path("sess-4")
        assert str(tmp_state_dir) in str(p)
        assert p.name == "sess-4.json"


# ---------------------------------------------------------------------------
# page builder — pure transformation, no IO
# ---------------------------------------------------------------------------


class TestPageBuilder:
    def test_session_path_includes_user_and_date_hierarchy(self):
        path = page_builder.session_path(
            "alice-at-example.com", "abc-123", "2026-04-29T10:00:00+00:00"
        )
        assert path == "sessions/alice-at-example.com/2026/04/29/abc-123"

    def test_session_path_falls_back_to_now_when_started_at_missing(self):
        path = page_builder.session_path("user1", "abc-123", None)
        assert path.startswith("sessions/user1/")
        assert path.endswith("/abc-123")
        # sessions / user / Y / M / D / sid = 6 segments
        assert len(path.split("/")) == 6

    def test_session_title_from_first_prompt(self):
        state = {"session_id": "sess-1", "first_prompt": "fix the FEVM permissions bug"}
        assert page_builder.session_title(state) == "fix the FEVM permissions bug"

    def test_session_title_strips_to_one_line(self):
        state = {"session_id": "sess-1", "first_prompt": "do this\nthen that\nand more"}
        assert page_builder.session_title(state) == "do this"

    def test_session_title_truncates_to_120_chars(self):
        state = {"session_id": "sess-1", "first_prompt": "x" * 200}
        assert len(page_builder.session_title(state)) == 120

    def test_session_title_falls_back_to_session_id_when_no_prompt(self):
        state = {"session_id": "abcdef1234567890", "first_prompt": None}
        assert "abcdef12" in page_builder.session_title(state)

    def test_session_tags_includes_session(self):
        state = {"session_id": "s", "cwd": None, "model": None}
        assert "session" in page_builder.session_tags(state)

    def test_session_tags_with_cwd_and_model(self):
        state = {"session_id": "s", "cwd": "/Users/me/wikibricks", "model": "claude-opus-4-7"}
        tags = page_builder.session_tags(state)
        assert "cwd:wikibricks" in tags
        assert "model:claude-opus-4-7" in tags

    def test_session_content_returns_summary_and_body(self):
        state = {
            "session_id": "sess-1",
            "started_at": "2026-04-29T10:00:00+00:00",
            "cwd": "/tmp",
            "model": "claude-opus-4-7",
            "first_prompt": "fix it",
            "events": [
                {"kind": "prompt", "ts": "t0", "prompt": "fix it"},
                {"kind": "tool", "ts": "t1", "tool_name": "Edit"},
            ],
        }
        content = page_builder.session_content(state)
        assert "summary" in content
        assert "body" in content
        assert content["summary"].startswith("fix it")
        assert "fix it" in content["body"]
        assert "Edit" in content["body"]

    def test_session_content_summary_truncated(self):
        state = {"session_id": "s", "first_prompt": "x" * 500, "events": []}
        content = page_builder.session_content(state)
        assert len(content["summary"]) <= 200


# ---------------------------------------------------------------------------
# hook entry points — input parsing + state mutation, WikiClient mocked
# ---------------------------------------------------------------------------


def _stub_stdin(monkeypatch, payload: dict):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


class TestHooks:
    def test_session_start_initializes_state(self, tmp_state_dir, monkeypatch):
        _stub_stdin(monkeypatch, {"session_id": "h-1", "hook_event_name": "SessionStart"})
        hooks.on_session_start()
        state = session.load("h-1")
        assert state["started_at"] is not None
        assert state["cwd"] is not None

    def test_session_start_is_idempotent(self, tmp_state_dir, monkeypatch):
        _stub_stdin(monkeypatch, {"session_id": "h-2", "hook_event_name": "SessionStart"})
        hooks.on_session_start()
        first_started = session.load("h-2")["started_at"]
        # second call should not overwrite started_at
        _stub_stdin(monkeypatch, {"session_id": "h-2", "hook_event_name": "SessionStart"})
        hooks.on_session_start()
        assert session.load("h-2")["started_at"] == first_started

    def test_user_prompt_submit_records_first_prompt(self, tmp_state_dir, monkeypatch):
        _stub_stdin(monkeypatch, {"session_id": "h-3", "prompt": "what's up"})
        hooks.on_user_prompt_submit()
        state = session.load("h-3")
        assert state["first_prompt"] == "what's up"
        assert any(e["kind"] == "prompt" for e in state["events"])

    def test_user_prompt_submit_does_not_overwrite_first_prompt(self, tmp_state_dir, monkeypatch):
        _stub_stdin(monkeypatch, {"session_id": "h-4", "prompt": "first"})
        hooks.on_user_prompt_submit()
        _stub_stdin(monkeypatch, {"session_id": "h-4", "prompt": "second"})
        hooks.on_user_prompt_submit()
        state = session.load("h-4")
        assert state["first_prompt"] == "first"
        assert len(state["events"]) == 2

    def test_post_tool_use_appends_event(self, tmp_state_dir, monkeypatch):
        _stub_stdin(monkeypatch, {"session_id": "h-5", "tool_name": "Edit"})
        hooks.on_post_tool_use()
        state = session.load("h-5")
        assert state["events"][0]["kind"] == "tool"
        assert state["events"][0]["tool_name"] == "Edit"

    def test_stop_writes_via_wiki_client(self, tmp_state_dir, monkeypatch):
        # arrange — accumulated state with events
        session.append_event("h-6", {"kind": "prompt", "ts": "t0", "prompt": "fix"})
        # mock WikiClient construction + config (no real workspace needed)
        fake_client = MagicMock()
        monkeypatch.setattr(hooks, "_build_wiki_client", lambda cfg: fake_client)
        monkeypatch.setattr(hooks.config, "load_config", lambda: _fake_cfg())
        _stub_stdin(monkeypatch, {"session_id": "h-6", "hook_event_name": "Stop"})
        hooks.on_stop()
        fake_client.write_page.assert_called_once()
        # path is positional in WikiClient; check args + kwargs include sid + user
        args, kwargs = fake_client.write_page.call_args
        assert "h-6" in args[0]
        assert "user1" in args[0]

    def test_stop_skips_when_no_events(self, tmp_state_dir, monkeypatch):
        # session exists but no events recorded — nothing to write
        session.save({"session_id": "h-7", "events": [], "started_at": None,
                      "cwd": None, "first_prompt": None, "model": None})
        fake_client = MagicMock()
        monkeypatch.setattr(hooks, "_build_wiki_client", lambda cfg: fake_client)
        monkeypatch.setattr(hooks.config, "load_config", lambda: _fake_cfg())
        _stub_stdin(monkeypatch, {"session_id": "h-7", "hook_event_name": "Stop"})
        hooks.on_stop()
        fake_client.write_page.assert_not_called()

    def test_stop_swallows_write_errors(self, tmp_state_dir, monkeypatch, capsys):
        """Hook must never crash Claude Code — write failure logs to stderr only."""
        session.append_event("h-8", {"kind": "prompt", "ts": "t0", "prompt": "x"})
        fake_client = MagicMock()
        fake_client.write_page.side_effect = RuntimeError("warehouse cold")
        monkeypatch.setattr(hooks, "_build_wiki_client", lambda cfg: fake_client)
        monkeypatch.setattr(hooks.config, "load_config", lambda: _fake_cfg())
        _stub_stdin(monkeypatch, {"session_id": "h-8", "hook_event_name": "Stop"})
        hooks.on_stop()  # should not raise
        captured = capsys.readouterr()
        assert "warehouse cold" in captured.err or "wikibricks_recorder" in captured.err

    def test_main_dispatches_by_event_name(self, tmp_state_dir, monkeypatch):
        _stub_stdin(monkeypatch, {"session_id": "h-9"})
        called = {}
        monkeypatch.setattr(hooks, "on_session_start", lambda: called.setdefault("ss", True))
        monkeypatch.setattr(hooks, "on_user_prompt_submit", lambda: called.setdefault("ups", True))
        monkeypatch.setattr(hooks, "on_post_tool_use", lambda: called.setdefault("ptu", True))
        monkeypatch.setattr(hooks, "on_stop", lambda: called.setdefault("stop", True))
        monkeypatch.setattr(hooks, "on_session_end", lambda: called.setdefault("se", True))
        hooks.dispatch("SessionStart")
        hooks.dispatch("UserPromptSubmit")
        hooks.dispatch("PostToolUse")
        hooks.dispatch("Stop")
        hooks.dispatch("SessionEnd")
        assert called == {"ss": True, "ups": True, "ptu": True, "stop": True, "se": True}

    def test_main_unknown_event_is_silent(self, tmp_state_dir, monkeypatch):
        # unknown events from future Claude Code versions must not crash
        hooks.dispatch("FutureEventName")  # no exception


def _fake_cfg() -> dict[str, str]:
    return {
        "catalog": "test_catalog",
        "schema": "test_schema",
        "warehouse_id": "wh-test",
        "profile": "test-profile",
        "user_id": "user1",
    }


class TestConfig:
    """OSS positioning: no hardcoded workspace defaults. Config resolved
    from env, ~/.wikibricks-recorder.toml, or — for user_id only —
    `git config user.email`. Anything missing must raise."""

    def _isolate(self, monkeypatch, tmp_path):
        """Point CONFIG_FILE at a non-existent path and clear all env vars."""
        for prefix in ("WIKIBRICKS_RECORDER_", "WIKIBRICKS_RECORDER_"):
            for key in ("CATALOG", "SCHEMA", "WAREHOUSE_ID", "PROFILE", "USER_ID"):
                monkeypatch.delenv(f"{prefix}{key}", raising=False)
        from wikibricks_recorder import config as cfg_module
        monkeypatch.setattr(cfg_module, "CONFIG_FILE", tmp_path / "missing.toml")
        return cfg_module

    def test_raises_when_nothing_configured(self, monkeypatch, tmp_path):
        cfg_module = self._isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(cfg_module, "_git_email", lambda: None)
        with pytest.raises(RuntimeError, match="missing config keys"):
            cfg_module.load_config()

    def test_env_vars_resolve_each_key(self, monkeypatch, tmp_path):
        cfg_module = self._isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("WIKIBRICKS_RECORDER_CATALOG", "c1")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_SCHEMA", "s1")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_WAREHOUSE_ID", "wh1")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_PROFILE", "p1")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_USER_ID", "u1")
        out = cfg_module.load_config()
        assert out == {"catalog": "c1", "schema": "s1", "warehouse_id": "wh1",
                       "profile": "p1", "user_id": "u1"}

    def test_legacy_wikibricks_recorder_env_still_works(self, monkeypatch, tmp_path):
        cfg_module = self._isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("WIKIBRICKS_RECORDER_CATALOG", "legacy_c")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_SCHEMA", "legacy_s")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_WAREHOUSE_ID", "legacy_wh")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_PROFILE", "legacy_p")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_USER_ID", "legacy_u")
        out = cfg_module.load_config()
        assert out["catalog"] == "legacy_c"
        assert out["user_id"] == "legacy_u"

    def test_toml_fallback_resolves_all_keys(self, monkeypatch, tmp_path):
        cfg_module = self._isolate(monkeypatch, tmp_path)
        toml_path = tmp_path / "rc.toml"
        toml_path.write_text(
            '[recorder]\n'
            'catalog = "tc"\n'
            'schema = "ts"\n'
            'warehouse_id = "twh"\n'
            'profile = "tp"\n'
            'user_id = "tu"\n'
        )
        monkeypatch.setattr(cfg_module, "CONFIG_FILE", toml_path)
        out = cfg_module.load_config()
        assert out["catalog"] == "tc"
        assert out["user_id"] == "tu"

    def test_user_id_falls_back_to_sanitized_git_email(self, monkeypatch, tmp_path):
        cfg_module = self._isolate(monkeypatch, tmp_path)
        # Other keys via env so only user_id needs the git fallback
        monkeypatch.setenv("WIKIBRICKS_RECORDER_CATALOG", "c")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_SCHEMA", "s")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_WAREHOUSE_ID", "wh")
        monkeypatch.setenv("WIKIBRICKS_RECORDER_PROFILE", "p")
        monkeypatch.setattr(cfg_module, "_git_email", lambda: "alice@example.com")
        out = cfg_module.load_config()
        assert out["user_id"] == "alice-at-example.com"

    def test_env_overrides_toml(self, monkeypatch, tmp_path):
        cfg_module = self._isolate(monkeypatch, tmp_path)
        toml_path = tmp_path / "rc.toml"
        toml_path.write_text(
            '[recorder]\n'
            'catalog = "from_toml"\n'
            'schema = "ts"\n'
            'warehouse_id = "twh"\n'
            'profile = "tp"\n'
            'user_id = "tu"\n'
        )
        monkeypatch.setattr(cfg_module, "CONFIG_FILE", toml_path)
        monkeypatch.setenv("WIKIBRICKS_RECORDER_CATALOG", "from_env")
        out = cfg_module.load_config()
        assert out["catalog"] == "from_env"


class TestMultiWikiConfig:
    """`[wikis.<name>]` sections + active-target file. Hooks pick which one."""

    def _isolate(self, monkeypatch, tmp_path):
        for prefix in ("WIKIBRICKS_RECORDER_", "WIKIBRICKS_RECORDER_"):
            for key in ("CATALOG", "SCHEMA", "WAREHOUSE_ID", "PROFILE", "USER_ID"):
                monkeypatch.delenv(f"{prefix}{key}", raising=False)
        monkeypatch.delenv("WIKIBRICKS_TARGET", raising=False)
        from wikibricks_recorder import config as cfg_module
        toml = tmp_path / "rc.toml"
        active = tmp_path / "active-target"
        monkeypatch.setattr(cfg_module, "CONFIG_FILE", toml)
        monkeypatch.setattr(cfg_module, "ACTIVE_TARGET_FILE", active)
        return cfg_module, toml, active

    def _two_wikis_toml(self, path):
        path.write_text(
            "[wikis.personal]\n"
            'catalog = "main"\n'
            'schema = "wikibricks_personal_alice"\n'
            'warehouse_id = "wh-p"\n'
            'profile = "personal_prof"\n'
            'user_id = "alice-at-x.com"\n\n'
            "[wikis.team-platform]\n"
            'catalog = "main"\n'
            'schema = "wikibricks_team_platform"\n'
            'warehouse_id = "wh-t"\n'
            'profile = "team_prof"\n'
            'user_id = "alice-at-x.com"\n'
        )

    def test_single_wiki_resolves_without_active_target(self, monkeypatch, tmp_path):
        cfg, toml, _ = self._isolate(monkeypatch, tmp_path)
        toml.write_text(
            "[wikis.solo]\n"
            'catalog = "c1"\nschema = "s1"\nwarehouse_id = "wh1"\n'
            'profile = "p1"\nuser_id = "u1"\n'
        )
        out = cfg.load_config()
        assert out["schema"] == "s1"

    def test_multiple_wikis_with_active_target_resolves(self, monkeypatch, tmp_path):
        cfg, toml, active = self._isolate(monkeypatch, tmp_path)
        self._two_wikis_toml(toml)
        active.write_text("team-platform\n")
        out = cfg.load_config()
        assert out["schema"] == "wikibricks_team_platform"
        assert out["profile"] == "team_prof"

    def test_multiple_wikis_without_active_target_raises_with_hint(
        self, monkeypatch, tmp_path
    ):
        cfg, toml, _ = self._isolate(monkeypatch, tmp_path)
        self._two_wikis_toml(toml)
        with pytest.raises(RuntimeError, match="multiple wikis configured"):
            cfg.load_config()

    def test_env_target_overrides_active_file(self, monkeypatch, tmp_path):
        cfg, toml, active = self._isolate(monkeypatch, tmp_path)
        self._two_wikis_toml(toml)
        active.write_text("personal\n")
        monkeypatch.setenv("WIKIBRICKS_TARGET", "team-platform")
        out = cfg.load_config()
        assert out["schema"] == "wikibricks_team_platform"

    def test_unknown_active_target_raises(self, monkeypatch, tmp_path):
        cfg, toml, active = self._isolate(monkeypatch, tmp_path)
        self._two_wikis_toml(toml)
        active.write_text("nope\n")
        with pytest.raises(RuntimeError, match="unknown wiki target"):
            cfg.load_config()

    def test_recorder_legacy_takes_precedence_over_wikis(self, monkeypatch, tmp_path):
        cfg, toml, _ = self._isolate(monkeypatch, tmp_path)
        toml.write_text(
            "[recorder]\n"
            'catalog = "legacy_c"\nschema = "legacy_s"\nwarehouse_id = "legacy_wh"\n'
            'profile = "legacy_p"\nuser_id = "legacy_u"\n\n'
            "[wikis.x]\n"
            'catalog = "c"\nschema = "s"\nwarehouse_id = "wh"\n'
            'profile = "p"\nuser_id = "u"\n'
        )
        out = cfg.load_config()
        assert out["catalog"] == "legacy_c"

    def test_list_wikis_returns_named_sections(self, monkeypatch, tmp_path):
        cfg, toml, _ = self._isolate(monkeypatch, tmp_path)
        self._two_wikis_toml(toml)
        wikis = cfg.list_wikis()
        assert set(wikis) == {"personal", "team-platform"}
        assert wikis["personal"]["schema"] == "wikibricks_personal_alice"

    def test_list_wikis_empty_when_no_toml(self, monkeypatch, tmp_path):
        cfg, _, _ = self._isolate(monkeypatch, tmp_path)
        assert cfg.list_wikis() == {}

    def test_get_active_target_returns_none_when_unset(self, monkeypatch, tmp_path):
        cfg, _, _ = self._isolate(monkeypatch, tmp_path)
        assert cfg.get_active_target() is None

    def test_set_active_target_writes_file(self, monkeypatch, tmp_path):
        cfg, _, active = self._isolate(monkeypatch, tmp_path)
        cfg.set_active_target("team-platform")
        assert active.read_text().strip() == "team-platform"
        assert cfg.get_active_target() == "team-platform"

    def test_clear_active_target_removes_file(self, monkeypatch, tmp_path):
        cfg, _, active = self._isolate(monkeypatch, tmp_path)
        cfg.set_active_target("x")
        cfg.clear_active_target()
        assert not active.exists()
        assert cfg.get_active_target() is None
