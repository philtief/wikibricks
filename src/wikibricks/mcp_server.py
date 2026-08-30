"""Harness-neutral stdio MCP server backed by local PostgreSQL."""

from __future__ import annotations

import asyncio
import json
from typing import Any


def get_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "wiki_search",
            "description": "Search local memory and return the best matching pages and sessions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "k": {
                        "type": "integer",
                        "description": "Maximum results to return (1-20).",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "wiki_read_full",
            "description": "Read one local memory page or reconstructed session by path.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Memory path to read."},
                },
                "required": ["path"],
            },
        },
        {
            "name": "wiki_index",
            "description": "List local memory pages, optionally restricted to a path prefix.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prefix": {"type": "string", "description": "Optional path prefix."},
                },
                "required": [],
            },
        },
        {
            "name": "wiki_write_page",
            "description": "Create or update a durable local memory page.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Page path containing a slash."},
                    "title": {"type": "string", "description": "Human-readable title."},
                    "summary": {"type": "string", "description": "Concise summary."},
                    "body": {"type": "string", "description": "Full Markdown body."},
                    "page_type": {
                        "type": "string",
                        "description": "entity | concept | synthesis | comparison",
                        "default": "concept",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags.",
                    },
                },
                "required": ["path", "title", "summary", "body"],
            },
        },
        {
            "name": "wiki_promote_answer",
            "description": "Save a synthesized answer as a page linked to its source pages.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Question being answered."},
                    "answer": {"type": "string", "description": "Answer to preserve."},
                    "source_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Memory paths used as sources.",
                    },
                },
                "required": ["question", "answer"],
            },
        },
    ]


def _build_tools() -> dict[str, Any]:
    from wikibricks import WikiClient, make_agent_tools

    client = WikiClient()
    write_tools = make_agent_tools(database_url=client.database_url)
    return {
        "wiki_search": lambda query, k=5: client.search(query, num_results=k),
        "wiki_read_full": client.read_page,
        "wiki_index": lambda prefix=None: client.list_pages(path_prefix=prefix),
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

    server: Server = Server("wikibricks")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [Tool(**schema) for schema in get_tool_schemas()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        return [TextContent(type="text", text=format_tool_response(name, arguments))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
