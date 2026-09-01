"""Harness-neutral stdio MCP server backed by local PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

from wikibricks.resources import get_server_instructions, get_tool_schemas

_LOGGER = logging.getLogger(__name__)


def _build_tools() -> dict[str, Any]:
    from wikibricks import WikiClient, make_agent_tools

    client = WikiClient()
    write_tools = make_agent_tools(database_url=client.database_url)
    return {
        "wiki_search": lambda query, k=5: client.search(query, num_results=k),
        "wiki_read_full": client.read_page,
        "wiki_index": lambda prefix=None: [
            page
            for page in client.list_pages(path_prefix=prefix)
            if page["page_type"] not in {"session", "archive"}
        ],
        "wiki_write_page": write_tools["wiki_write_page"],
        "wiki_promote_answer": write_tools["wiki_promote_answer"],
    }


def dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    tools: dict[str, Any] | None = None,
) -> Any:
    active_tools = tools or _build_tools()
    if name not in active_tools:
        raise ValueError(f"Unknown tool: {name}")
    return active_tools[name](**arguments)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def format_tool_response(
    name: str,
    arguments: dict[str, Any],
    tools: dict[str, Any] | None = None,
) -> str:
    try:
        result = dispatch_tool(name, arguments, tools)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(result, default=_json_default)


async def _serve() -> None:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    server: Server = Server("wikibricks", instructions=get_server_instructions())

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [Tool(**schema) for schema in get_tool_schemas()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        return [TextContent(type="text", text=format_tool_response(name, arguments))]

    from wikibricks.automation import run_background_loop
    from wikibricks.config import load_config
    from wikibricks.maintenance import initialize_database

    try:
        await asyncio.to_thread(initialize_database, load_config().database_url)
    except Exception as exc:
        _LOGGER.warning("WikiBricks local database initialization failed: %s", exc)

    automation = asyncio.create_task(run_background_loop())
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        automation.cancel()
        with suppress(asyncio.CancelledError):
            await automation


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
