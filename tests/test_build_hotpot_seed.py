"""Tests for scripts/build_hotpot_seed.py — HotpotQA → WikiBricks conversion."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_hotpot_seed import _slugify, convert  # noqa: E402,I001


SAMPLE = [
    {
        "_id": "q1",
        "question": "Who directed Sinister and Ed Wood?",
        "context": [
            ["Scott Derrickson", ["Scott Derrickson is an American director.", "He was born in 1966."]],
            ["Ed Wood (film)", ["Ed Wood is a 1994 film directed by Tim Burton."]],
            ["Tim Burton", ["Tim Burton is a filmmaker.", "He directed Ed Wood."]],
        ],
        "supporting_facts": [["Scott Derrickson", 0], ["Ed Wood (film)", 0]],
    },
    {
        "_id": "q2",
        "question": "What year was Ed Wood made?",
        "context": [
            ["Ed Wood (film)", ["Ed Wood is a 1994 film directed by Tim Burton."]],
        ],
        "supporting_facts": [["Ed Wood (film)", 0]],
    },
]


class TestSlugify:
    def test_lowercase_and_underscore(self):
        assert _slugify("Ed Wood (film)") == "ed_wood_film"

    def test_strips_punct(self):
        assert _slugify("Scott Derrickson!") == "scott_derrickson"

    def test_empty_returns_untitled(self):
        assert _slugify("") == "untitled"


class TestConvert:
    def test_dedupes_pages_across_questions(self):
        pages, _, _ = convert(SAMPLE)
        paths = [p["path"] for p in pages]
        assert len(paths) == len(set(paths)), "pages should be deduped"
        assert "ed_wood_film" in paths

    def test_page_has_paragraph_sections(self):
        pages, _, _ = convert(SAMPLE)
        sd = next(p for p in pages if p["path"] == "scott_derrickson")
        assert sd["content"]["paragraphs"][0] == {
            "paragraph_id": 0,
            "text": "Scott Derrickson is an American director.",
        }

    def test_page_type_is_entity(self):
        pages, _, _ = convert(SAMPLE)
        assert all(p["page_type"] == "entity" for p in pages)

    def test_supporting_pages_linked(self):
        _, links, _ = convert(SAMPLE)
        supports = [link for link in links if link["link_type"] == "supports"]
        assert {"source_path": "scott_derrickson", "target_path": "ed_wood_film",
                "link_type": "supports"} in supports
        assert {"source_path": "ed_wood_film", "target_path": "scott_derrickson",
                "link_type": "supports"} in supports

    def test_single_supporting_page_no_link(self):
        _, links, _ = convert([SAMPLE[1]])
        assert links == []

    def test_queries_have_relevant_paths(self):
        _, _, queries = convert(SAMPLE)
        q1 = next(q for q in queries if q["id"] == "q1")
        assert set(q1["relevant_paths"]) == {"scott_derrickson", "ed_wood_film"}

    def test_links_deduped(self):
        _, links, _ = convert(SAMPLE + SAMPLE)
        seen = {(link["source_path"], link["target_path"], link["link_type"]) for link in links}
        assert len(seen) == len(links)
