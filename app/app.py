"""WikiBricks -- browse, search, edit, and chat with your wiki knowledge base."""

import json

import streamlit as st
from databricks.sdk import WorkspaceClient
from openai import OpenAI

from wikibricks import WikiClient

# --- Configuration ---
WAREHOUSE_ID = "41754a8563a43a49"
VS_INDEX = "agent_marketplace_catalog.wiki.pages_index"
LLM_MODEL = "databricks-claude-sonnet-4-5"
SEARCH_COLUMNS = ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]
PAGE_TYPES = ["concept", "entity", "synthesis", "comparison"]

SYSTEM_PROMPT = """You are a helpful assistant that answers questions using a wiki knowledge base.
You have been given relevant wiki pages as context. Use them to answer the user's question
accurately and concisely. Always cite which wiki page(s) you used by referencing the page path.

If the context does not contain enough information to answer, say so clearly.
Do not make up information that is not in the provided context."""


def _get_token(ws):
    """Extract bearer token from WorkspaceClient auth (works with OAuth and PAT)."""
    headers = ws.config.authenticate()
    return headers.get("Authorization", "").removeprefix("Bearer ")


def get_clients():
    """Return WorkspaceClient, OpenAI client, and WikiClient (cached in session state)."""
    if "ws" not in st.session_state:
        ws = WorkspaceClient()
        st.session_state.ws = ws
        st.session_state.wiki = WikiClient(warehouse_id=WAREHOUSE_ID, workspace_client=ws)
    ws = st.session_state.ws
    # Refresh OpenAI client each rerun so OAuth token stays fresh
    openai_client = OpenAI(
        api_key=_get_token(ws),
        base_url=f"{ws.config.host}/serving-endpoints",
    )
    return ws, openai_client, st.session_state.wiki


def search_wiki(wiki, query, num_results=5):
    """Search the wiki via Vector Search. Returns list of page dicts."""
    try:
        return wiki.search(query, num_results=num_results)
    except Exception as e:
        st.error(f"Search failed: {e}")
        return []


def build_context(pages):
    """Format search results into context string for the LLM."""
    if not pages:
        return "No relevant wiki pages found."

    parts = []
    for i, page in enumerate(pages, 1):
        title = page.get("title", "Untitled")
        path = page.get("path", "unknown")
        content = page.get("content_text", "")
        page_type = page.get("page_type", "")
        parts.append(f"[{i}] {title} ({path}, {page_type})\n{content}")

    return "\n\n---\n\n".join(parts)


def generate_response(client, messages):
    """Stream a response from the Foundation Model API."""
    return client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        stream=True,
    )


def validate_write_form(path, title, summary, body):
    """Validate write form fields. Returns list of error messages."""
    errors = []
    if not path or not path.strip():
        errors.append("Path is required.")
    elif "/" not in path:
        errors.append("Path should use slash hierarchy (e.g. 'databricks/delta-lake').")
    if not title or not title.strip():
        errors.append("Title is required.")
    if not summary or not summary.strip():
        errors.append("Summary is required.")
    if not body or not body.strip():
        errors.append("Body is required.")
    return errors


# --- Streamlit UI ---
st.set_page_config(page_title="WikiBricks", page_icon="📚", layout="centered")

# Sidebar navigation
mode = st.sidebar.radio("Mode", ["Chat", "Browse", "Write", "Ingest"], index=0)
st.sidebar.markdown("---")
st.sidebar.caption("WikiBricks -- Databricks-native wiki for AI agents")

# Initialize shared state
if "messages" not in st.session_state:
    st.session_state.messages = []


