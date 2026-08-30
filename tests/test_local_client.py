from __future__ import annotations

from wikibricks import WikiClient, make_agent_tools
from wikibricks.local_client import LocalWikiClient


def test_wiki_client_defaults_to_local_postgres(postgres_url: str):
    client = WikiClient(database_url=postgres_url)

    assert isinstance(client, LocalWikiClient)
    client.write_page(
        "topics/local-first",
        "Local first",
        {"summary": "offline", "body": "PostgreSQL is the source of truth"},
        tags=["architecture"],
    )
    assert client.read_page("topics/local-first")["content"]["body"].startswith("PostgreSQL")
    assert client.search("source of truth")[0]["path"] == "topics/local-first"
    assert client.list_pages(path_prefix="topics/")[0]["version"] == 1


def test_agent_tool_factory_is_harness_neutral_and_local(postgres_url: str):
    tools = make_agent_tools(database_url=postgres_url)

    result = tools["wiki_write_page"](
        path="topics/tool-write",
        title="Tool write",
        summary="summary",
        body="body",
    )

    assert result == {"path": "topics/tool-write", "status": "ok"}
    assert WikiClient(database_url=postgres_url).read_page("topics/tool-write") is not None


def test_promote_answer_writes_a_local_synthesis_with_citations(postgres_url: str):
    client = WikiClient(database_url=postgres_url)
    client.write_page("topics/source", "Source", {"summary": "s", "body": "source"})

    path = client.promote_answer(
        "How is memory stored?",
        "In local PostgreSQL.",
        [client.read_page("topics/source")],
    )

    assert path.startswith("synthesis/how-is-memory-stored-")
    assert client.read_page(path)["content"]["body"] == "In local PostgreSQL."
    assert client.graph_neighbors(path)[0]["path"] == "topics/source"
