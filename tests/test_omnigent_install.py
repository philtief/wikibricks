import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import yaml

from wikibricks.omnigent_install import install_omnigent


def _runner(calls: list[list[str]]):
    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        if command == ["omnigent", "--version"]:
            return CompletedProcess(command, 0, stdout="omnigent 0.11.0\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    return run


def test_install_omnigent_configures_native_harnesses_without_an_agent(tmp_path: Path):
    calls: list[list[str]] = []
    mcp_command = tmp_path / "bin" / "wikibricks-mcp"
    versioned_command = tmp_path / "tools" / "wikibricks-0.11" / "wikibricks-mcp"
    versioned_command.parent.mkdir(parents=True)
    versioned_command.write_text("")
    mcp_command.parent.mkdir(parents=True)
    mcp_command.symlink_to(versioned_command)
    legacy_agent = tmp_path / "custom" / "old-wikibricks-agent.yaml"
    legacy_agent.parent.mkdir(parents=True)
    legacy_agent.write_text(
        yaml.safe_dump(
            {
                "name": "wikibricks",
                "tools": {"wikibricks": {"type": "mcp", "command": "wikibricks-mcp"}},
            }
        )
    )
    omnigent_config = tmp_path / ".omnigent" / "config.yaml"
    omnigent_config.parent.mkdir(parents=True)
    omnigent_config.write_text(
        yaml.safe_dump({"default_agent": str(legacy_agent), "server": "https://example.test"})
    )
    kimi_config = tmp_path / ".kimi" / "mcp.json"
    kimi_config.parent.mkdir(parents=True)
    kimi_config.write_text(
        json.dumps({"futureSetting": True, "mcpServers": {"existing": {"command": "existing-mcp"}}})
    )

    binaries = {
        "wikibricks-mcp": str(mcp_command),
        "codex": "/tools/codex",
        "claude": "/tools/claude",
        "kimi": None,
    }
    result = install_omnigent(
        home=tmp_path,
        run=_runner(calls),
        which=binaries.get,
    )

    shared_skill = tmp_path / ".agents" / "skills" / "wikibricks-memory" / "SKILL.md"
    codex_skill = tmp_path / ".codex" / "skills" / "wikibricks-memory" / "SKILL.md"
    claude_skill = tmp_path / ".claude" / "skills" / "wikibricks-memory" / "SKILL.md"
    assert shared_skill.read_text() == codex_skill.read_text()
    assert shared_skill.read_text() == claude_skill.read_text()
    assert "wiki_search" in shared_skill.read_text()
    assert "wiki_write_page" in shared_skill.read_text()

    kimi = json.loads(kimi_config.read_text())
    assert kimi["futureSetting"] is True
    assert kimi["mcpServers"]["existing"] == {"command": "existing-mcp"}
    assert kimi["mcpServers"]["wikibricks"] == {"command": str(mcp_command)}

    assert not legacy_agent.exists()
    assert ["omnigent", "config", "unset", "--global", "default_agent"] in calls
    assert not any("default_agent=" in part for call in calls for part in call)
    assert ["/tools/codex", "mcp", "add", "wikibricks", "--", str(mcp_command)] in calls
    assert [
        "/tools/claude",
        "mcp",
        "add",
        "--scope",
        "user",
        "wikibricks",
        "--",
        str(mcp_command),
    ] in calls
    assert result == {
        "omnigent_version": "0.11.0",
        "skill_path": str(shared_skill),
        "mcp_command": str(mcp_command),
        "harnesses": {
            "codex": "configured",
            "claude": "configured",
            "kimi": "prepared (binary not installed)",
        },
        "legacy_agent_removed": True,
        "legacy_default_unset": True,
    }


def test_install_omnigent_skips_absent_cli_harnesses_and_prepares_kimi(tmp_path: Path):
    calls: list[list[str]] = []
    other_agent = tmp_path / "my-agent.yaml"
    other_agent.write_text(yaml.safe_dump({"name": "my-agent"}))
    config = tmp_path / ".omnigent" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({"default_agent": str(other_agent)}))
    result = install_omnigent(
        home=tmp_path,
        run=_runner(calls),
        which=lambda name: "/bin/wikibricks-mcp" if name == "wikibricks-mcp" else None,
    )

    assert result["harnesses"] == {
        "codex": "not installed",
        "claude": "not installed",
        "kimi": "prepared (binary not installed)",
    }
    assert json.loads((tmp_path / ".kimi" / "mcp.json").read_text()) == {
        "mcpServers": {"wikibricks": {"command": "/bin/wikibricks-mcp"}}
    }
    assert calls == [["omnigent", "--version"]]
    assert other_agent.exists()


def test_install_omnigent_respects_kimi_share_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    kimi_home = tmp_path / "kimi"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KIMI_SHARE_DIR", str(kimi_home))

    install_omnigent(
        run=_runner([]),
        which=lambda name: "/bin/wikibricks-mcp" if name == "wikibricks-mcp" else None,
    )

    assert (home / ".agents" / "skills" / "wikibricks-memory" / "SKILL.md").exists()
    assert json.loads((kimi_home / "mcp.json").read_text()) == {
        "mcpServers": {"wikibricks": {"command": "/bin/wikibricks-mcp"}}
    }


def test_install_omnigent_preserves_invalid_kimi_config(tmp_path: Path):
    config = tmp_path / ".kimi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not-json\n")

    with pytest.raises(RuntimeError, match="Cannot update Kimi MCP config"):
        install_omnigent(
            home=tmp_path,
            run=_runner([]),
            which=lambda name: "/bin/wikibricks-mcp" if name == "wikibricks-mcp" else None,
        )

    assert config.read_text() == "{not-json\n"


def test_install_omnigent_rejects_old_omnigent_before_writing_files(tmp_path: Path):
    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, stdout="omnigent 0.10.0\n", stderr="")

    with pytest.raises(RuntimeError, match="Omnigent 0.11.0 or newer"):
        install_omnigent(
            home=tmp_path,
            run=run,
            which=lambda name: "/bin/wikibricks-mcp" if name == "wikibricks-mcp" else None,
        )

    assert not (tmp_path / ".agents").exists()
    assert not (tmp_path / ".kimi").exists()
