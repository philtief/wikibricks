"""Tests for `wiki-init` — the personal/team setup CLI.

Three branches: personal-create, team-create, team-join. All input is
injectable via a `reader` callable so no real stdin/stdout is touched.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from wikibricks_recorder import config as recorder_config
from wikibricks_recorder import init_cli


@pytest.fixture(autouse=True)
def _isolate_active_target(monkeypatch, tmp_path):
    """Redirect ACTIVE_TARGET_FILE to tmp so tests don't pollute ~/.wikibricks."""
    monkeypatch.setattr(
        recorder_config, "ACTIVE_TARGET_FILE", tmp_path / "active-target"
    )


def make_reader(answers: list[str]):
    """Pop answers off the front of a list each time `input()` would be called."""
    queue = list(answers)

    def reader(_prompt: str) -> str:
        if not queue:
            raise AssertionError(f"reader exhausted, last prompt: {_prompt!r}")
        return queue.pop(0)

    return reader


# ---------------------------------------------------------------------------
# helpers — _prompt, _prompt_choice, _email_local_part, _format_*
# ---------------------------------------------------------------------------


class TestPromptHelpers:
    def test_prompt_uses_default_when_empty(self):
        r = make_reader([""])
        assert init_cli._prompt(r, "Catalog", default="main") == "main"

    def test_prompt_returns_user_input(self):
        r = make_reader(["my_cat"])
        assert init_cli._prompt(r, "Catalog", default="main") == "my_cat"

    def test_prompt_choice_default_on_empty(self):
        r = make_reader([""])
        assert init_cli._prompt_choice(r, "?", ["p", "t"], "p") == "p"

    def test_prompt_choice_accepts_first_letter(self):
        r = make_reader(["T"])
        assert init_cli._prompt_choice(r, "?", ["p", "t"], "p") == "t"

    def test_email_local_part_sanitizes_to_uc(self):
        assert init_cli._email_local_part("alice@example.com") == "alice"
        assert init_cli._email_local_part("Bob.Smith@x.io") == "bob_smith"

    def test_email_local_part_falls_back_to_me_when_empty(self):
        assert init_cli._email_local_part("@x.io") == "me"


class TestFormatters:
    def test_wiki_section_has_named_header_and_keys(self):
        out = init_cli._format_wiki_section("personal", {
            "catalog": "c", "schema": "s", "warehouse_id": "wh",
            "profile": "p", "user_id": "u",
        })
        assert "[wikis.personal]" in out
        for needle in ('catalog = "c"', 'schema = "s"', 'warehouse_id = "wh"',
                       'profile = "p"', 'user_id = "u"'):
            assert needle in out

    def test_write_wiki_config_preserves_other_wikis(self, tmp_path):
        path = tmp_path / "rc.toml"
        init_cli._write_wiki_config(path, "personal", {
            "catalog": "c1", "schema": "s1", "warehouse_id": "wh1",
            "profile": "p1", "user_id": "u1",
        })
        init_cli._write_wiki_config(path, "team-x", {
            "catalog": "c2", "schema": "s2", "warehouse_id": "wh2",
            "profile": "p2", "user_id": "u2",
        })
        text = path.read_text()
        assert "[wikis.personal]" in text
        assert "[wikis.team-x]" in text
        assert 's1' in text and 's2' in text

    def test_write_wiki_config_overwrites_same_name(self, tmp_path):
        path = tmp_path / "rc.toml"
        init_cli._write_wiki_config(path, "personal", {
            "catalog": "c1", "schema": "old", "warehouse_id": "wh",
            "profile": "p", "user_id": "u",
        })
        init_cli._write_wiki_config(path, "personal", {
            "catalog": "c1", "schema": "new", "warehouse_id": "wh",
            "profile": "p", "user_id": "u",
        })
        text = path.read_text()
        assert 'schema = "new"' in text
        assert 'schema = "old"' not in text

    def test_write_wiki_config_drops_legacy_recorder(self, tmp_path):
        path = tmp_path / "rc.toml"
        path.write_text(
            '[recorder]\ncatalog = "old"\nschema = "old_s"\n'
            'warehouse_id = "old_w"\nprofile = "old_p"\nuser_id = "old_u"\n'
        )
        init_cli._write_wiki_config(path, "personal", {
            "catalog": "c", "schema": "s", "warehouse_id": "wh",
            "profile": "p", "user_id": "u",
        })
        text = path.read_text()
        assert "[recorder]" not in text
        assert "[wikis.personal]" in text

    def test_team_toml_has_required_keys(self):
        out = init_cli._format_team_toml({
            "name": "platform", "host": "https://x", "catalog": "c",
            "schema": "s", "warehouse_id": "wh",
        })
        assert "[team]" in out
        for needle in ('name = "platform"', 'host = "https://x"',
                       'catalog = "c"', 'schema = "s"', 'warehouse_id = "wh"'):
            assert needle in out

    def test_grants_sql_includes_three_statements(self):
        out = init_cli._grants_sql("c", "s", "wh", "alice@x.com")
        assert "GRANT USE CATALOG ON CATALOG c TO `alice@x.com`" in out
        assert "GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA c.s TO `alice@x.com`" in out
        assert "GRANT CAN_USE ON WAREHOUSE wh TO `alice@x.com`" in out


