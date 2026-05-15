"""Integration tests for `wikibricks.export_karpathy`.

Round-trip contract: starting from the example fixture, importing it
produces pages + edges; exporting those should yield markdown files whose
content (after re-parsing) carries the same titles, paths, tags, and
typed-edge relationships.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from wikibricks.client import WikiClient
from wikibricks.export_karpathy import main, write_pages  # noqa: I001

FIXTURE_IMPORT = Path(__file__).parent.parent / "examples" / "karpathy_wiki"


class TestWritePages:
    def test_writes_one_md_per_page(self):
        pages = [
            {"path": "topics/foo", "title": "Foo", "page_type": "concept",
             "tags": ["alpha"], "memory_class": "semantic",
             "content": {"summary": "s", "body": "Body of Foo."}},
            {"path": "topics/bar", "title": "Bar", "page_type": "concept",
             "tags": [], "memory_class": "semantic",
             "content": {"summary": "s", "body": "Body of Bar."}},
        ]
        edges = []
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            n = write_pages(target, pages, edges)
            assert n == 2
            assert (target / "topics" / "foo.md").is_file()
            assert (target / "topics" / "bar.md").is_file()

    def test_outgoing_edges_become_related_section(self):
        pages = [
            {"path": "topics/foo", "title": "Foo", "page_type": "concept",
             "tags": [], "memory_class": "semantic",
             "content": {"body": "Body"}},
            {"path": "topics/bar", "title": "Bar", "page_type": "concept",
             "tags": [], "memory_class": "semantic",
             "content": {"body": "Body"}},
        ]
        edges = [
            {"source_path": "topics/foo", "target_path": "topics/bar",
             "link_type": "cites"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            write_pages(target, pages, edges)
            foo_md = (target / "topics" / "foo.md").read_text()
            assert "## Related" in foo_md
            assert "cites::[[topics/bar]]" in foo_md
            # Bar has no outgoing edges
            bar_md = (target / "topics" / "bar.md").read_text()
            assert "## Related" not in bar_md

    def test_subfolders_created_automatically(self):
        pages = [
            {"path": "topics/databricks/uc", "title": "UC",
             "page_type": "concept", "tags": [], "memory_class": "semantic",
             "content": {"body": "Body"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            write_pages(target, pages, [])
            assert (target / "topics" / "databricks" / "uc.md").is_file()


class TestRoundTrip:
    """Import the fixture → export the result → assert lossless round-trip."""

    def test_import_then_export_preserves_pages_and_typed_edges(self):
        # Run the importer's build_pages_and_edges over the fixture
        from wikibricks.import_karpathy import build_pages_and_edges
        pages, edges = build_pages_and_edges(FIXTURE_IMPORT)

        # Each imported page record carries `_body` stripped before write —
        # restore a minimal content dict for the exporter.
        for p in pages:
            if "content" not in p:
                p["content"] = {"summary": "", "body": p.get("body", "")}

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            write_pages(target, pages, edges)

            # Re-import the exported folder
            pages2, edges2 = build_pages_and_edges(target)
            paths_in = {p["path"] for p in pages}
            paths_out = {p["path"] for p in pages2}
            assert paths_in == paths_out, (
                f"round-trip page set differs: lost={paths_in-paths_out}, "
                f"added={paths_out-paths_in}"
            )

            # Typed edges round-trip: the 'cites' edge from delta-lake →
            # apache-spark must survive.
            cites_in = [(e["source_path"], e["target_path"]) for e in edges
                        if e["link_type"] == "cites"]
            cites_out = [(e["source_path"], e["target_path"]) for e in edges2
                         if e["link_type"] == "cites"]
            assert set(cites_in) <= set(cites_out), (
                f"cites edges lost in round-trip: {set(cites_in) - set(cites_out)}"
            )


class TestMain:
    def test_main_calls_wiki_and_writes_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.argv", [
            "export-karpathy", str(tmp_path),
            "--catalog", "c", "--schema", "s", "--warehouse-id", "wh",
        ])

        wiki = MagicMock(spec_set=WikiClient)
        wiki.list_pages.return_value = [
            {"path": "topics/foo", "title": "Foo", "page_type": "concept",
             "tags": [], "memory_class": "semantic",
             "content": {"body": "Hello"}},
        ]
        # _exec returns the edges query result (empty)
        empty_resp = MagicMock()
        empty_resp.result.data_array = []
        empty_resp.manifest = MagicMock()
        wiki._exec.return_value = empty_resp
        wiki._manifest_columns.return_value = []

        with patch("databricks.sdk.WorkspaceClient", return_value=MagicMock()), \
             patch("wikibricks.WikiClient", return_value=wiki):
            rc = main()
        assert rc == 0
        assert (tmp_path / "topics" / "foo.md").is_file()
