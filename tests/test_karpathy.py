from __future__ import annotations

from pathlib import Path

from wikibricks import WikiClient
from wikibricks.export_karpathy import fetch_pages_and_edges, write_pages
from wikibricks.import_karpathy import build_pages_and_edges
from wikibricks.karpathy_logic import parse_frontmatter, wiki_path_for

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "karpathy_wiki"


def test_frontmatter_and_path_mapping_support_plain_markdown():
    scalar, _ = parse_frontmatter("---\ntitle: One\ntags: a, b\n---\nBody")
    listed, _ = parse_frontmatter(
        "---\ntitle: Two\ntags:\n  - a\n  - b\n---\nBody"
    )

    assert scalar["tags"] == "a, b"
    assert listed["tags"] == ["a", "b"]
    assert wiki_path_for("/notes/wiki/Big Topic.md", "/notes") == "topics/big-topic"
    assert (
        wiki_path_for(
            "/notes/elsewhere.md",
            "/notes",
            {"path": "topics/explicit"},
        )
        == "topics/explicit"
    )


def test_fixture_import_is_complete_linked_and_idempotent(postgres_url: str):
    pages, edges = build_pages_and_edges(FIXTURE)
    wiki = WikiClient(postgres_url)

    assert wiki.write_pages(pages) == 6
    assert wiki.commit_edges(edges) == len(edges)
    assert wiki.write_pages(pages) == 6
    assert wiki.commit_edges(edges) == len(edges)

    listed = wiki.list_pages()
    delta_links = wiki.graph_neighbors("topics/delta-lake")
    by_path = {page["path"]: page for page in pages}
    assert len(listed) == 6
    assert "lakehouse" in by_path["topics/databricks"]["tags"]
    assert {"storage", "acid"} <= set(by_path["topics/delta-lake"]["tags"])
    assert any(
        edge["path"] == "topics/apache-spark" and edge["link_type"] == "cites"
        for edge in delta_links
    )
    assert len(wiki.history("topics/databricks")) == 1


def test_postgres_export_round_trips_pages_and_typed_links(
    postgres_url: str,
    tmp_path: Path,
):
    pages, edges = build_pages_and_edges(FIXTURE)
    wiki = WikiClient(postgres_url)
    wiki.write_pages(pages)
    wiki.commit_edges(edges)

    exported_pages, exported_edges = fetch_pages_and_edges(wiki)
    assert write_pages(tmp_path, exported_pages, exported_edges) == 6
    round_trip_pages, round_trip_edges = build_pages_and_edges(tmp_path)

    assert {page["path"] for page in round_trip_pages} == {
        page["path"] for page in pages
    }
    assert {
        (edge["source_path"], edge["target_path"])
        for edge in round_trip_edges
        if edge["link_type"] == "cites"
    } == {
        (edge["source_path"], edge["target_path"])
        for edge in edges
        if edge["link_type"] == "cites"
    }


def test_export_excludes_session_and_archive_evidence():
    class FakeWiki:
        def list_pages(self):
            return [
                {"path": "topics/one", "page_type": "concept"},
                {"path": "sessions/u/one", "page_type": "session"},
                {"path": "archive/one", "page_type": "archive"},
            ]

        def read_page(self, path):
            assert path == "topics/one"
            return {
                "path": path,
                "title": "One",
                "page_type": "concept",
                "content": {"body": "One"},
            }

        def graph_neighbors(self, path):
            assert path == "topics/one"
            return []

    pages, edges = fetch_pages_and_edges(FakeWiki())

    assert [page["path"] for page in pages] == ["topics/one"]
    assert edges == []