# ---------------------------------------------------------------------------
# personal flow
# ---------------------------------------------------------------------------


class TestPersonalFlow:
    def test_writes_recorder_toml(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "alice@example.com")
        config = tmp_path / "rc.toml"
        # answers: profile, catalog (default), schema (default), warehouse_id, user_id (default)
        r = make_reader(["myprof", "", "", "wh-123", ""])
        out = io.StringIO()
        rc = init_cli.run_personal(r, out, config_path=config)
        assert rc == 0
        text = config.read_text()
        assert "[wikis.personal]" in text
        assert 'profile = "myprof"' in text
        assert 'catalog = "main"' in text
        assert 'schema = "wikibricks_personal_alice"' in text
        assert 'warehouse_id = "wh-123"' in text
        assert 'user_id = "alice-at-example.com"' in text

    def test_invalid_schema_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "a@b.com")
        config = tmp_path / "rc.toml"
        # profile, catalog, schema (BAD), warehouse_id, user_id
        r = make_reader(["p", "main", "Bad-Schema!", "wh", "u"])
        out = io.StringIO()
        rc = init_cli.run_personal(r, out, config_path=config)
        assert rc == 2
        assert not config.exists()

    def test_personal_flow_sets_active_target(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "a@b.com")
        config = tmp_path / "rc.toml"
        r = make_reader(["myprof", "", "", "wh", ""])
        out = io.StringIO()
        rc = init_cli.run_personal(r, out, config_path=config)
        assert rc == 0
        assert "[wikis.personal]" in config.read_text()
        assert recorder_config.get_active_target() == "personal"

    def test_personal_flow_preserves_existing_team_wiki(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "a@b.com")
        config = tmp_path / "rc.toml"
        # An existing team wiki section should be preserved
        config.write_text(
            "[wikis.team-platform]\n"
            'catalog = "main"\nschema = "wikibricks_team_platform"\n'
            'warehouse_id = "wh-t"\nprofile = "tprof"\nuser_id = "u"\n'
        )
        r = make_reader(["myprof", "", "", "wh", ""])
        out = io.StringIO()
        rc = init_cli.run_personal(r, out, config_path=config)
        assert rc == 0
        text = config.read_text()
        assert "[wikis.personal]" in text
        assert "[wikis.team-platform]" in text  # preserved


# ---------------------------------------------------------------------------
# team-create flow
# ---------------------------------------------------------------------------


class TestTeamCreate:
    def test_writes_both_files_and_prints_grants(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "owner@acme.com")
        config = tmp_path / "rc.toml"
        team_config = tmp_path / "wikibricks-team.toml"
        # answers: team_name, profile, catalog (default), schema (default),
        # warehouse_id, host, user_id (default)
        r = make_reader([
            "platform", "myprof", "", "", "wh-xyz",
            "https://acme.cloud.databricks.com", "",
        ])
        out = io.StringIO()
        rc = init_cli.run_team_create(
            r, out, config_path=config, team_config_path=team_config
        )
        assert rc == 0
        assert config.exists()
        assert team_config.exists()

        recorder = config.read_text()
        assert 'schema = "wikibricks_team_platform"' in recorder
        assert 'profile = "myprof"' in recorder
        assert 'user_id = "owner-at-acme.com"' in recorder

        team = team_config.read_text()
        assert 'name = "platform"' in team
        assert 'host = "https://acme.cloud.databricks.com"' in team
        assert "profile" not in team  # no per-user secrets in the sharable file

        printed = out.getvalue()
        assert "GRANT USE CATALOG ON CATALOG main TO `<email>`" in printed
        assert "GRANT CAN_USE ON WAREHOUSE wh-xyz" in printed
        assert "wiki-init --join" in printed

    def test_invalid_team_name_aborts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "a@b.com")
        config = tmp_path / "rc.toml"
        team_config = tmp_path / "team.toml"
        r = make_reader(["Bad Team Name!"])
        out = io.StringIO()
        rc = init_cli.run_team_create(
            r, out, config_path=config, team_config_path=team_config
        )
        assert rc == 2
        assert not config.exists()
        assert not team_config.exists()


