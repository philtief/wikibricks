from __future__ import annotations

from pathlib import Path

from wikibricks import make_agent_tools


def test_agent_tools_write_and_promote_local_pages(tmp_path: Path):
    tools = make_agent_tools(str(tmp_path / "memory.db"))

    assert set(tools) == {"wiki_write_page", "wiki_promote_answer"}
    written = tools["wiki_write_page"](
        path="topics/source",
        title="Source",
        summary="Local source",
        body="SQLite evidence",
        page_type="synthesis",
        tags=["local"],
    )
    promoted = tools["wiki_promote_answer"](
        question="Where is memory stored?",
        answer="In local SQLite.",
        source_paths=["topics/source", "topics/missing"],
    )

    assert written == {"path": "topics/source", "status": "ok"}
    assert promoted["path"].startswith("synthesis/")
    assert promoted["cited"] == 1


def test_agent_tools_have_harness_neutral_contracts(tmp_path: Path):
    tools = make_agent_tools(str(tmp_path / "memory.db"))

    assert "Create or update a wiki page" in tools["wiki_write_page"].__doc__
    assert "Promote a chat answer" in tools["wiki_promote_answer"].__doc__
