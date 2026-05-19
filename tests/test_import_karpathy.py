"""Integration test for `wikibricks.import_karpathy` against the example fixture.

Spec_set'd WikiClient so any drift between the importer's expected API and
the real WikiClient raises immediately.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from wikibricks.client import WikiClient
from wikibricks.import_karpathy import build_pages_and_edges, main

FIXTURE = Path(__file__).parent.parent / "examples" / "karpathy_wiki"


class TestBuildPagesAndEdges:
    def test_discovers_all_markdown_files(self):
        pages, _ = build_pages_and_edges(FIXTURE)
        paths = {p["path"] for p in pages}
        assert "topics/databricks" in paths
        assert "topics/delta-lake" in paths
        assert "topics/apache-spark" in paths
        assert "topics/unity-catalog" in paths
        assert "sources/karpathy-llm-wiki-gist" in paths
        # index.md has an explicit `path:` frontmatter override
        assert "notes/index" in paths

    def test_typed_edge_preserves_link_type(self):
        _, edges = build_pages_and_edges(FIXTURE)
        # delta-lake.md has `cites::[[Apache Spark]]` — typed edge.
        cites = [
            e for e in edges
            if e["source_path"] == "topics/delta-lake"
            and e["target_path"] == "topics/apache-spark"
        ]
        assert len(cites) == 1
        assert cites[0]["link_type"] == "cites"

    def test_plain_wikilink_becomes_related(self):
        _, edges = build_pages_and_edges(FIXTURE)
        # databricks.md has `[[Apache Spark]]` and `[[Delta Lake]]` — plain.
        plain = [
            e for e in edges
            if e["source_path"] == "topics/databricks"
            and e["link_type"] == "related"
        ]
        plain_targets = {e["target_path"] for e in plain}
        assert "topics/apache-spark" in plain_targets
        assert "topics/delta-lake" in plain_targets

    def test_tags_parsed_from_frontmatter(self):
        pages, _ = build_pages_and_edges(FIXTURE)
        by_path = {p["path"]: p for p in pages}
        # YAML list form on databricks.md
        assert "lakehouse" in by_path["topics/databricks"]["tags"]
        # Comma-separated string form on delta-lake.md
        assert "storage" in by_path["topics/delta-lake"]["tags"]
        assert "acid" in by_path["topics/delta-lake"]["tags"]

    def test_title_falls_back_to_first_h1_then_filename(self):
        pages, _ = build_pages_and_edges(FIXTURE)
        by_path = {p["path"]: p for p in pages}
        assert by_path["topics/databricks"]["title"] == "Databricks"


class TestMain:
    def _argv(self, *extra):
        return ["import-karpathy", str(FIXTURE), *extra]

    def test_dry_run_writes_nothing(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", self._argv("--dry-run"))
        # Patch WorkspaceClient + WikiClient so importing wikibricks doesn't
        # require credentials. They shouldn't be called on --dry-run anyway.
        with patch("databricks.sdk.WorkspaceClient") as ws, \
             patch("wikibricks.WikiClient") as wc:
            rc = main()
        assert rc == 0
        ws.assert_not_called()
        wc.assert_not_called()
        out = capsys.readouterr().out
        assert '"pages": 6' in out or '"pages":6' in out

    def test_writes_pages_and_resolves_edges_to_ids(self, monkeypatch):
        monkeypatch.setattr("sys.argv", self._argv(
            "--catalog", "c", "--schema", "s", "--warehouse-id", "wh"))

        wiki = MagicMock(spec_set=WikiClient)
        wiki.write_pages.return_value = 6
        wiki.list_pages.return_value = [
            {"path": "topics/databricks", "page_id": "id-db"},
            {"path": "topics/delta-lake", "page_id": "id-dl"},
            {"path": "topics/apache-spark", "page_id": "id-as"},
            {"path": "topics/unity-catalog", "page_id": "id-uc"},
            {"path": "sources/karpathy-llm-wiki-gist", "page_id": "id-src"},
            {"path": "notes/index", "page_id": "id-idx"},
        ]
        wiki.commit_edges.return_value = 13

        with patch("databricks.sdk.WorkspaceClient", return_value=MagicMock()), \
             patch("wikibricks.WikiClient", return_value=wiki):
            rc = main()
        assert rc == 0
        wiki.write_pages.assert_called_once()
        wiki.commit_edges.assert_called_once()
        resolved = wiki.commit_edges.call_args[0][0]
        # Every edge has both source_page_id and target_page_id resolved.
        for e in resolved:
            assert e["source_page_id"] and not e["source_page_id"].startswith("topics/")
            assert e["target_page_id"] and not e["target_page_id"].startswith("topics/")
        # The typed `cites` edge survives the round-trip.
        assert any(e["link_type"] == "cites" for e in resolved)