# ---------------------------------------------------------------------------
# team-join flow
# ---------------------------------------------------------------------------


class TestTeamJoin:
    def _write_team_toml(self, path: Path, **overrides) -> None:
        data = {
            "name": "platform",
            "host": "https://acme.cloud.databricks.com",
            "catalog": "main",
            "schema": "wikibricks_team_platform",
            "warehouse_id": "wh-xyz",
        }
        data.update(overrides)
        body = "[team]\n" + "".join(f'{k} = "{v}"\n' for k, v in data.items())
        path.write_text(body)

    def test_imports_team_config_and_writes_recorder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "bob@acme.com")
        team = tmp_path / "team.toml"
        self._write_team_toml(team)
        config = tmp_path / "rc.toml"
        # answers: profile, user_id (default)
        r = make_reader(["bobprof", ""])
        out = io.StringIO()
        rc = init_cli.run_team_join(
            r, out, team_config_path=team, config_path=config
        )
        assert rc == 0
        text = config.read_text()
        assert "[wikis.team-platform]" in text
        assert 'catalog = "main"' in text
        assert 'schema = "wikibricks_team_platform"' in text
        assert 'warehouse_id = "wh-xyz"' in text
        assert 'profile = "bobprof"' in text
        assert 'user_id = "bob-at-acme.com"' in text

    def test_missing_team_file_returns_error(self, tmp_path):
        config = tmp_path / "rc.toml"
        r = make_reader([])
        out = io.StringIO()
        rc = init_cli.run_team_join(
            r, out, team_config_path=tmp_path / "nope.toml", config_path=config
        )
        assert rc == 2
        assert "team config not found" in out.getvalue()

    def test_team_file_missing_section_returns_error(self, tmp_path):
        team = tmp_path / "team.toml"
        team.write_text("[other]\nfoo = 'bar'\n")
        config = tmp_path / "rc.toml"
        r = make_reader([])
        out = io.StringIO()
        rc = init_cli.run_team_join(
            r, out, team_config_path=team, config_path=config
        )
        assert rc == 2
        assert "missing [team] section" in out.getvalue()

    def test_team_file_missing_required_key_returns_error(self, tmp_path):
        team = tmp_path / "team.toml"
        team.write_text(
            '[team]\nname = "x"\nhost = "h"\ncatalog = "c"\nschema = "s"\n'
        )  # missing warehouse_id
        config = tmp_path / "rc.toml"
        r = make_reader([])
        out = io.StringIO()
        rc = init_cli.run_team_join(
            r, out, team_config_path=team, config_path=config
        )
        assert rc == 2
        assert "warehouse_id" in out.getvalue()


# ---------------------------------------------------------------------------
# top-level dispatch via run()
# ---------------------------------------------------------------------------


class TestRunDispatch:
    def test_personal_branch_via_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "alice@x.com")
        config = tmp_path / "rc.toml"
        # mode (personal default), profile, catalog, schema, wh, user_id
        r = make_reader(["p", "p1", "", "", "w1", ""])
        out = io.StringIO()
        rc = init_cli.run([], reader=r, out=out, config_path=config, cwd=tmp_path)
        assert rc == 0
        assert config.exists()

    def test_team_create_branch_via_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "owner@x.com")
        config = tmp_path / "rc.toml"
        # mode=t, sub=c, team_name, profile, catalog, schema, wh, host, user_id
        r = make_reader([
            "t", "c", "platform", "p1", "", "", "w1", "https://x", "",
        ])
        out = io.StringIO()
        rc = init_cli.run([], reader=r, out=out, config_path=config, cwd=tmp_path)
        assert rc == 0
        assert (tmp_path / "wikibricks-team.toml").exists()

    def test_join_via_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(init_cli, "_git_email", lambda: "joiner@x.com")
        team = tmp_path / "team.toml"
        team.write_text(
            '[team]\nname = "p"\nhost = "h"\ncatalog = "c"\nschema = "s"\n'
            'warehouse_id = "w"\n'
        )
        config = tmp_path / "rc.toml"
        # join flow only prompts for profile + user_id
        r = make_reader(["jp", ""])
        out = io.StringIO()
        rc = init_cli.run(
            ["--join", str(team)],
            reader=r,
            out=out,
            config_path=config,
            cwd=tmp_path,
        )
        assert rc == 0
        text = config.read_text()
        assert 'schema = "s"' in text
        assert 'profile = "jp"' in text


