"""Packaged agent guidance and MCP tool contracts."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

_TOOL_NAMES = (
    "wiki_search",
    "wiki_read_full",
    "wiki_index",
    "wiki_write_page",
    "wiki_promote_answer",
)


def get_server_instructions() -> str:
    resource = files("wikibricks.resources").joinpath("agent-instructions.md")
    return resource.read_text(encoding="utf-8")


def get_tool_schemas() -> list[dict[str, Any]]:
    resource = files("wikibricks.resources").joinpath("mcp-tools.json")
    try:
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid mcp-tools.json resource: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError("mcp-tools.json resource must contain an array of objects")
    names = tuple(item.get("name") for item in value)
    if names != _TOOL_NAMES:
        raise RuntimeError(
            f"mcp-tools.json tool names do not match dispatch contract: {names}"
        )
    if any(not isinstance(item.get("inputSchema"), dict) for item in value):
        raise RuntimeError("mcp-tools.json tools must define inputSchema objects")
    return value


__all__ = ["get_server_instructions", "get_tool_schemas"]
