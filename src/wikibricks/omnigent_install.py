"""Install WikiBricks for Omnigent's native harnesses."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

_MINIMUM_VERSION = (0, 11, 0)
_VERSION_PATTERN = re.compile(r"\b(\d+)\.(\d+)\.(\d+)(?:[^\s]*)?")
_MCP_NAME = "wikibricks"


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
    result = run(command, capture_output=True, text=True, check=False)
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


def _load_kimi_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot update Kimi MCP config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Cannot update Kimi MCP config {path}: top level must be an object")
    servers = value.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        raise RuntimeError(f"Cannot update Kimi MCP config {path}: mcpServers must be an object")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _install_skill(home: Path) -> Path:
    text = files("wikibricks.resources").joinpath("wikibricks-memory-skill.md").read_text(
        encoding="utf-8"
    )
    shared = home / ".agents" / "skills" / "wikibricks-memory" / "SKILL.md"
    codex = home / ".codex" / "skills" / "wikibricks-memory" / "SKILL.md"
    claude = home / ".claude" / "skills" / "wikibricks-memory" / "SKILL.md"
    for target in (shared, codex, claude):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return shared


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


def _remove_legacy_agent(
    home: Path,
    *,
    command: str,
    run: Callable[..., Any],
) -> tuple[bool, bool]:
    config_path = home / ".omnigent" / "config.yaml"
    default_value: object = None
    if config_path.is_file():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"Cannot read Omnigent config {config_path}: {exc}") from exc
        if not isinstance(config, dict):
            raise RuntimeError(f"Cannot read Omnigent config {config_path}: top level must be a mapping")
        default_value = config.get("default_agent")

    standard = home / ".wikibricks" / "omnigent" / "agent.yaml"
    candidates = [standard]
    default_path: Path | None = None
    default_was_legacy = False
    if isinstance(default_value, str) and default_value:
        default_path = Path(default_value).expanduser()
        candidates.append(default_path)
        resolved_default = default_path.resolve()
        if resolved_default == standard.resolve():
            default_was_legacy = not resolved_default.exists() or _is_legacy_agent(
                resolved_default
            )
        else:
            default_was_legacy = _is_legacy_agent(resolved_default)

    removed = False
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if _is_legacy_agent(resolved):
            resolved.unlink()
            removed = True

    if default_was_legacy:
        _checked(
            run,
            [command, "config", "unset", "--global", "default_agent"],
            "remove the legacy WikiBricks default agent",
        )
    return removed, default_was_legacy


def install_omnigent(
    *,
    home: Path | None = None,
    command: str = "omnigent",
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Configure one shared memory skill and MCP server for native harnesses."""
    version = _version(command, run)
    resolved_home = (home or Path.home()).expanduser().resolve()
    mcp_command = which("wikibricks-mcp")
    if mcp_command is None:
        raise RuntimeError("wikibricks-mcp is not installed or is not on PATH")
    mcp_command = os.path.abspath(Path(mcp_command).expanduser())

    kimi_home = resolved_home / ".kimi"
    if home is None and os.environ.get("KIMI_SHARE_DIR"):
        kimi_home = Path(os.environ["KIMI_SHARE_DIR"]).expanduser().resolve()
    kimi_path = kimi_home / "mcp.json"
    kimi_config = _load_kimi_config(kimi_path)
    skill_path = _install_skill(resolved_home)

    statuses: dict[str, str] = {}
    codex = which("codex")
    if codex is None:
        statuses["codex"] = "not installed"
    else:
        _configure_cli_mcp(
            get_command=[codex, "mcp", "get", _MCP_NAME],
            remove_command=[codex, "mcp", "remove", _MCP_NAME],
            add_command=[codex, "mcp", "add", _MCP_NAME, "--", mcp_command],
            run=run,
            harness="Codex",
        )
        statuses["codex"] = "configured"

    claude = which("claude")
    if claude is None:
        statuses["claude"] = "not installed"
    else:
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

    servers = kimi_config.setdefault("mcpServers", {})
    servers[_MCP_NAME] = {"command": mcp_command}
    _write_json_atomic(kimi_path, kimi_config)
    statuses["kimi"] = "configured" if which("kimi") else "prepared (binary not installed)"

    legacy_removed, default_unset = _remove_legacy_agent(
        resolved_home,
        command=command,
        run=run,
    )
    return {
        "omnigent_version": version,
        "skill_path": str(skill_path),
        "mcp_command": mcp_command,
        "harnesses": statuses,
        "legacy_agent_removed": legacy_removed,
        "legacy_default_unset": default_unset,
    }


__all__ = ["install_omnigent"]
