"""Launch Omnigent harnesses with WikiBricks visible in isolated configs."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

from wikibricks.install_io import (
    load_json_object,
    load_yaml_mapping,
    write_yaml_atomic,
)


def prepare_opencode_environment(
    environ: Mapping[str, str], mcp_command: str
) -> dict[str, str]:
    """Return an environment with WikiBricks merged into OpenCode's inline config."""
    prepared = dict(environ)
    raw = prepared.get("OPENCODE_CONFIG_CONTENT")
    if raw:
        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Cannot update OpenCode inline config: {exc}") from exc
        if not isinstance(config, dict):
            raise RuntimeError("Cannot update OpenCode inline config: top level must be an object")
    else:
        config = {}

    mcp = config.get("mcp")
    if mcp is None:
        mcp = {}
        config["mcp"] = mcp
    elif not isinstance(mcp, dict):
        raise RuntimeError("Cannot update OpenCode inline config: mcp must be an object")
    mcp["wikibricks"] = {
        "type": "local",
        "command": [mcp_command],
        "enabled": True,
    }
    prepared["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    return prepared


def prepare_hermes_home(
    environ: Mapping[str, str], mcp_command: str
) -> Path | None:
    """Merge WikiBricks into an isolated Hermes session home, when present."""
    raw_home = environ.get("HERMES_HOME")
    if not raw_home:
        return None
    config_path = Path(raw_home).expanduser().resolve() / "config.yaml"
    config = load_yaml_mapping(config_path, parent="mcp_servers")
    config.setdefault("mcp_servers", {})["wikibricks"] = {
        "command": mcp_command,
        "args": [],
    }
    write_yaml_atomic(config_path, config)
    return config_path


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> NoReturn:
    """Prepare one isolated harness environment and replace this process with it."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"opencode", "hermes"}:
        raise SystemExit("usage: python -m wikibricks.harness_launchers {opencode|hermes} [args ...]")
    kind = arguments[0]
    prepared = dict(os.environ if environ is None else environ)
    home = Path(prepared["HOME"]).expanduser().resolve() if prepared.get("HOME") else Path.home()
    manifest = load_json_object(home / ".wikibricks" / "omnigent-install.json")
    launchers = manifest.get("launchers")
    if not isinstance(launchers, dict) or not isinstance(launchers.get(kind), str):
        raise RuntimeError(f"WikiBricks has no downstream {kind} executable")
    downstream = launchers[kind]
    mcp_command = manifest.get("mcp_command")
    if not isinstance(mcp_command, str) or not mcp_command:
        raise RuntimeError("WikiBricks installer manifest has no MCP command")
    wrapper = home / ".wikibricks" / "bin" / kind
    if Path(downstream).expanduser().resolve() == wrapper.resolve():
        raise RuntimeError(f"WikiBricks {kind} launcher cannot invoke itself")

    if kind == "opencode":
        prepared = prepare_opencode_environment(prepared, mcp_command)
    else:
        prepare_hermes_home(prepared, mcp_command)
    os.execvpe(downstream, [downstream, *arguments[1:]], prepared)


if __name__ == "__main__":
    main()
