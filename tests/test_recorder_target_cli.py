"""Tests for `wiki-target` — switch between configured wikis."""

from __future__ import annotations

import io

from wikibricks_recorder import target_cli


def _two_wikis_toml(path):
    path.write_text(
        "[wikis.personal]\n"
        'catalog = "main"\nschema = "wikibricks_personal_alice"\n'
        'warehouse_id = "wh-p"\nprofile = "personal_prof"\n'
        'user_id = "alice-at-x.com"\n\n'
        "[wikis.team-platform]\n"
        'catalog = "main"\nschema = "wikibricks_team_platform"\n'
        'warehouse_id = "wh-t"\nprofile = "team_prof"\n'
        'user_id = "alice-at-x.com"\n'
    )


class TestTargetCli:
    def _isolate(self, monkeypatch, tmp_path):
        from wikibricks_recorder import config as cfg
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "rc.toml")
        monkeypatch.setattr(cfg, "ACTIVE_TARGET_FILE", tmp_path / "active-target")
        return cfg

    def test_list_no_wikis_configured_warns(self, monkeypatch, tmp_path):
        self._isolate(monkeypatch, tmp_path)
        out = io.StringIO()
        rc = target_cli.run([], out=out)
        assert rc == 0
        assert "no wikis configured" in out.getvalue().lower()

    def test_list_marks_active(self, monkeypatch, tmp_path):
        cfg = self._isolate(monkeypatch, tmp_path)
        _two_wikis_toml(cfg.CONFIG_FILE)
        cfg.set_active_target("personal")
        out = io.StringIO()
        rc = target_cli.run([], out=out)
        assert rc == 0
        text = out.getvalue()
        assert "personal" in text and "team-platform" in text
        # active marker on the right line
        for line in text.splitlines():
            if line.lstrip().startswith("*"):
                assert "personal" in line

    def test_set_writes_active_file(self, monkeypatch, tmp_path):
        cfg = self._isolate(monkeypatch, tmp_path)
        _two_wikis_toml(cfg.CONFIG_FILE)
        out = io.StringIO()
        rc = target_cli.run(["team-platform"], out=out)
        assert rc == 0
        assert cfg.get_active_target() == "team-platform"
        assert "team-platform" in out.getvalue()

    def test_set_unknown_returns_error(self, monkeypatch, tmp_path):
        cfg = self._isolate(monkeypatch, tmp_path)
        _two_wikis_toml(cfg.CONFIG_FILE)
        out = io.StringIO()
        rc = target_cli.run(["nope"], out=out)
        assert rc == 2
        assert cfg.get_active_target() is None
        assert "unknown" in out.getvalue().lower()

    def test_clear_removes_active(self, monkeypatch, tmp_path):
        cfg = self._isolate(monkeypatch, tmp_path)
        _two_wikis_toml(cfg.CONFIG_FILE)
        cfg.set_active_target("personal")
        out = io.StringIO()
        rc = target_cli.run(["--clear"], out=out)
        assert rc == 0
        assert cfg.get_active_target() is None
