"""Tests for WikiBricks seed data and AutoEval functions."""

import json

import pytest

from wikibricks import seeds
from wikibricks.ops import VS_INDEX, autoeval_config, eval_queries, seed_pages


class TestSeedPages:
    def test_returns_list_of_dicts(self):
        pages = seed_pages()
        assert isinstance(pages, list)
        assert len(pages) > 0
        assert isinstance(pages[0], dict)

    def test_each_page_has_required_fields(self):
        required = {"path", "title", "page_type", "content", "created_by", "tags"}
        for page in seed_pages():
            missing = required - set(page.keys())
            assert not missing, f"Page '{page.get('title', '?')}' missing fields: {missing}"

    def test_content_has_summary_and_body(self):
        for page in seed_pages():
            content = page["content"]
            assert isinstance(content, dict)
            assert "summary" in content, f"Page '{page['title']}' content missing summary"
            assert "body" in content, f"Page '{page['title']}' content missing body"

    def test_page_types_are_valid(self):
        valid_types = {"entity", "concept", "synthesis", "comparison"}
        for page in seed_pages():
            assert page["page_type"] in valid_types, f"Invalid page_type: {page['page_type']}"

    def test_paths_use_slash_hierarchy(self):
        for page in seed_pages():
            assert "/" in page["path"], f"Path '{page['path']}' should use slash hierarchy"

    def test_at_least_five_pages(self):
        assert len(seed_pages()) >= 5

    def test_tags_are_lists(self):
        for page in seed_pages():
            assert isinstance(page["tags"], list)

    def test_paths_have_hierarchy_depth(self):
        for page in seed_pages():
            segments = page["path"].split("/")
            assert len(segments) >= 2, f"Path '{page['path']}' should have at least 2 segments"

    def test_content_json_serializable(self):
        for page in seed_pages():
            serialized = json.dumps(page["content"])
            assert isinstance(serialized, str)


class TestSeedDomains:
    def test_sample_is_default(self):
        assert seed_pages() == seeds.load("sample")

    def test_sample_paths_cover_eval_queries(self):
        seed_paths = {p["path"] for p in seeds.load("sample")}
        eval_paths = {path for q in eval_queries() for path in q["relevant_paths"]}
        missing = eval_paths - seed_paths
        assert not missing, f"sample seed missing paths referenced by eval_queries: {missing}"

    def test_custom_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("WIKIBRICKS_CUSTOM_PAGES", raising=False)
        assert seeds.load("custom") == []

    def test_none_returns_empty(self):
        assert seeds.load("none") == []
        assert seeds.load("") == []

    def test_unknown_domain_raises(self):
        with pytest.raises(ValueError, match="Unknown seed domain"):
            seeds.load("does-not-exist")


class TestAutoEvalConfig:
    def test_returns_dict(self):
        config = autoeval_config()
        assert isinstance(config, dict)

    def test_has_index_name(self):
        config = autoeval_config()
        assert config["index_name"] == VS_INDEX

    def test_has_num_queries(self):
        config = autoeval_config()
        assert "num_queries" in config
        assert config["num_queries"] > 0

    def test_has_metrics(self):
        config = autoeval_config()
        assert "metrics" in config
        metrics = config["metrics"]
        assert "recall" in metrics
        assert "ndcg" in metrics
