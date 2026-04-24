"""Custom agent tools for write operations.

UC functions cannot perform DML, so writes (create page, promote answer) are
exposed to agents as plain Python callables. Register them with your agent
framework of choice — Databricks Agent Framework, LangChain, LlamaIndex, or
any MCP server implementation that supports Python tools.

Usage::

    from wikibricks import make_agent_tools

    tools = make_agent_tools(warehouse_id="abc123")
    tools["wiki_promote_answer"](
        question="What is a Delta table?",
        answer="A Delta table is ...",
        source_paths=["topics/delta", "topics/acid"],
    )
"""

from wikibricks.client import WikiClient


def make_agent_tools(warehouse_id: str, workspace_client=None) -> dict:
    """Return a dict of write-capable agent tools bound to a WikiClient.

    Each value is a plain Python callable whose docstring describes the
    tool contract. Agent frameworks can wrap these into their native
    tool specs (MLflow ``tool_call``, LangChain ``@tool``, etc.).

    Args:
        warehouse_id: SQL Warehouse id to run MERGE / INSERT statements on.
        workspace_client: Optional pre-configured ``WorkspaceClient``.

    Returns:
        ``{"wiki_write_page": callable, "wiki_promote_answer": callable}``.
    """
    client = WikiClient(warehouse_id=warehouse_id, workspace_client=workspace_client)

    def wiki_write_page(
        path: str,
        title: str,
        summary: str,
        body: str,
        page_type: str = "concept",
        tags: list[str] | None = None,
        created_by: str = "agent",
    ) -> dict:
        """Create or update a wiki page (archives previous version to history).

        Args:
            path: Wiki page path. Must contain a slash, e.g. ``topics/my-topic``.
            title: Human-readable title.
            summary: One-sentence summary.
            body: Full page body (markdown).
            page_type: One of ``entity``, ``concept``, ``synthesis``, ``comparison``.
            tags: Optional list of tag strings.
            created_by: Attribution recorded on the page.

        Returns:
            ``{"path": <path>, "status": "ok"}``.
        """
        content = {"summary": summary, "body": body}
        client.write_page(
            path, title, content,
            page_type=page_type,
            created_by=created_by,
            tags=tags or [],
        )
        return {"path": path, "status": "ok"}

    def wiki_promote_answer(
        question: str,
        answer: str,
        source_paths: list[str] | None = None,
        created_by: str = "agent",
    ) -> dict:
        """Promote a chat answer to a canonical synthesis page with ``cites`` edges.

        Resolves each ``source_paths`` entry to a page id and links the new
        synthesis page back to it.

        Args:
            question: The user question.
            answer: The synthesized answer.
            source_paths: Wiki page paths the answer cites. Unknown paths are
                silently skipped.
            created_by: Attribution recorded on the promoted page.

        Returns:
            ``{"path": <promoted_path>, "cited": <n>}``.
        """
        source_pages = []
        for p in source_paths or []:
            page = client.read_page(p)
            if page and page.get("page_id"):
                source_pages.append(page)
        path = client.promote_answer(
            question, answer, source_pages, created_by=created_by,
        )
        return {"path": path, "cited": len(source_pages)}

    return {
        "wiki_write_page": wiki_write_page,
        "wiki_promote_answer": wiki_promote_answer,
    }
