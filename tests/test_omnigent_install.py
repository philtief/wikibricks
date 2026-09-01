from pathlib import Path
from subprocess import CompletedProcess

import pytest
import yaml

from wikibricks.omnigent_install import install_omnigent


def test_install_omnigent_writes_harness_selectable_agent_and_sets_public_default(
    tmp_path: Path,
):
    calls: list[list[str]] = []

    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, stdout="omnigent 0.11.0\n", stderr="")

    target = tmp_path / "agent.yaml"
    result = install_omnigent(target, run=run)

    agent = yaml.safe_load(target.read_text())
    assert agent["name"] == "wikibricks"
    assert agent["executor"] == {"harness": "codex"}
    assert agent["tools"]["wikibricks"] == {
        "type": "mcp",
        "command": "wikibricks-mcp",
    }
    assert "wiki_search" in agent["instructions"]
    assert "wiki_write_page" in agent["instructions"]
    assert calls == [
        ["omnigent", "--version"],
        [
            "omnigent",
            "config",
            "set",
            "--global",
            f"default_agent={target.resolve()}",
        ],
    ]
    assert result == {
        "agent_path": str(target.resolve()),
        "omnigent_version": "0.11.0",
        "default_harness": "codex",
        "configured_as_default": True,
    }


def test_install_omnigent_can_select_another_default_harness(tmp_path: Path):
    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, stdout="omnigent 0.11.0\n", stderr="")

    target = tmp_path / "agent.yaml"
    install_omnigent(target, harness="kimi", run=run)

    assert yaml.safe_load(target.read_text())["executor"] == {"harness": "kimi"}


def test_install_omnigent_rejects_versions_without_public_agent_mcp_contract(
    tmp_path: Path,
):
    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, stdout="omnigent 0.10.0\n", stderr="")

    with pytest.raises(RuntimeError, match="Omnigent 0.11.0 or newer"):
        install_omnigent(tmp_path / "agent.yaml", run=run)

    assert not (tmp_path / "agent.yaml").exists()