# ── Chat Mode ─────────────────────────────────────────────────────────────────
if mode == "Chat":
    st.title("WikiBricks Chat")
    st.caption("Ask questions about the wiki knowledge base")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                with st.expander("Sources"):
                    for s in msg["sources"]:
                        st.markdown(f"- **{s['title']}** (`{s['path']}`)")

    if prompt := st.chat_input("Ask about the wiki..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            ws, openai_client, wiki = get_clients()

            with st.spinner("Searching wiki..."):
                pages = search_wiki(wiki, prompt)

            context = build_context(pages)
            llm_messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": f"## Wiki Context\n\n{context}"},
                *[{"role": m["role"], "content": m["content"]} for m in st.session_state.messages],
            ]

            stream = generate_response(openai_client, llm_messages)
            response = st.write_stream(
                chunk.choices[0].delta.content
                for chunk in stream
                if chunk.choices and chunk.choices[0].delta.content
            )

            sources = [{"title": p.get("title", ""), "path": p.get("path", "")} for p in pages]
            if sources:
                with st.expander("Sources"):
                    for s in sources:
                        st.markdown(f"- **{s['title']}** (`{s['path']}`)")

            st.session_state.messages.append({
                "role": "assistant",
                "content": response,
                "sources": sources,
            })

            # Auto-promote: score the answer and save to wiki if quality is high
            if pages and response:
                try:
                    score_resp = openai_client.chat.completions.create(
                        model=LLM_MODEL,
                        messages=[{
                            "role": "user",
                            "content": (
                                f"Rate this answer on a scale of 1-5 for quality, "
                                f"accuracy, and usefulness. Reply with ONLY a single "
                                f"digit (1-5).\n\nQuestion: {prompt}\n\nAnswer: {response}"
                            ),
                        }],
                    )
                    score_text = score_resp.choices[0].message.content.strip()
                    score = int(score_text[0]) if score_text and score_text[0].isdigit() else 0
                    if score >= 4:
                        promoted_path = wiki.promote_answer(prompt, response, pages)
                        st.toast(f"Answer saved to wiki: {promoted_path}", icon="📝")
                except Exception:
                    pass


# ── Browse Mode ───────────────────────────────────────────────────────────────
elif mode == "Browse":
    st.title("Browse Wiki")

    search_col, btn_col = st.columns([4, 1])
    with search_col:
        query = st.text_input("Search pages", placeholder="e.g. Delta Lake, Unity Catalog...")
    with btn_col:
        st.write("")  # vertical alignment spacer
        search_clicked = st.button("Search", use_container_width=True)

    # Read a specific page by path
    with st.expander("Read page by path"):
        read_path = st.text_input("Page path", placeholder="databricks/delta-lake", key="read_path")
        read_clicked = st.button("Read")

    if read_clicked and read_path:
        ws, _, wiki = get_clients()
        with st.spinner("Loading page..."):
            page = wiki.read_page(read_path.strip())
        if page:
            st.subheader(page.get("title", "Untitled"))
            st.caption(f"`{page.get('path', '')}` | {page.get('page_type', '')} | v{page.get('version', '?')}")
            st.markdown(page.get("content_text", ""))
            tags = page.get("tags", "")
            if tags and tags != "[]":
                st.markdown(f"**Tags:** {tags}")

            source_ids = page.get("source_ids", "")
            if source_ids and source_ids != "[]" and source_ids != "null":
                with st.expander("Source provenance"):
                    st.markdown(f"Linked source IDs: `{source_ids}`")

            # Show version history
            with st.spinner("Loading history..."):
                versions = wiki.history(read_path.strip())
            if versions:
                st.markdown("#### Version History")
                for v in versions:
                    st.markdown(
                        f"- **v{v.get('version', '?')}** by {v.get('created_by', '?')} "
                        f"at {v.get('created_at', '?')} -- {v.get('summary', '')}"
                    )
        else:
            st.warning(f"No page found at path: `{read_path}`")

    elif (search_clicked or query) and query:
        ws, _, wiki = get_clients()
        with st.spinner("Searching..."):
            results = search_wiki(wiki, query.strip())

        if results:
            st.markdown(f"**{len(results)} result(s)**")
            for page in results:
                title = page.get("title", "Untitled")
                path = page.get("path", "")
                page_type = page.get("page_type", "")
                version = page.get("version", "?")
                content_preview = (page.get("content_text", "") or "")[:200]
                with st.container(border=True):
                    st.markdown(f"**{title}** (`{path}`)")
                    st.caption(f"{page_type} | v{version}")
                    st.markdown(content_preview + ("..." if len(page.get("content_text", "") or "") > 200 else ""))
        else:
            st.info("No results found.")


