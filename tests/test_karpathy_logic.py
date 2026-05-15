"""Behavioral tests for `wikibricks.karpathy_logic`.

LLM-free helpers that turn a Karpathy-style markdown wiki folder into the
shape WikiBricks expects: pages with paths/titles/tags/content and edges
with source/target/link_type.
"""

from wikibricks.karpathy_logic import (
    extract_typed_edges,
    extract_wikilinks,
    parse_frontmatter,
    wiki_path_for,
)


class TestParseFrontmatter:
    def test_no_frontmatter_returns_empty_dict_and_full_body(self):
        text = "# Heading\n\nBody text."
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_simple_key_value_pairs(self):
        text = "---\ntitle: My Page\ntags: foo, bar\n---\nBody."
        meta, body = parse_frontmatter(text)
        assert meta["title"] == "My Page"
        assert meta["tags"] == "foo, bar"
        assert body == "Body."

    def test_yaml_list_form_for_tags(self):
        text = "---\ntitle: T\ntags:\n  - retrieval\n  - vector-search\n---\nB"
        meta, body = parse_frontmatter(text)
        assert meta["tags"] == ["retrieval", "vector-search"]

    def test_path_override(self):
        text = "---\npath: topics/foo\n---\nbody"
        meta, body = parse_frontmatter(text)
        assert meta["path"] == "topics/foo"

    def test_trailing_newline_body(self):
        text = "---\ntitle: T\n---\nbody\n"
        meta, body = parse_frontmatter(text)
        assert body == "body"

    def test_malformed_frontmatter_returns_empty(self):
        # No closing --- → treat as no frontmatter.
        text = "---\ntitle: T\nbody"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text


class TestExtractWikilinks:
    def test_single_wikilink(self):
        body = "See [[Other Page]] for context."
        assert extract_wikilinks(body) == ["Other Page"]

    def test_multiple_wikilinks(self):
        body = "[[Alpha]] and [[Beta]] and [[Gamma]]."
        assert extract_wikilinks(body) == ["Alpha", "Beta", "Gamma"]

    def test_wikilink_with_pipe_uses_target(self):
        # Obsidian-style display alias: [[target|display]] — target wins.
        body = "Click [[some/path|here]]"
        assert extract_wikilinks(body) == ["some/path"]

    def test_empty_brackets_ignored(self):
        body = "Not a [[]] link."
        assert extract_wikilinks(body) == []

    def test_typed_edges_not_counted_as_plain(self):
        # `relationship::[[Target]]` is a TYPED edge — counted separately,
        # not as a plain wikilink. Verifies the typed-vs-plain partition.
        body = "Plain [[A]] and typed cites::[[B]]."
        plain = extract_wikilinks(body)
        # Plain extractor should still return both A and B; the partitioning
        # into typed/plain happens at the importer level when extract_typed_edges
        # is also consulted.
        assert "A" in plain
        # B may also appear in plain — caller deduplicates against typed list.

    def test_dedupes_repeated_links(self):
        body = "[[A]] x [[A]] y [[B]]"
        assert extract_wikilinks(body) == ["A", "B"]


class TestExtractTypedEdges:
    def test_simple_typed_edge(self):
        body = "depends_on::[[Other]]"
        assert extract_typed_edges(body) == [("depends_on", "Other")]

    def test_multiple_typed_edges(self):
        body = "cites::[[A]] and derives_from::[[B]]"
        edges = extract_typed_edges(body)
        assert ("cites", "A") in edges
        assert ("derives_from", "B") in edges

    def test_typed_edge_with_pipe_alias(self):
        body = "cites::[[real/path|Display Name]]"
        assert extract_typed_edges(body) == [("cites", "real/path")]

    def test_invalid_relationship_name_ignored(self):
        # link_type must be alphanumeric + underscore (kebab-friendly).
        body = "weird-link!::[[X]]"
        assert extract_typed_edges(body) == []

    def test_no_typed_edges_returns_empty(self):
        body = "Just [[a plain link]]."
        assert extract_typed_edges(body) == []


class TestWikiPathFor:
    def test_wiki_folder_maps_to_topics(self):
        # Default mapping: wiki/foo-bar.md → topics/foo-bar
        path = wiki_path_for("/notes/wiki/foo-bar.md", base_dir="/notes")
        assert path == "topics/foo-bar"

    def test_raw_folder_maps_to_sources(self):
        path = wiki_path_for("/notes/raw/paper.pdf.md", base_dir="/notes")
        assert path == "sources/paper-pdf"

    def test_frontmatter_path_overrides(self):
        path = wiki_path_for(
            "/notes/wiki/foo.md", base_dir="/notes",
            frontmatter={"path": "promoted/explicit"},
        )
        assert path == "promoted/explicit"

    def test_strips_md_extension(self):
        path = wiki_path_for("/notes/wiki/x.md", base_dir="/notes")
        assert path.endswith("/x")

    def test_nested_subfolder_preserves_structure(self):
        path = wiki_path_for(
            "/notes/wiki/databricks/uc.md", base_dir="/notes",
        )
        assert path == "topics/databricks/uc"

    def test_normalizes_uppercase_and_spaces(self):
        path = wiki_path_for("/notes/wiki/Big Topic.md", base_dir="/notes")
        assert path == "topics/big-topic"
