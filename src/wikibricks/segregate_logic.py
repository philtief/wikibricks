"""Pure helpers for the page-segregation flow.

When a page's body exceeds the size threshold, the segregate notebook
breaks it into a parent (summary + ToC) plus N chunk children, joined by
`parent_id` and ordered by `chunk_index`. The library core stays LLM-free
(AGENTS.md hard rule); the LLM-driven summary + chunk-titling step lives
in `notebooks/wiki_segregate.py`. These helpers are deterministic and
fully unit-testable.
"""

from __future__ import annotations

# Default chunk size in characters. Bumped from 8 000 (v0.7.3 and earlier)
# to 30 000 in v0.7.4 — see test_segregate_logic.py::TestDefaultMaxChars for
# the rationale. Notebooks may override via the `max_chars_per_chunk` widget.
DEFAULT_MAX_CHARS_PER_CHUNK = 30_000


def chunk_at_boundaries(body: str, *, max_chars: int) -> list[str]:
    """Split `body` into chunks of <= `max_chars` each.

    Greedy packing of paragraphs (separated by blank lines). A single
    paragraph that exceeds `max_chars` is preserved intact — the LLM step
    is responsible for further summarization.
    """
    if not body.strip():
        return []
    paragraphs = body.split("\n\n")
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if not current:
            current = p
            continue
        candidate = current + "\n\n" + p
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = p
    if current:
        chunks.append(current)
    return chunks


def child_path(parent_path: str, chunk_index: int) -> str:
    """`<parent_path>/chunks/NN` — zero-padded so lexicographic = numeric."""
    return f"{parent_path.rstrip('/')}/chunks/{chunk_index:02d}"


def child_title(parent_title: str, chunk_title: str, *, max_chars: int = 120) -> str:
    """`<parent> - <chunk>`, truncated to keep DB title column happy."""
    full = f"{parent_title} - {chunk_title}"
    return full[:max_chars]


def build_parent_body(*, summary: str, toc: list[dict]) -> str:
    """Build the parent's body: LLM summary + Markdown ToC linking to children.

    Each `toc` entry is `{path, title}`. The body is read by humans (Streamlit
    app) and by agents (Vector Search), so keep it natural Markdown.
    """
    lines = [summary.strip(), "", "## Contents"]
    for entry in toc:
        lines.append(f"- [{entry['title']}]({entry['path']})")
    return "\n".join(lines)
