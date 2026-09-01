import json
import os
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import yaml

from wikibricks import cli as cli_module
from wikibricks import omnigent_install as install_module
from wikibricks.config import load_config


def _runner(calls: list[list[str]], version: str = "0.11.0"):
    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        if command == ["omnigent", "--version"]:
            return CompletedProcess(command, 0, stdout=f"omnigent {version}\n", stderr="")
        if len(command) >= 3 and command[1:3] == ["mcp", "get"]:
            return CompletedProcess(command, 1, stdout="", stderr="not found")
        return CompletedProcess(command, 0, stdout="", stderr="")

    return run


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value))


def test_install_integrations_configures_every_omnigent_harness_idempotently(
    tmp_path: Path,
):
    calls: list[list[str]] = []
    mcp_command = tmp_path / "bin" / "wikibricks-mcp"
    mcp_command.parent.mkdir(parents=True)
    mcp_command.write_text("")

    legacy_agent = tmp_path / "old-agent.yaml"
    _write_yaml(
        legacy_agent,
        {
            "name": "wikibricks",
            "tools": {"wikibricks": {"type": "mcp", "command": "wikibricks-mcp"}},
        },
    )
    omnigent_config = tmp_path / ".omnigent" / "config.yaml"
    _write_yaml(
        omnigent_config,
        {
            "default_agent": str(legacy_agent),
            "harness": "codex-native",
            "server": "https://example.test",
        },
    )

    json_configs = {
        tmp_path / ".kimi" / "mcp.json": {
            "futureSetting": True,
            "mcpServers": {"existing": {"command": "existing-mcp"}},
        },
        tmp_path / ".qwen" / "settings.json": {
            "theme": "dark",
            "mcpServers": {"existing": {"command": "existing-mcp"}},
        },
        tmp_path / ".kiro" / "settings" / "mcp.json": {
            "futureSetting": True,
            "mcpServers": {"existing": {"command": "existing-mcp"}},
        },
        tmp_path / ".config" / "opencode" / "opencode.json": {
            "model": "provider/model",
            "mcp": {"existing": {"type": "remote", "url": "https://mcp.test"}},
        },
    }
    for path, value in json_configs.items():
        _write_json(path, value)

    goose_config = tmp_path / ".config" / "goose" / "config.yaml"
    _write_yaml(
        goose_config,
        {
            "GOOSE_PROVIDER": "openai",
            "extensions": {"existing": {"type": "builtin", "enabled": True}},
        },
    )
    hermes_config = tmp_path / ".hermes" / "config.yaml"
    _write_yaml(
        hermes_config,
        {
            "model": {"provider": "openrouter"},
            "mcp_servers": {"existing": {"command": "existing-mcp"}},
        },
    )

    binaries = {
        "wikibricks-mcp": str(mcp_command),
        "omnigent": "/tools/omnigent",
        "codex": "/tools/codex",
        "claude": "/tools/claude",
        "goose": "/tools/goose",
        "hermes": "/tools/hermes",
        "kimi": "/tools/kimi",
        "kiro-cli": "/tools/kiro-cli",
        "opencode": "/tools/opencode",
        "pi": "/tools/pi",
        "qwen": "/tools/qwen",
    }
    result = install_module.install_integrations(
        home=tmp_path,
        run=_runner(calls),
        which=binaries.get,
    )

    assert result["mode"] == "omnigent"
    assert result["omnigent_version"] == "0.11.0"
    assert set(result["harnesses"]) == {
        "claude",
        "codex",
        "debby",
        "goose",
        "hermes",
        "kimi",
        "kiro",
        "opencode",
        "pi",
        "polly",
        "qwen",
    }

    kimi = json.loads((tmp_path / ".kimi" / "mcp.json").read_text())
    qwen = json.loads((tmp_path / ".qwen" / "settings.json").read_text())
    kiro = json.loads((tmp_path / ".kiro" / "settings" / "mcp.json").read_text())
    opencode = json.loads(
        (tmp_path / ".config" / "opencode" / "opencode.json").read_text()
    )
    goose = yaml.safe_load(goose_config.read_text())
    hermes = yaml.safe_load(hermes_config.read_text())

    assert kimi["futureSetting"] is True
    assert kimi["mcpServers"]["existing"] == {"command": "existing-mcp"}
    assert kimi["mcpServers"]["wikibricks"] == {"command": str(mcp_command)}
    assert qwen["theme"] == "dark"
    assert qwen["mcpServers"]["wikibricks"] == {"command": str(mcp_command)}
    assert kiro["futureSetting"] is True
    assert kiro["mcpServers"]["wikibricks"] == {"command": str(mcp_command)}
    assert opencode["model"] == "provider/model"
    assert opencode["mcp"]["existing"] == {
        "type": "remote",
        "url": "https://mcp.test",
    }
    assert opencode["mcp"]["wikibricks"] == {
        "type": "local",
        "command": [str(mcp_command)],
        "enabled": True,
    }
    assert goose["GOOSE_PROVIDER"] == "openai"
    assert goose["extensions"]["existing"] == {"type": "builtin", "enabled": True}
    assert goose["extensions"]["wikibricks"] == {
        "name": "wikibricks",
        "type": "stdio",
        "enabled": True,
        "cmd": str(mcp_command),
        "args": [],
        "timeout": 300,
    }
    assert hermes["model"] == {"provider": "openrouter"}
    assert hermes["mcp_servers"]["wikibricks"] == {
        "command": str(mcp_command),
        "args": [],
    }

    skill_texts = [
        (tmp_path / path / "skills" / "wikibricks-memory" / "SKILL.md").read_text()
        for path in (".agents", ".codex", ".claude", ".pi/agent")
    ]
    assert len(set(skill_texts)) == 1
    assert "wiki_search" in skill_texts[0]

    updated_omnigent = yaml.safe_load(omnigent_config.read_text())
    assert updated_omnigent["server"] == "https://example.test"
    assert updated_omnigent["harness"]["default"] == "codex-native"
    for name in ("opencode", "hermes"):
        wrapper = tmp_path / ".wikibricks" / "bin" / name
        assert updated_omnigent["harness"][f"{name}-native"]["command"] == str(wrapper)
        assert wrapper.is_file()
        assert os.access(wrapper, os.X_OK)
    assert "default_agent" not in updated_omnigent
    assert not legacy_agent.exists()

    manifest_path = tmp_path / ".wikibricks" / "omnigent-install.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["mode"] == "omnigent"
    assert manifest["mcp_command"] == str(mcp_command)
    assert manifest["launchers"] == {
        "hermes": "/tools/hermes",
        "opencode": "/tools/opencode",
    }
    assert "mcpServers.wikibricks" in manifest["owned_settings"]["kimi"]
    assert str(omnigent_config) not in manifest["owned_files"]

    tracked = [*json_configs, goose_config, hermes_config, omnigent_config, manifest_path]
    first_bytes = {path: path.read_bytes() for path in tracked}
    second = install_module.install_integrations(
        home=tmp_path,
        run=_runner(calls),
        which=binaries.get,
    )
    assert second == {
        **result,
        "legacy_agent_removed": False,
        "legacy_default_unset": False,
    }
    assert {path: path.read_bytes() for path in tracked} == first_bytes


