"""Exercise the installed WikiBricks wheel through its stdio MCP executable."""

from __future__ import annotations

import asyncio
import json
import os
import shutil

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "wiki_search",
    "wiki_read_full",
    "wiki_index",
    "wiki_write_page",
    "wiki_promote_answer",
}


def _json_result(result):
    assert not result.isError
    return json.loads(result.content[0].text)


async def _exercise() -> None:
    executable = shutil.which("wikibricks-mcp")
    assert executable is not None
    server = StdioServerParameters(
        command=executable,
        env=dict(os.environ),
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS

            written = _json_result(
                await session.call_tool(
                    "wiki_write_page",
                    {
                        "path": "topics/wheel-smoke",
                        "title": "Wheel smoke",
                        "summary": "Installed package",
                        "body": "installed wheel marker",
                    },
                )
            )
            assert written == {"path": "topics/wheel-smoke", "status": "ok"}

            searched = _json_result(
                await session.call_tool(
                    "wiki_search",
                    {"query": "installed wheel marker"},
                )
            )
            assert any(result["path"] == "topics/wheel-smoke" for result in searched)

            page = _json_result(
                await session.call_tool(
                    "wiki_read_full",
                    {"path": "topics/wheel-smoke"},
                )
            )
            assert page["content"]["body"] == "installed wheel marker"


if __name__ == "__main__":
    asyncio.run(_exercise())
    print("installed wheel MCP smoke passed")
