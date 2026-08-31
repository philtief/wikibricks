from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin"
HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_plugin_identity_and_version_match_package():
    manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    marketplace = _json(REPO / ".claude-plugin" / "marketplace.json")
    with (REPO / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)

    assert manifest["name"] == "wikibricks-recorder"
    assert manifest["version"] == project["project"]["version"]
    assert any(
        item["name"] == manifest["name"] and item["source"] == "./plugin"
        for item in marketplace["plugins"]
    )


@pytest.mark.parametrize("event", HOOK_EVENTS)
def test_plugin_hook_contract(event: str):
    entry = _json(PLUGIN / "hooks" / "hooks.json")["hooks"][event][0]["hooks"][0]

    assert entry["type"] == "command"
    assert entry["command"] == (
        "${CLAUDE_PLUGIN_ROOT}/bin/launch.sh wikibricks-recorder-hook"
    )
    assert 1 <= entry["timeout"] <= 60


def test_plugin_mcp_contract():
    wiki = _json(PLUGIN / ".mcp.json")["mcpServers"]["wiki"]

    assert wiki == {
        "command": "${CLAUDE_PLUGIN_ROOT}/bin/launch.sh",
        "args": ["wikibricks-mcp"],
    }


def test_plugin_launcher_and_console_script_are_local_only():
    launcher = PLUGIN / "bin" / "launch.sh"
    with (REPO / "pyproject.toml").open("rb") as file:
        scripts = tomllib.load(file)["project"]["scripts"]

    assert os.access(launcher, os.X_OK)
    assert scripts["wikibricks-recorder-hook"] == (
        "wikibricks.adapters.claude_code_hook:main"
    )
    assert "wikibricks[recorder]" not in launcher.read_text()
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not on PATH")
    assert subprocess.run([bash, "-n", launcher], check=False).returncode == 0
