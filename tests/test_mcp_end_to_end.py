from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from wikibricks.mcp_server import get_server_instructions, get_tool_schemas
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.postgres_store import PostgresStore

EXPECTED_TOOLS = {
    "wiki_search",
    "wiki_read_full",
    "wiki_index",
    "wiki_write_page",
    "wiki_promote_answer",
}


def _result_json(result):
    assert not result.isError
    return json.loads(result.content[0].text)


def test_tool_contract_is_harness_and_backend_neutral():
    schemas = get_tool_schemas()

    assert {schema["name"] for schema in schemas} == EXPECTED_TOOLS
    descriptions = " ".join(schema["description"] for schema in schemas).lower()
    assert "claude" not in descriptions
    assert "databricks" not in descriptions
    assert "vector search" not in descriptions


def test_server_instructions_preserve_the_compounding_wiki_workflow():
    instructions = get_server_instructions().lower()

    for concept in (
        "raw sources",
        "wiki pages",
        "incrementally",
        "ingest",
        "query",
        "lint",
        "cross-reference",
    ):
        assert concept in instructions
    assert "recording a session does not" in instructions
    assert "claude" not in instructions
    assert "databricks" not in instructions


def test_all_five_tools_work_over_stdio_without_databricks_or_outbound_network(
    postgres_url: str,
    tmp_path: Path,
):
    store = PostgresStore(postgres_url)
    store.migrate()
    store.clear_all()
    store.ingest_session(
        SessionRecord(
            harness="test-harness",
            external_id="raw-evidence",
            user_id="u",
            events=[SessionEvent("0", "user", "raw evidence marker")],
        )
    )
    guard = tmp_path / "sitecustomize.py"
    guard.write_text(
        "import socket\n"
        "_connect = socket.socket.connect\n"
        "def _local_only(self, address):\n"
        "    if self.family != socket.AF_UNIX:\n"
        "        raise OSError('outbound network disabled by test')\n"
        "    return _connect(self, address)\n"
        "socket.socket.connect = _local_only\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DATABRICKS_")
    }
    environment["WIKIBRICKS_DATABASE_URL"] = postgres_url
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(Path(__file__).resolve().parents[1] / "src")]
    )

    async def exercise() -> None:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "wikibricks.mcp_server"],
            env=environment,
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert "persistent, compounding artifact" in initialized.instructions
                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS

                written = _result_json(
                    await session.call_tool(
                        "wiki_write_page",
                        {
                            "path": "topics/mcp-local",
                            "title": "MCP local",
                            "summary": "Offline memory",
                            "body": "Stored in local PostgreSQL",
                        },
                    )
                )
                assert written["status"] == "ok"

                searched = _result_json(
                    await session.call_tool("wiki_search", {"query": "offline memory", "k": 5})
                )
                assert searched[0]["path"] == "topics/mcp-local"

                evidence = _result_json(
                    await session.call_tool("wiki_search", {"query": "raw evidence marker"})
                )
                assert evidence[0]["page_type"] == "session"

                read = _result_json(
                    await session.call_tool("wiki_read_full", {"path": "topics/mcp-local"})
                )
                assert read["content"]["body"] == "Stored in local PostgreSQL"

                indexed = _result_json(await session.call_tool("wiki_index", {}))
                assert [page["path"] for page in indexed] == ["topics/mcp-local"]

                promoted = _result_json(
                    await session.call_tool(
                        "wiki_promote_answer",
                        {
                            "question": "Where is memory stored?",
                            "answer": "In PostgreSQL.",
                            "source_paths": ["topics/mcp-local"],
                        },
                    )
                )
                assert promoted["cited"] == 1

    asyncio.run(exercise())
