from __future__ import annotations

from pathlib import Path

from wikibricks.curation.mentions import mention_edges
from wikibricks.maintenance import curate_database
from wikibricks.storage.sqlite_store import SQLiteStore


def test_mention_edges_detect_exact_path():
    pages = [
        {"path": "topics/source", "title": "Source", "content_text": "See topics/target."},
        {"path": "topics/target", "title": "Target", "content_text": ""},
    ]

    assert mention_edges(pages) == [
        {
            "source_path": "topics/source",
            "target_path": "topics/target",
            "link_type": "mentions",
            "origin": "curate",
            "evidence": "path",
        }
    ]


def test_mention_edges_detect_title_case_insensitively():
    pages = [
        {"path": "topics/source", "title": "Source", "content_text": "Read Slide Hub."},
        {"path": "topics/slide-hub", "title": "Slide Hub", "content_text": ""},
    ]

    assert mention_edges(pages)[0]["evidence"] == "title"


def test_mention_edges_ignores_short_titles():
    pages = [
        {"path": "topics/source", "title": "Source Page", "content_text": "Read Short."},
        {"path": "topics/target", "title": "Short", "content_text": ""},
    ]

    assert mention_edges(pages) == []


def test_mention_edges_ignores_path_and_title_inside_longer_tokens():
    pages = [
        {"path": "topics/source", "title": "Source Page", "content_text": "topics/target-extra xslide hub"},
        {"path": "topics/target", "title": "Slide Hub", "content_text": ""},
    ]

    assert mention_edges(pages) == []


def test_mention_edges_ignores_self_and_meta_pages():
    pages = [
        {"path": "topics/self", "title": "Self Page", "content_text": "topics/self"},
        {"path": "_meta/source", "title": "Meta Source", "content_text": "topics/target"},
        {"path": "topics/target", "title": "Target Page", "content_text": ""},
    ]

    assert mention_edges(pages) == []


def test_mention_edges_prefers_path_when_both_match():
    pages = [
        {"path": "topics/source", "title": "Source", "content_text": "topics/target mentions Target Page"},
        {"path": "topics/target", "title": "Target Page", "content_text": ""},
    ]

    assert mention_edges(pages)[0]["evidence"] == "path"


def test_curate_database_links_mentioning_pages(tmp_path: Path):
    database_path = tmp_path / "memory.db"
    store = SQLiteStore(database_path)
    store.migrate()
    store.write_page(
        "topics/path-source",
        "Path Source",
        {"summary": "links by path", "body": "topics/target"},
    )
    store.write_page(
        "topics/title-source",
        "Title Source",
        {"summary": "links by title", "body": "The Slide Hub"},
    )
    store.write_page("topics/target", "Slide Hub", {"summary": "target", "body": ""})

    result = curate_database(database_path)

    assert result["linked_pages"] == 2
    assert result["orphan_pages"] == []
    for source_path in ("topics/path-source", "topics/title-source"):
        neighbors = store.graph_neighbors(source_path)
        assert len(neighbors) == 1
        neighbor = neighbors[0]
        assert neighbor["path"] == "topics/target"
        assert neighbor["link_type"] == "mentions"
        assert neighbor["origin"] == "curate"


def test_curate_database_does_not_rewrite_existing_mentions(tmp_path: Path):
    database_path = tmp_path / "memory.db"
    store = SQLiteStore(database_path)
    store.migrate()
    store.write_page("topics/source", "Source", {"summary": "source", "body": "topics/target"})
    store.write_page("topics/target", "Slide Hub", {"summary": "target", "body": ""})

    assert curate_database(database_path)["linked_pages"] == 1
    assert curate_database(database_path)["linked_pages"] == 0


def test_postgres_curate_links_mentioning_pages(store, postgres_url: str):
    store.write_page("topics/slide-hub", "Slide Hub", {"summary": "target", "body": "Deck search."})
    store.write_page(
        "synthesis/slide-hub-latency",
        "Search latency",
        {"summary": "source", "body": "Slide Hub search got faster."},
    )

    assert curate_database(postgres_url)["linked_pages"] == 1
    assert curate_database(postgres_url)["linked_pages"] == 0