# ---------------------------------------------------------------------------
# git email helpers
# ---------------------------------------------------------------------------


class TestGitEmail:
    def test_sanitize_replaces_at(self):
        assert init_cli._sanitize_user_id("alice@example.com") == "alice-at-example.com"

    def test_git_email_returns_none_when_unset(self, monkeypatch):
        # Force the subprocess to "succeed" with empty stdout
        class _Result:
            stdout = ""

        def fake_run(*a, **k):
            return _Result()

        monkeypatch.setattr("subprocess.run", fake_run)
        assert init_cli._git_email() is None


# ---------------------------------------------------------------------------
# --install-hooks — merge into ~/.claude/settings.json
# ---------------------------------------------------------------------------


class TestInstallHooks:
    def test_creates_settings_when_missing(self, tmp_path):
        settings = tmp_path / "settings.json"
        out = io.StringIO()
        rc = init_cli.install_hooks(
            settings_path=settings,
            python_path="/clone/.venv/bin/python",
            out=out,
            now_iso="20260503T200000Z",
        )
        assert rc == 0
        data = json.loads(settings.read_text())
        for event in (
            "SessionStart",
            "UserPromptSubmit",
            "PostToolUse",
            "Stop",
            "SessionEnd",
        ):
            entries = data["hooks"][event]
            assert len(entries) == 1
            cmd = entries[0]["hooks"][0]["command"]
            assert cmd == "/clone/.venv/bin/python -m wikibricks_recorder.hooks"
        assert data["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] == 5
        assert data["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"] == 10
        assert data["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 30

    def test_merges_with_existing_hooks(self, tmp_path):
        settings = tmp_path / "settings.json"
        existing = {
            "model": "opus",
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "/other/hook.sh", "timeout": 5}
                        ],
                    }
                ]
            },
        }
        settings.write_text(json.dumps(existing))
        out = io.StringIO()
        rc = init_cli.install_hooks(
            settings_path=settings,
            python_path="/clone/.venv/bin/python",
            out=out,
            now_iso="20260503T200000Z",
        )
        assert rc == 0
        data = json.loads(settings.read_text())
        assert data["model"] == "opus"
        session_start = data["hooks"]["SessionStart"]
        assert len(session_start) == 2
        commands = [e["hooks"][0]["command"] for e in session_start]
        assert "/other/hook.sh" in commands
        assert any("wikibricks_recorder.hooks" in c for c in commands)

    def test_backs_up_existing_settings(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text('{"hooks": {}}')
        out = io.StringIO()
        rc = init_cli.install_hooks(
            settings_path=settings,
            python_path="/p/python",
            out=out,
            now_iso="20260503T200000Z",
        )
        assert rc == 0
        backups = list(tmp_path.glob("settings.json.bak-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == '{"hooks": {}}'

    def test_skips_when_already_installed(self, tmp_path):
        settings = tmp_path / "settings.json"
        out = io.StringIO()
        init_cli.install_hooks(
            settings_path=settings,
            python_path="/p/python",
            out=out,
            now_iso="20260503T200000Z",
        )
        first = json.loads(settings.read_text())
        out2 = io.StringIO()
        rc = init_cli.install_hooks(
            settings_path=settings,
            python_path="/p/python",
            out=out2,
            now_iso="20260503T210000Z",
        )
        assert rc == 0
        second = json.loads(settings.read_text())
        for event in first["hooks"]:
            assert len(first["hooks"][event]) == len(second["hooks"][event])
        assert "skipped" in out2.getvalue().lower() or "already" in out2.getvalue().lower()

    def test_run_dispatches_install_hooks(self, tmp_path):
        settings = tmp_path / "settings.json"
        out = io.StringIO()
        rc = init_cli.run(
            ["--install-hooks", "--python", "/p/python", "--settings", str(settings)],
            reader=make_reader([]),
            out=out,
            config_path=tmp_path / "rc.toml",
            cwd=tmp_path,
        )
        assert rc == 0
        data = json.loads(settings.read_text())
        cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert cmd == "/p/python -m wikibricks_recorder.hooks"
