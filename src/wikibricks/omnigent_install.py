"""Install WikiBricks through Omnigent's public agent configuration."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

_MINIMUM_VERSION = (0, 11, 0)
_VERSION_PATTERN = re.compile(r"\b(\d+)\.(\d+)\.(\d+)(?:[^\s]*)?")
_HARNESS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def default_agent_path() -> Path:
    return Path.home() / ".wikibricks" / "omnigent" / "agent.yaml"


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


def _agent_text(harness: str) -> str:
    if _HARNESS_PATTERN.fullmatch(harness) is None:
        raise ValueError("Omnigent harness must contain lowercase letters, digits, or hyphens")
    resource = files("wikibricks.resources").joinpath("omnigent-agent.yml")
    text = resource.read_text(encoding="utf-8").replace(
        "  harness: codex\n",
        f"  harness: {harness}\n",
        1,
    )
    value = yaml.safe_load(text)
    if not isinstance(value, dict) or value.get("name") != "wikibricks":
        raise RuntimeError("invalid packaged Omnigent agent")
    return text


def install_omnigent(
    agent_path: Path | None = None,
    *,
    harness: str = "codex",
    command: str = "omnigent",
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, str | bool]:
    """Install the companion agent and select it with Omnigent's public CLI."""
    version = _version(command, run)
    target = (agent_path or default_agent_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_agent_text(harness), encoding="utf-8")

    result = run(
        [
            command,
            "config",
            "set",
            "--global",
            f"default_agent={target}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "configuration failed").strip()
        raise RuntimeError(f"Cannot configure Omnigent: {detail}")
    return {
        "agent_path": str(target),
        "omnigent_version": version,
        "default_harness": harness,
        "configured_as_default": True,
    }


__all__ = ["default_agent_path", "install_omnigent"]
