"""Tests for `wikibricks.karpathy_logic` export helpers.

Round-trip with the v0.6.0 importer: a folder of markdown imported into
wikibricks, exported back out, and diffed should round-trip clean
(modulo allowed normalisations like wikilink target resolution).
"""

from wikibricks.karpathy_logic import (
    map_wiki_path_to_file,
    render_page_markdown,
)


class TestMapWikiPathToFile:
    def test_topics_path_to_relative_md_file(self):
        # topics/databricks → topics/databricks.md (relative path under target)
        assert map_wiki_path_to_file("topics/databricks") == "topics/databricks.md"

    def test_sources_path_to_relative_md_file(self):
        assert map_wiki_path_to_file("sources/bar") == "sources/bar.md"

    def test_nested_path_preserves_subfolders(self):
        assert map_wiki_path_to_file("topics/databricks/uc") == "topics/databricks/uc.md"

    def test_notes_index_special_case_top_level(self):
        # The importer accepts notes/index via frontmatter override.
        # Exporter writes it under notes/ to round-trip.
        assert map_wiki_path_to_file("notes/index") == "notes/index.md"


class TestRenderPageMarkdown:
    def _page(self, **overrides) -> dict:
        page = {
            "page_id": "p1",
            "path": "topics/foo",
            "title": "Foo",
            "page_type": "concept",
            "tags": ["alpha", "beta"],
            "memory_class": "semantic",
            "content": {"summary": "s", "body": "Body text."},
        }
        page.update(overrides)
        return page

    def test_frontmatter_includes_title_tags_path(self):
        md = render_page_markdown(self._page(), outgoing_edges=[])
        assert md.startswith("---\n")
        assert "title: Foo" in md
        # YAML list form for tags
        assert "tags:" in md
        assert "- alpha" in md
        assert "- beta" in md
        assert "path: topics/foo" in md
        assert "memory_class: semantic" in md
        assert "page_type: concept" in md

    def test_body_preserved_verbatim(self):
        md = render_page_markdown(self._page(), outgoing_edges=[])
        assert "Body text." in md

    def test_empty_tags_omits_list_form(self):
        md = render_page_markdown(self._page(tags=[]), outgoing_edges=[])
        # No empty `tags:` line dangling without items.
        assert "tags:\n  - " not in md

    def test_related_section_appears_when_outgoing_edges_exist(self):
        edges = [
            {"target_path": "topics/bar", "link_type": "related"},
            {"target_path": "topics/baz", "link_type": "cites"},
        ]
        md = render_page_markdown(self._page(), outgoing_edges=edges)
        assert "## Related" in md
        # Plain edge becomes [[wikilink]]
        assert "[[topics/bar]]" in md
        # Typed edge becomes link_type::[[wikilink]] (v2 LLM Wiki syntax)
        assert "cites::[[topics/baz]]" in md

    def test_no_related_section_when_no_outgoing_edges(self):
        md = render_page_markdown(self._page(), outgoing_edges=[])
        assert "## Related" not in md

    def test_content_dict_string_form_handled(self):
        # If content was stored as a JSON string (not dict), still extract body.
        page = self._page(content='{"summary": "s", "body": "From string."}')
        md = render_page_markdown(page, outgoing_edges=[])
        assert "From string." in md

    def test_default_memory_class_when_missing(self):
        page = self._page()
        del page["memory_class"]
        md = render_page_markdown(page, outgoing_edges=[])
        assert "memory_class: semantic" in md

    def test_round_trip_with_importer_parses_back_to_same_fields(self):
        # The exporter's output must be a valid input for the v0.6.0 importer.
        # This is the round-trip contract.
        from wikibricks.karpathy_logic import (
            extract_typed_edges,
            extract_wikilinks,
            parse_frontmatter,
        )
        edges = [
            {"target_path": "topics/bar", "link_type": "related"},
            {"target_path": "topics/baz", "link_type": "cites"},
        ]
        md = render_page_markdown(self._page(), outgoing_edges=edges)
        meta, body = parse_frontmatter(md)
        assert meta.get("title") == "Foo"
        assert meta.get("path") == "topics/foo"
        assert meta.get("memory_class") == "semantic"
        plain = extract_wikilinks(body)
        typed = extract_typed_edges(body)
        # Plain edge round-trips as a plain wikilink
        assert "topics/bar" in plain
        # Typed edge round-trips as a typed edge
        assert ("cites", "topics/baz") in typed
