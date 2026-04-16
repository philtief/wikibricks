"""Tests for WikiBricks seed data and AutoEval functions."""

import json

from wiki_ops import VS_INDEX, autoeval_config, seed_pages


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

    def test_includes_insurance_domain_pages(self):
        paths = [p["path"] for p in seed_pages()]
        has_claims = any("claims" in p for p in paths)
        assert has_claims, "Seed data should include claims-related pages for the demo"

    def test_content_json_serializable(self):
        for page in seed_pages():
            serialized = json.dumps(page["content"])
            assert isinstance(serialized, str)


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
