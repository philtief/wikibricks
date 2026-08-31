from __future__ import annotations

from wikibricks import make_agent_tools


def test_agent_tools_write_and_promote_local_pages(postgres_url: str):
    tools = make_agent_tools(postgres_url)

    assert set(tools) == {"wiki_write_page", "wiki_promote_answer"}
    written = tools["wiki_write_page"](
        path="topics/source",
        title="Source",
        summary="Local source",
        body="PostgreSQL evidence",
        page_type="synthesis",
        tags=["local"],
    )
    promoted = tools["wiki_promote_answer"](
        question="Where is memory stored?",
        answer="In local PostgreSQL.",
        source_paths=["topics/source", "topics/missing"],
    )

    assert written == {"path": "topics/source", "status": "ok"}
    assert promoted["path"].startswith("synthesis/")
    assert promoted["cited"] == 1


def test_agent_tools_have_harness_neutral_contracts(postgres_url: str):
    tools = make_agent_tools(postgres_url)

    assert "Create or update a wiki page" in tools["wiki_write_page"].__doc__
    assert "Promote a chat answer" in tools["wiki_promote_answer"].__doc__
