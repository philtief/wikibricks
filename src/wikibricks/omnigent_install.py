"""Install one WikiBricks memory across local agent harnesses."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from wikibricks.install_io import (
    load_json_object,
    load_yaml_mapping,
    write_json_atomic,
    write_text_atomic,
    write_yaml_atomic,
)
from wikibricks.resources import get_tool_schemas

_MINIMUM_VERSION = (0, 11, 0)
_VERSION_PATTERN = re.compile(r"\b(\d+)\.(\d+)\.(\d+)(?:[^\s]*)?")
_MCP_NAME = "wikibricks"
_FILE_HARNESSES = {
    "goose": "goose",
    "hermes": "hermes",
    "kimi": "kimi",
    "kiro": "kiro-cli",
    "opencode": "opencode",
    "pi": "pi",
    "qwen": "qwen",
}


def _version(command: str, run: Callable[..., Any]) -> str:
    try:
        result = run(
            [command, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Omnigent is not installed or is not on PATH") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "version check failed").strip()
        raise RuntimeError(f"Cannot run Omnigent: {detail}")
    match = _VERSION_PATTERN.search(result.stdout)
    if match is None:
        raise RuntimeError(f"Cannot parse Omnigent version: {result.stdout.strip()}")
    parsed = tuple(int(part) for part in match.groups())
    if parsed < _MINIMUM_VERSION:
        raise RuntimeError("Omnigent 0.11.0 or newer is required")
    return match.group(0)


def _checked(run: Callable[..., Any], command: list[str], action: str) -> None:
    try:
        result = run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Cannot {action}: executable not found") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(f"Cannot {action}: {detail}")


def _configure_cli_mcp(
    *,
    add_command: list[str],
    remove_command: list[str],
    get_command: list[str],
    run: Callable[..., Any],
    harness: str,
) -> None:
    existing = run(get_command, capture_output=True, text=True, check=False)
    if existing.returncode == 0:
        _checked(run, remove_command, f"replace the {harness} WikiBricks MCP server")
    _checked(run, add_command, f"configure the {harness} WikiBricks MCP server")


def _installed_version() -> str:
    try:
        return package_version("wikibricks")
    except PackageNotFoundError:
        return "0.11.0"


def _selected(prepare_all: bool, executable: str | None) -> bool:
    return prepare_all or executable is not None


def _skill_paths(home: Path, *, codex: bool, claude: bool, pi: bool) -> list[Path]:
    targets = [home / ".agents" / "skills" / "wikibricks-memory" / "SKILL.md"]
    if codex:
        targets.append(home / ".codex" / "skills" / "wikibricks-memory" / "SKILL.md")
    if claude:
        targets.append(home / ".claude" / "skills" / "wikibricks-memory" / "SKILL.md")
    if pi:
        targets.append(home / ".pi" / "agent" / "skills" / "wikibricks-memory" / "SKILL.md")
    return targets


def _install_skills(paths: list[Path]) -> None:
    text = files("wikibricks.resources").joinpath("wikibricks-memory-skill.md").read_text(
        encoding="utf-8"
    )
    for path in paths:
        write_text_atomic(path, text)


def _is_legacy_agent(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(value, dict) or value.get("name") != "wikibricks":
        return False
    tools = value.get("tools")
    if not isinstance(tools, dict):
        return False
    tool = tools.get("wikibricks")
    if not isinstance(tool, dict) or tool.get("type") != "mcp":
        return False
    command = tool.get("command")
    return isinstance(command, str) and Path(command).name == "wikibricks-mcp"


def _legacy_agents(home: Path, config: dict[str, Any]) -> tuple[list[Path], bool]:
    standard = home / ".wikibricks" / "omnigent" / "agent.yaml"
    candidates = [standard]
    default = config.get("default_agent")
    default_path: Path | None = None
    if isinstance(default, str) and default:
        default_path = Path(default).expanduser().resolve()
        candidates.append(default_path)

    legacy: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in legacy and _is_legacy_agent(resolved):
            legacy.append(resolved)
    default_is_legacy = default_path is not None and default_path in legacy
    return legacy, default_is_legacy


def _omnigent_harness_config(
    config: dict[str, Any],
    *,
    wrapper_paths: Mapping[str, Path],
) -> dict[str, Any]:
    raw = config.get("harness")
    if isinstance(raw, str):
        harnesses: dict[str, Any] = {"default": raw}
    elif raw is None:
        harnesses = {}
    elif isinstance(raw, dict):
        harnesses = dict(raw)
    else:
        raise RuntimeError("Cannot update Omnigent config: harness must be a string or mapping")

    for harness, path in wrapper_paths.items():
        existing = harnesses.get(harness)
        if existing is None:
            entry: dict[str, Any] = {}
        elif isinstance(existing, dict):
            entry = dict(existing)
        else:
            raise RuntimeError(f"Cannot update Omnigent config: harness.{harness} must be a mapping")
        entry["command"] = str(path)
        harnesses[harness] = entry
    config["harness"] = harnesses
    return config


def _wrapper_source(kind: str) -> str:
    python = shlex.quote(sys.executable)
    return f"#!/bin/sh\nexec {python} -m wikibricks.harness_launchers {kind} \"$@\"\n"


def _pi_extension_source(config_path: Path) -> str:
    source = files("wikibricks.resources").joinpath("pi-mcp-extension.js").read_text(
        encoding="utf-8"
    )
    marker = "__WIKIBRICKS_PI_CONFIG_PATH__"
    if source.count(marker) != 1:
        raise RuntimeError("invalid Pi MCP extension resource")
    return source.replace(marker, json.dumps(str(config_path)))


def _config_root(home: Path, environ: Mapping[str, str], *, explicit_home: bool) -> Path:
    if not explicit_home and environ.get("XDG_CONFIG_HOME"):
        return Path(environ["XDG_CONFIG_HOME"]).expanduser().resolve()
    return home / ".config"


def install_integrations(
    *,
    home: Path | None = None,
    require_omnigent: bool = False,
    command: str = "omnigent",
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Install WikiBricks for detected clients and all Omnigent harnesses."""
    env = os.environ if environ is None else environ
    explicit_home = home is not None
    resolved_home = (home or Path.home()).expanduser().resolve()
    omnigent = which(command)
    prepare_all = require_omnigent or omnigent is not None
    omnigent_version = _version(command, run) if prepare_all else None
    mode = "omnigent" if prepare_all else "standalone"

    mcp_command = which("wikibricks-mcp")
    if mcp_command is None:
        raise RuntimeError("wikibricks-mcp is not installed or is not on PATH")
    mcp_command = os.path.abspath(Path(mcp_command).expanduser())

    executables = {
        "codex": which("codex"),
        "claude": which("claude"),
        **{name: which(binary) for name, binary in _FILE_HARNESSES.items()},
    }
    selected = {
        name: _selected(prepare_all, executables[name]) for name in _FILE_HARNESSES
    }

    config_root = _config_root(resolved_home, env, explicit_home=explicit_home)
    kimi_home = resolved_home / ".kimi"
    if not explicit_home and env.get("KIMI_SHARE_DIR"):
        kimi_home = Path(env["KIMI_SHARE_DIR"]).expanduser().resolve()
    paths = {
        "goose": config_root / "goose" / "config.yaml",
        "hermes": resolved_home / ".hermes" / "config.yaml",
        "kimi": kimi_home / "mcp.json",
        "kiro": resolved_home / ".kiro" / "settings" / "mcp.json",
        "opencode": config_root / "opencode" / "opencode.json",
        "qwen": resolved_home / ".qwen" / "settings.json",
        "claude": resolved_home / ".claude.json",
        "omnigent": resolved_home / ".omnigent" / "config.yaml",
        "manifest": resolved_home / ".wikibricks" / "omnigent-install.json",
        "pi_config": resolved_home / ".wikibricks" / "pi-mcp.json",
        "pi_extension": resolved_home
        / ".pi"
        / "agent"
        / "extensions"
        / "wikibricks-mcp.js",
    }

    configs: dict[str, dict[str, Any]] = {}
    for name in ("kimi", "kiro", "qwen", "opencode"):
        if selected.get(name):
            parent = "mcp" if name == "opencode" else "mcpServers"
            configs[name] = load_json_object(paths[name], parent=parent)
    for name, parent in (("goose", "extensions"), ("hermes", "mcp_servers")):
        if selected.get(name):
            configs[name] = load_yaml_mapping(paths[name], parent=parent)
    if prepare_all and executables["claude"] is None:
        configs["claude"] = load_json_object(paths["claude"], parent="mcpServers")

    pi_config: dict[str, Any] | None = None
    pi_extension: str | None = None
    if selected["pi"]:
        pi_config = {"mcpCommand": mcp_command, "tools": get_tool_schemas()}
        pi_extension = _pi_extension_source(paths["pi_config"])

    omnigent_config: dict[str, Any] = {}
    legacy_agents: list[Path] = []
    default_was_legacy = False
    wrapper_paths: dict[str, Path] = {}
    launchers: dict[str, str] = {}
    if prepare_all:
        omnigent_config = load_yaml_mapping(paths["omnigent"])
        legacy_agents, default_was_legacy = _legacy_agents(resolved_home, omnigent_config)
        for name in ("opencode", "hermes"):
            downstream = executables[name]
            if downstream:
                wrapper_paths[f"{name}-native"] = resolved_home / ".wikibricks" / "bin" / name
                launchers[name] = os.path.abspath(Path(downstream).expanduser())
        _omnigent_harness_config(omnigent_config, wrapper_paths=wrapper_paths)
        if default_was_legacy:
            omnigent_config.pop("default_agent", None)

    statuses: dict[str, str] = {}
    codex = executables["codex"]
    if codex is not None:
        _configure_cli_mcp(
            get_command=[codex, "mcp", "get", _MCP_NAME],
            remove_command=[codex, "mcp", "remove", _MCP_NAME],
            add_command=[codex, "mcp", "add", _MCP_NAME, "--", mcp_command],
            run=run,
            harness="Codex",
        )
        statuses["codex"] = "configured"
    elif prepare_all:
        statuses["codex"] = "not installed"

    claude = executables["claude"]
    if claude is not None:
        _configure_cli_mcp(
            get_command=[claude, "mcp", "get", _MCP_NAME],
            remove_command=[claude, "mcp", "remove", "--scope", "user", _MCP_NAME],
            add_command=[
                claude,
                "mcp",
                "add",
                "--scope",
                "user",
                _MCP_NAME,
                "--",
                mcp_command,
            ],
            run=run,
            harness="Claude",
        )
        statuses["claude"] = "configured"
    elif prepare_all:
        servers = configs["claude"].setdefault("mcpServers", {})
        servers[_MCP_NAME] = {"command": mcp_command}
        statuses["claude"] = "prepared (binary not installed)"

    if prepare_all:
        statuses["debby"] = "configured via Claude MCP"
        statuses["polly"] = "configured via Claude MCP"

    if selected["kimi"]:
        configs["kimi"].setdefault("mcpServers", {})[_MCP_NAME] = {"command": mcp_command}
    if selected["qwen"]:
        configs["qwen"].setdefault("mcpServers", {})[_MCP_NAME] = {"command": mcp_command}
    if selected["kiro"]:
        configs["kiro"].setdefault("mcpServers", {})[_MCP_NAME] = {"command": mcp_command}
    if selected["opencode"]:
        configs["opencode"].setdefault("mcp", {})[_MCP_NAME] = {
            "type": "local",
            "command": [mcp_command],
            "enabled": True,
        }
    if selected["goose"]:
        configs["goose"].setdefault("extensions", {})[_MCP_NAME] = {
            "name": _MCP_NAME,
            "type": "stdio",
            "enabled": True,
            "cmd": mcp_command,
            "args": [],
            "timeout": 300,
        }
    if selected["hermes"]:
        configs["hermes"].setdefault("mcp_servers", {})[_MCP_NAME] = {
            "command": mcp_command,
            "args": [],
        }

    for name in _FILE_HARNESSES:
        if selected[name]:
            statuses[name] = (
                "configured" if executables[name] is not None else "prepared (binary not installed)"
            )

    skill_paths = _skill_paths(
        resolved_home,
        codex=prepare_all or codex is not None,
        claude=prepare_all or claude is not None,
        pi=selected["pi"],
    )
    _install_skills(skill_paths)

    if pi_config is not None and pi_extension is not None:
        write_json_atomic(paths["pi_config"], pi_config)
        write_text_atomic(paths["pi_extension"], pi_extension)

    for name in ("kimi", "kiro", "qwen", "opencode"):
        if name in configs:
            write_json_atomic(paths[name], configs[name])
    if "claude" in configs:
        write_json_atomic(paths["claude"], configs["claude"])
    for name in ("goose", "hermes"):
        if name in configs:
            write_yaml_atomic(paths[name], configs[name])
    if prepare_all:
        write_yaml_atomic(paths["omnigent"], omnigent_config)
        for name in launchers:
            wrapper = resolved_home / ".wikibricks" / "bin" / name
            write_text_atomic(wrapper, _wrapper_source(name), executable=True)
        for path in legacy_agents:
            path.unlink()

    owned_files = [str(path) for path in skill_paths]
    if selected["pi"]:
        owned_files.extend((str(paths["pi_config"]), str(paths["pi_extension"])))
    owned_files.extend(str(resolved_home / ".wikibricks" / "bin" / name) for name in launchers)
    owned_files.append(str(paths["manifest"]))
    owned_settings: dict[str, list[str]] = {}
    for name in statuses:
        if name in {"debby", "polly"}:
            owned_settings[name] = ["Claude mcpServers.wikibricks"]
        elif name == "goose":
            owned_settings[name] = ["extensions.wikibricks"]
        elif name == "hermes":
            owned_settings[name] = ["mcp_servers.wikibricks"]
        elif name == "opencode":
            owned_settings[name] = ["mcp.wikibricks"]
        elif name in {"kimi", "kiro", "qwen", "claude"}:
            owned_settings[name] = ["mcpServers.wikibricks"]
        elif name == "codex":
            owned_settings[name] = ["mcp_servers.wikibricks"]
    if wrapper_paths:
        owned_settings["omnigent"] = [
            *(f"harness.{name}.command" for name in sorted(wrapper_paths)),
        ]

    manifest = {
        "schema_version": 1,
        "installer_version": _installed_version(),
        "mode": mode,
        "mcp_command": mcp_command,
        "harnesses": statuses,
        "owned_files": sorted(owned_files),
        "owned_settings": owned_settings,
        "launchers": launchers,
    }
    write_json_atomic(paths["manifest"], manifest)

    return {
        "mode": mode,
        "omnigent_version": omnigent_version,
        "skill_path": str(skill_paths[0]),
        "mcp_command": mcp_command,
        "harnesses": statuses,
        "legacy_agent_removed": bool(legacy_agents),
        "legacy_default_unset": default_was_legacy,
    }


def install_omnigent(
    *,
    home: Path | None = None,
    command: str = "omnigent",
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Compatibility entry point that requires Omnigent."""
    return install_integrations(
        home=home,
        require_omnigent=True,
        command=command,
        run=run,
        which=which,
    )


__all__ = ["install_integrations", "install_omnigent"]