# ── Write Mode ────────────────────────────────────────────────────────────────
elif mode == "Write":
    st.title("Write Wiki Page")
    st.caption("Create a new page or update an existing one")

    with st.form("write_form"):
        path = st.text_input("Page path", placeholder="databricks/new-topic")
        title = st.text_input("Title", placeholder="My Wiki Page Title")
        page_type = st.selectbox("Page type", PAGE_TYPES)
        tags_input = st.text_input("Tags (comma-separated)", placeholder="databricks, delta-lake, concept")
        summary = st.text_area("Summary", placeholder="Brief summary of the page content")
        body = st.text_area("Body", placeholder="Full page content (Markdown supported)", height=300)
        created_by = st.text_input("Author", value="user")
        submitted = st.form_submit_button("Save Page", use_container_width=True)

    if submitted:
        errors = validate_write_form(path, title, summary, body)
        if errors:
            for err in errors:
                st.error(err)
        else:
            ws, _, wiki = get_clients()
            tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else None
            content = {"summary": summary.strip(), "body": body.strip()}
            with st.spinner("Saving page..."):
                try:
                    result = wiki.write_page(
                        path=path.strip(),
                        title=title.strip(),
                        content_json=content,
                        page_type=page_type,
                        created_by=created_by.strip() or "user",
                        tags=tags,
                    )
                    st.success(result)
                except Exception as e:
                    st.error(f"Failed to save: {e}")

    # Quick-load existing page for editing
    with st.expander("Load existing page for editing"):
        load_path = st.text_input("Path to load", placeholder="databricks/delta-lake", key="load_path")
        if st.button("Load"):
            ws, _, wiki = get_clients()
            with st.spinner("Loading..."):
                page = wiki.read_page(load_path.strip())
            if page:
                st.info(
                    f"Found **{page.get('title', '')}** (v{page.get('version', '?')}). "
                    f"Copy the details into the form above to update it."
                )
                st.json(page)
            else:
                st.warning(f"No page at `{load_path}`")


# ── Ingest Mode ──────────────────────────────────────────────────────────────
elif mode == "Ingest":
    st.title("Ingest Source")
    st.caption("Add a source document to the wiki's bronze layer")

    with st.form("ingest_form"):
        uri = st.text_input("Source URI", placeholder="https://docs.databricks.com/...")
        src_title = st.text_input("Title", placeholder="Databricks Delta Lake Overview")
        source_type = st.selectbox("Source type", ["url", "document", "api", "manual"])
        content = st.text_area(
            "Content", placeholder="Paste or type the source content here", height=300,
        )
        create_page = st.checkbox("Also create a wiki page from this source", value=True)
        ingest_submitted = st.form_submit_button("Ingest", use_container_width=True)

    if ingest_submitted:
        if not uri or not uri.strip():
            st.error("URI is required.")
        elif not content or not content.strip():
            st.error("Content is required.")
        else:
            ws, _, wiki = get_clients()
            with st.spinner("Ingesting source..."):
                try:
                    result = wiki.ingest_source(
                        uri=uri.strip(),
                        title=src_title.strip() or None,
                        content_text=content.strip(),
                        source_type=source_type,
                    )
                    st.success(result)

                    if create_page and src_title.strip():
                        import re
                        slug = re.sub(r"[^a-z0-9]+", "-", src_title.lower()).strip("-")[:60]
                        page_path = f"topics/{slug}"
                        page_content = {
                            "summary": src_title.strip(),
                            "body": content.strip(),
                        }
                        page_result = wiki.write_page(
                            path=page_path,
                            title=src_title.strip(),
                            content_json=page_content,
                            page_type="entity",
                            created_by="ingest",
                            tags=["ingested", source_type],
                        )
                        st.success(f"{page_result} (from ingested source)")
                except Exception as e:
                    st.error(f"Ingestion failed: {e}")
