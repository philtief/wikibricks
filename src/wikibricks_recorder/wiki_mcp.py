"""Unified stdio MCP server for the WikiBricks recorder (3 reads + 2 writes).

Single Mac-side surface that wraps `WikiClient` directly — one MCP
registration covering both reads and writes. Works for personal wiki
(one user_id, one schema) or team wiki (many user_ids sharing one
schema) — same code, different config.

Tools (mirror the deployed UC subset on reads):

- wiki_search      — HYBRID Vector Search
- wiki_read_full   — read with parent+chunks reassembly (calls fn_wiki_read_full)
- wiki_index       — list pages
- wiki_write_page  — create/update page
- wiki_promote_answer — promote Q&A to synthesis page

Register::

    claude mcp add wiki -- uvx --from . wikibricks-mcp

Auth uses the local Databricks profile. Workspace target + user_id resolved
at startup via `config.load_config()` — env var, ~/.wikibricks-recorder.toml,
or raise. No hardcoded defaults.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from wikibricks_recorder import config


def get_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "wiki_search",
            "description": (
                "Hybrid Vector Search over the personal wiki. Returns top-K pages "
                "matching the query (semantic + keyword). Use to find prior sessions "
                "or notes related to the current task."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query."},
                    "k": {
                        "type": "integer",
                        "description": "Top-K pages to return (1-20).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "wiki_read_full",
            "description": (
                "Read a wiki page by path, with parent+chunk content reassembled "
                "(uses the deployed fn_wiki_read_full UC function). Use after "
                "wiki_search to fetch full content of a hit."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Wiki path, e.g. sessions/2026/04/29/abc-123.",
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "wiki_index",
            "description": (
                "List wiki pages (path, title, page_type, version). Optional path "
                "prefix to narrow results, e.g. 'sessions/' or 'topics/'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": "Optional path prefix filter.",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "wiki_write_page",
            "description": (
                "Create or update a wiki page. Use when the conversation produces a "
                "reusable note worth saving."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Wiki path, e.g. topics/my-topic. Must contain a slash.",
                    },
                    "title": {"type": "string", "description": "Human-readable page title."},
                    "summary": {"type": "string", "description": "One-sentence summary."},
                    "body": {"type": "string", "description": "Full markdown body."},
                    "page_type": {
                        "type": "string",
                        "description": "entity | concept | synthesis | comparison.",
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
            "description": (
                "Promote a Q&A pair to a canonical synthesis page that cites source pages. "
                "Use when an answer was synthesized from existing wiki pages."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The user question."},
                    "answer": {"type": "string", "description": "The synthesized answer."},
                    "source_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Wiki paths the answer cites.",
                    },
                },
                "required": ["question", "answer"],
            },
        },
    ]


def dispatch_tool(name: str, arguments: dict[str, Any], tools: dict | None = None) -> Any:
    """Run the named tool with kwargs, returning its result.

    `tools` overrides the default WikiClient-bound factory — used by tests.
    """
    if tools is None:
        tools = _build_tools()
    if name not in tools:
        raise ValueError(f"Unknown tool: {name}")
    return tools[name](**arguments)


def _read_full(client, catalog: str, schema: str, path: str) -> dict | None:
    """Call deployed fn_wiki_read_full UC function — reassembles parent+chunks."""
    safe_path = path.replace("\\", "\\\\").replace("'", "\\'")
    resp = client._exec(
        f"SELECT {catalog}.{schema}.fn_wiki_read_full('{safe_path}') AS j"
    )
    rows = resp.result.data_array if resp.result else []
    if not rows or not rows[0][0]:
        return None
    return json.loads(rows[0][0])


def _build_tools() -> dict:
    """Construct the 5-tool dict bound to a fresh WikiClient + workspace client."""
    cfg = config.load_config()
    os.environ.setdefault("WIKIBRICKS_CATALOG", cfg["catalog"])
    os.environ.setdefault("WIKIBRICKS_SCHEMA", cfg["schema"])
    from databricks.sdk import WorkspaceClient

    from wikibricks import WikiClient, make_agent_tools

    ws = WorkspaceClient(profile=cfg["profile"])
    client = WikiClient(warehouse_id=cfg["warehouse_id"], workspace_client=ws)
    write_tools = make_agent_tools(
        warehouse_id=cfg["warehouse_id"], workspace_client=ws
    )

    return {
        "wiki_search": lambda query, k=5: client.search(query, mode="HYBRID", num_results=k),
        "wiki_read_full": lambda path: _read_full(client, cfg["catalog"], cfg["schema"], path),
        "wiki_index": lambda prefix=None: client.list_pages(path_prefix=prefix),
        "wiki_write_page": write_tools["wiki_write_page"],
        "wiki_promote_answer": write_tools["wiki_promote_answer"],
    }


def _json_default(o: Any) -> str:
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def format_tool_response(
    name: str,
    arguments: dict[str, Any],
    tools: dict | None = None,
) -> str:
    """Run a tool and return its result as a JSON string.

    Any exception (unknown tool, bad args, backend error) is wrapped as
    `{"error": "<message>"}` so the server returns a structured response
    instead of crashing the stdio loop.
    """
    try:
        result = dispatch_tool(name, arguments, tools=tools)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(result, default=_json_default)


async def _serve() -> None:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    server: Server = Server("wiki")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [Tool(**t) for t in get_tool_schemas()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        return [TextContent(type="text", text=format_tool_response(name, arguments))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Entry point for `wikibricks-mcp` console script."""
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
