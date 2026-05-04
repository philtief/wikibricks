"""Tests for the Claude Code plugin manifest at plugin/.

Plugin distribution is via Git URL (no PyPI). Drift between
pyproject.toml and plugin/.claude-plugin/plugin.json silently ships a
mismatched version, so these tests are the safety net.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "plugin"
PLUGIN_MANIFEST = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
HOOKS_MANIFEST = PLUGIN_DIR / "hooks" / "hooks.json"
MCP_MANIFEST = PLUGIN_DIR / ".mcp.json"
LAUNCHER = PLUGIN_DIR / "bin" / "launch.sh"
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
PYPROJECT = REPO / "pyproject.toml"


def _load_json(p: Path) -> dict:
    with p.open() as f:
        return json.load(f)


def _load_pyproject() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# plugin.json — manifest required + recommended fields
# ---------------------------------------------------------------------------


class TestPluginManifest:
    def test_manifest_exists(self):
        assert PLUGIN_MANIFEST.exists(), f"plugin manifest missing: {PLUGIN_MANIFEST}"

    def test_required_fields_present(self):
        m = _load_json(PLUGIN_MANIFEST)
        for field in ("name", "description", "version"):
            assert field in m, f"required field missing: {field}"
            assert m[field], f"required field empty: {field}"

    def test_recommended_fields_present(self):
        m = _load_json(PLUGIN_MANIFEST)
        for field in ("homepage", "repository", "license", "keywords", "author"):
            assert field in m, f"recommended field missing: {field}"

    def test_version_matches_pyproject(self):
        m = _load_json(PLUGIN_MANIFEST)
        py = _load_pyproject()
        assert m["version"] == py["project"]["version"], (
            "plugin.json version drifted from pyproject.toml — bump both atomically"
        )

    def test_name_matches_marketplace_entry(self):
        m = _load_json(PLUGIN_MANIFEST)
        market = _load_json(MARKETPLACE)
        names = [p["name"] for p in market["plugins"]]
        assert m["name"] in names, f"plugin name {m['name']} not in marketplace plugins {names}"


# ---------------------------------------------------------------------------
# hooks/hooks.json — all 5 events present, explicit timeouts, all route via launcher
# ---------------------------------------------------------------------------


REQUIRED_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)


class TestHooksManifest:
    def test_all_events_present(self):
        h = _load_json(HOOKS_MANIFEST)
        events = h.get("hooks", {})
        for event in REQUIRED_HOOK_EVENTS:
            assert event in events, f"hook event missing: {event}"
            assert events[event], f"hook event empty: {event}"

    def test_every_hook_has_explicit_timeout(self):
        h = _load_json(HOOKS_MANIFEST)
        for event, entries in h["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    assert "timeout" in hook, f"{event}: hook missing explicit timeout"
                    assert isinstance(hook["timeout"], int), f"{event}: timeout must be int"
                    assert 1 <= hook["timeout"] <= 600, f"{event}: timeout {hook['timeout']} out of range"

    def test_every_hook_routes_through_launcher(self):
        h = _load_json(HOOKS_MANIFEST)
        for event, entries in h["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    cmd = hook["command"]
                    assert "${CLAUDE_PLUGIN_ROOT}/bin/launch.sh" in cmd, (
                        f"{event}: hook does not route through launcher: {cmd}"
                    )
                    assert "wikibricks-recorder-hook" in cmd, (
                        f"{event}: hook does not call wikibricks-recorder-hook binary"
                    )


# ---------------------------------------------------------------------------
# .mcp.json — wiki server present, routes through launcher
# ---------------------------------------------------------------------------


class TestMcpManifest:
    def test_wiki_server_present(self):
        m = _load_json(MCP_MANIFEST)
        servers = m.get("mcpServers", {})
        assert "wiki" in servers, "wiki MCP server missing from .mcp.json"

    def test_wiki_server_routes_through_launcher(self):
        m = _load_json(MCP_MANIFEST)
        wiki = m["mcpServers"]["wiki"]
        assert "${CLAUDE_PLUGIN_ROOT}/bin/launch.sh" in wiki["command"], (
            "wiki MCP server does not route through launcher"
        )
        assert "wikibricks-mcp" in wiki.get("args", []), (
            "wiki MCP server does not call wikibricks-mcp binary"
        )


# ---------------------------------------------------------------------------
# bin/launch.sh — exists, executable, syntactically valid bash
# ---------------------------------------------------------------------------


class TestLauncher:
    def test_launcher_exists_and_is_executable(self):
        assert LAUNCHER.exists(), f"launcher missing: {LAUNCHER}"
        import os
        assert os.access(LAUNCHER, os.X_OK), f"launcher not executable: {LAUNCHER}"

    def test_launcher_is_valid_bash(self):
        import shutil
        import subprocess
        bash = shutil.which("bash")
        if bash is None:
            import pytest
            pytest.skip("bash not on PATH")
        result = subprocess.run([bash, "-n", str(LAUNCHER)], capture_output=True, text=True)
        assert result.returncode == 0, f"bash syntax error in launcher: {result.stderr}"


# ---------------------------------------------------------------------------
# marketplace.json — wikibricks-recorder plugin entry sourced from ./plugin
# ---------------------------------------------------------------------------


class TestMarketplace:
    def test_marketplace_exists(self):
        assert MARKETPLACE.exists(), f"marketplace manifest missing: {MARKETPLACE}"

    def test_required_fields_present(self):
        m = _load_json(MARKETPLACE)
        for field in ("name", "owner", "plugins"):
            assert field in m, f"marketplace required field missing: {field}"

    def test_recorder_plugin_entry_sourced_locally(self):
        m = _load_json(MARKETPLACE)
        entries = [p for p in m["plugins"] if p["name"] == "wikibricks-recorder"]
        assert entries, "wikibricks-recorder plugin missing from marketplace"
        entry = entries[0]
        assert entry["source"] == "./plugin", (
            f"recorder source must be './plugin', got {entry['source']}"
        )

    def test_marketplace_name_not_reserved(self):
        m = _load_json(MARKETPLACE)
        reserved = {
            "claude-code-marketplace", "claude-code-plugins", "claude-plugins-official",
            "anthropic-marketplace", "anthropic-plugins", "agent-skills",
            "knowledge-work-plugins", "life-sciences",
        }
        assert m["name"] not in reserved, f"marketplace name {m['name']} is reserved by Anthropic"


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))
