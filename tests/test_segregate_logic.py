"""Pure-helper tests for `wikibricks.segregate_logic`.

The split feature breaks oversize pages into a parent (summary + ToC) + N
chunk children, joined by `parent_id` and ordered by `chunk_index`. All LLM
work lives in the notebook; these helpers are deterministic.
"""

from wikibricks.segregate_logic import (
    DEFAULT_MAX_CHARS_PER_CHUNK,
    build_parent_body,
    child_path,
    child_title,
    chunk_at_boundaries,
)


class TestChunkAtBoundaries:
    def test_short_body_returns_single_chunk(self):
        chunks = chunk_at_boundaries("hello world", max_chars=100)
        assert chunks == ["hello world"]

    def test_splits_on_h2_headings(self):
        body = "intro\n\n## Section A\nbody A\n\n## Section B\nbody B"
        chunks = chunk_at_boundaries(body, max_chars=20)
        # Each section should land in its own chunk; intro is its own chunk
        assert len(chunks) >= 2
        # No chunk exceeds max_chars unless a single paragraph itself is bigger
        assert all(len(c) <= 20 or "\n" not in c for c in chunks)

    def test_falls_back_to_paragraph_boundary(self):
        body = "para1 line\n\npara2 line\n\npara3 line"
        chunks = chunk_at_boundaries(body, max_chars=15)
        assert len(chunks) == 3
        assert "para1" in chunks[0]
        assert "para2" in chunks[1]
        assert "para3" in chunks[2]

    def test_groups_paragraphs_under_max(self):
        # Three paragraphs of ~10 chars; max=25 should pack 2 per chunk
        body = "a" * 8 + "\n\n" + "b" * 8 + "\n\n" + "c" * 8
        chunks = chunk_at_boundaries(body, max_chars=25)
        assert len(chunks) == 2

    def test_preserves_total_content(self):
        body = "## A\nfoo\n\n## B\nbar\n\n## C\nbaz"
        chunks = chunk_at_boundaries(body, max_chars=10)
        # Concat (with separator stripped) should contain all original text
        joined = "\n\n".join(chunks)
        for needle in ("foo", "bar", "baz"):
            assert needle in joined

    def test_empty_body_returns_empty_list(self):
        assert chunk_at_boundaries("", max_chars=100) == []

    def test_single_oversize_paragraph_kept_intact(self):
        # A paragraph bigger than max_chars cannot be split further by this
        # helper — it stays as one chunk; the LLM step would summarize it.
        body = "x" * 200
        chunks = chunk_at_boundaries(body, max_chars=50)
        assert len(chunks) == 1
        assert chunks[0] == body


class TestDefaultMaxChars:
    """v0.7.4: raise default from 8 000 → 30 000.

    Measured against last 24h of recorder traffic: avg session body is
    ~165 KB which produced ~21 chunks at the 8 000 threshold. At 30 000
    the same body produces ~5–6 chunks — closer to a useful per-page
    granularity for Vector Search without exploding page count.
    """

    def test_default_is_30k(self):
        assert DEFAULT_MAX_CHARS_PER_CHUNK == 30_000

    def test_default_yields_few_chunks_on_typical_session_body(self):
        # Synthesise a 160 KB body of mid-sized paragraphs.
        paragraph = ("word " * 200).strip()  # ~1 KB each
        body = "\n\n".join([paragraph] * 160)
        chunks = chunk_at_boundaries(body, max_chars=DEFAULT_MAX_CHARS_PER_CHUNK)
        # 160 KB / 30 KB ≈ 6 chunks. Allow 4–8 for paragraph-packing slack.
        assert 4 <= len(chunks) <= 8, f"got {len(chunks)} chunks"

    def test_default_is_well_above_paragraph_size(self):
        # Sanity: a single typical Markdown paragraph is well under the
        # default. Saves us from silent regressions to absurdly low values.
        assert DEFAULT_MAX_CHARS_PER_CHUNK > 5_000


class TestChildPath:
    def test_zero_pads_index(self):
        assert child_path("topics/foo", 1) == "topics/foo/chunks/01"

    def test_handles_double_digits(self):
        assert child_path("topics/foo", 12) == "topics/foo/chunks/12"

    def test_strips_trailing_slash(self):
        assert child_path("topics/foo/", 3) == "topics/foo/chunks/03"


class TestChildTitle:
    def test_concatenates_parent_and_chunk(self):
        assert child_title("Big Doc", "Section A") == "Big Doc - Section A"

    def test_truncates_to_120_chars(self):
        long_parent = "p" * 100
        long_chunk = "c" * 100
        assert len(child_title(long_parent, long_chunk)) <= 120


class TestBuildParentBody:
    def test_includes_summary_and_toc(self):
        body = build_parent_body(
            summary="A long doc about widgets.",
            toc=[
                {"path": "topics/foo/chunks/01", "title": "Intro"},
                {"path": "topics/foo/chunks/02", "title": "Details"},
            ],
        )
        assert "widgets" in body
        assert "## Contents" in body
        assert "topics/foo/chunks/01" in body
        assert "Intro" in body
        assert "topics/foo/chunks/02" in body

    def test_empty_toc_still_includes_summary(self):
        body = build_parent_body(summary="hello", toc=[])
        assert "hello" in body
