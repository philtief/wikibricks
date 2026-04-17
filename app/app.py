"""WikiBricks Chat -- RAG chat over the wiki knowledge base."""

import streamlit as st
from databricks.sdk import WorkspaceClient
from openai import OpenAI

# --- Configuration ---
VS_INDEX = "agent_marketplace_catalog.wiki.pages_index"
LLM_MODEL = "databricks-claude-sonnet-4-5"
SEARCH_COLUMNS = ["page_id", "path", "title", "page_type", "content_text", "tags", "version"]

SYSTEM_PROMPT = """You are a helpful assistant that answers questions using a wiki knowledge base.
You have been given relevant wiki pages as context. Use them to answer the user's question
accurately and concisely. Always cite which wiki page(s) you used by referencing the page path.

If the context does not contain enough information to answer, say so clearly.
Do not make up information that is not in the provided context."""


def get_clients():
    """Return WorkspaceClient and OpenAI client (cached in session state)."""
    if "ws" not in st.session_state:
        ws = WorkspaceClient()
        st.session_state.ws = ws
        st.session_state.openai = OpenAI(
            api_key=ws.config.token,
            base_url=f"{ws.config.host}/serving-endpoints",
        )
    return st.session_state.ws, st.session_state.openai


def search_wiki(ws, query, num_results=5):
    """Search the wiki Vector Search index. Returns list of page dicts."""
    try:
        resp = ws.vector_search_indexes.query_index(
            index_name=VS_INDEX,
            columns=SEARCH_COLUMNS,
            query_text=query,
            query_type="HYBRID",
            num_results=num_results,
        )
    except Exception as e:
        st.error(f"Search failed: {e}")
        return []

    if not resp.result or not resp.result.data_array:
        return []

    columns = [c.name for c in resp.manifest.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


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


# --- Streamlit UI ---
st.set_page_config(page_title="WikiBricks Chat", page_icon="📚", layout="centered")
st.title("WikiBricks Chat")
st.caption("Ask questions about the wiki knowledge base")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display existing messages
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander("Sources"):
                for s in msg["sources"]:
                    st.markdown(f"- **{s['title']}** (`{s['path']}`)")

# Chat input
if prompt := st.chat_input("Ask about the wiki..."):
    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Search and generate
    with st.chat_message("assistant"):
        ws, openai_client = get_clients()

        with st.spinner("Searching wiki..."):
            pages = search_wiki(ws, prompt)

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