def test_install_command_defaults_to_universal_mode_and_keeps_omnigent_alias(
    tmp_path: Path,
):
    parser = cli_module.build_parser(load_config(home=tmp_path, environ={}))

    universal = parser.parse_args(["install"])
    compatibility = parser.parse_args(["install", "omnigent"])

    assert universal.install_target is None
    assert universal.handler is cli_module._command_install
    assert compatibility.install_target == "omnigent"
    assert compatibility.handler is cli_module._command_install


def test_install_integrations_with_only_codex_does_not_create_other_client_configs(
    tmp_path: Path,
):
    calls: list[list[str]] = []
    binaries = {
        "wikibricks-mcp": "/tools/wikibricks-mcp",
        "codex": "/tools/codex",
    }

    result = install_module.install_integrations(
        home=tmp_path,
        run=_runner(calls),
        which=binaries.get,
    )

    assert result["mode"] == "standalone"
    assert result["omnigent_version"] is None
    assert result["harnesses"] == {"codex": "configured"}
    assert [
        "/tools/codex",
        "mcp",
        "add",
        "wikibricks",
        "--",
        "/tools/wikibricks-mcp",
    ] in calls
    assert (tmp_path / ".agents" / "skills" / "wikibricks-memory" / "SKILL.md").is_file()
    assert (tmp_path / ".codex" / "skills" / "wikibricks-memory" / "SKILL.md").is_file()
    for absent in (".claude", ".kimi", ".qwen", ".hermes", ".kiro", ".pi"):
        assert not (tmp_path / absent).exists()
    assert not (tmp_path / ".config" / "goose").exists()
    assert not (tmp_path / ".config" / "opencode").exists()


def test_install_integrations_respects_kimi_share_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    kimi_home = tmp_path / "kimi"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KIMI_SHARE_DIR", str(kimi_home))

    install_module.install_integrations(
        run=_runner([]),
        which=lambda name: {
            "wikibricks-mcp": "/bin/wikibricks-mcp",
            "kimi": "/bin/kimi",
        }.get(name),
    )

    assert (home / ".agents" / "skills" / "wikibricks-memory" / "SKILL.md").exists()
    assert json.loads((kimi_home / "mcp.json").read_text()) == {
        "mcpServers": {"wikibricks": {"command": "/bin/wikibricks-mcp"}}
    }


def test_install_integrations_preflights_every_config_before_mutation(tmp_path: Path):
    invalid = tmp_path / ".qwen" / "settings.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("{not-json\n")
    calls: list[list[str]] = []
    binaries = {
        "wikibricks-mcp": "/bin/wikibricks-mcp",
        "codex": "/bin/codex",
        "qwen": "/bin/qwen",
    }

    with pytest.raises(RuntimeError, match="Cannot update JSON config"):
        install_module.install_integrations(
            home=tmp_path,
            run=_runner(calls),
            which=binaries.get,
        )

    assert invalid.read_text() == "{not-json\n"
    assert calls == []
    assert not (tmp_path / ".agents").exists()
    assert not (tmp_path / ".codex").exists()


def test_install_omnigent_alias_rejects_old_version_before_writing(tmp_path: Path):
    with pytest.raises(RuntimeError, match="Omnigent 0.11.0 or newer"):
        install_module.install_omnigent(
            home=tmp_path,
            run=_runner([], version="0.10.0"),
            which=lambda name: "/bin/wikibricks-mcp" if name == "wikibricks-mcp" else None,
        )

    assert not (tmp_path / ".agents").exists()
    assert not (tmp_path / ".wikibricks").exists()
