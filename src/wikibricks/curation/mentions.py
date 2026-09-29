from __future__ import annotations

import re

_PATH_BOUNDARY_BEFORE = r"(?<![\w/\-])"
_PATH_BOUNDARY_AFTER = r"(?![\w/\-])"


def _mentions_path(content_text: str, path: str) -> bool:
    return re.search(
        f"{_PATH_BOUNDARY_BEFORE}{re.escape(path)}{_PATH_BOUNDARY_AFTER}",
        content_text,
    ) is not None


def _mentions_title(content_text: str, title: str) -> bool:
    if len(title) < 6:
        return False
    return re.search(
        f"{_PATH_BOUNDARY_BEFORE}{re.escape(title)}{_PATH_BOUNDARY_AFTER}",
        content_text,
        flags=re.IGNORECASE,
    ) is not None


def mention_edges(pages: list[dict]) -> list[dict]:
    """Derive deterministic page-to-page mention links from active pages."""
    pages = [page for page in pages if not page["path"].startswith("_meta/")]
    edges: list[dict] = []
    for source in sorted(pages, key=lambda page: page["path"]):
        for target in sorted(pages, key=lambda page: page["path"]):
            if source["path"] == target["path"]:
                continue
            if _mentions_path(source["content_text"], target["path"]):
                evidence = "path"
            elif _mentions_title(source["content_text"], target["title"]):
                evidence = "title"
            else:
                continue
            edges.append(
                {
                    "source_path": source["path"],
                    "target_path": target["path"],
                    "link_type": "mentions",
                    "origin": "curate",
                    "evidence": evidence,
                }
            )
    return edges


def new_mention_edges(conn) -> list[dict]:
    """Return mention edges for active pages that no stored `mentions` link covers.

    Uses unparameterized SQL and positional rows so it runs on sqlite3 and psycopg.
    """
    rows = conn.execute(
        "SELECT p.path, v.title, v.content_text FROM pages p "
        "JOIN page_versions v ON v.version_id = p.current_version_id "
        "WHERE p.status = 'active'"
    ).fetchall()
    existing = {
        (row[0], row[1])
        for row in conn.execute(
            "SELECT source.path, target.path FROM links l "
            "JOIN pages source ON source.page_id = l.source_page_id "
            "JOIN pages target ON target.page_id = l.target_page_id "
            "WHERE l.link_type = 'mentions'"
        ).fetchall()
    }
    edges = mention_edges(
        [{"path": row[0], "title": row[1], "content_text": row[2]} for row in rows]
    )
    return [
        edge for edge in edges if (edge["source_path"], edge["target_path"]) not in existing
    ]
