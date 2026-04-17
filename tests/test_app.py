"""Tests for WikiBricks app logic."""

import ast
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

APP_DIR = Path(__file__).parent.parent / "app"


class _SessionState(dict):
    """Dict subclass that supports attribute-style access like Streamlit's session_state."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value


@pytest.fixture
def app_module():
    """Import app.py with external deps mocked out."""
    mock_st = MagicMock()
    mock_st.session_state = _SessionState()
    mock_st.chat_input = MagicMock(return_value=None)
    mock_st.sidebar = MagicMock()
    mock_st.sidebar.radio = MagicMock(return_value="Chat")

    mock_openai = MagicMock()
    mock_wikibricks = MagicMock()

    mocks = {
        "streamlit": mock_st,
        "openai": mock_openai,
        "wikibricks": mock_wikibricks,
    }

    with patch.dict(sys.modules, mocks):
        spec = importlib.util.spec_from_file_location("app", APP_DIR / "app.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

    return mod


class TestAppStructure:
    def test_app_is_valid_python(self):
        source = (APP_DIR / "app.py").read_text()
        ast.parse(source)

    def test_app_yaml_exists(self):
        assert (APP_DIR / "app.yaml").exists()

    def test_app_yaml_uses_port_8000(self):
        content = (APP_DIR / "app.yaml").read_text()
        assert "8000" in content

    def test_requirements_txt_exists(self):
        content = (APP_DIR / "requirements.txt").read_text()
        assert "streamlit" in content
        assert "databricks-sdk" in content
        assert "openai" in content


class TestBuildContext:
    def test_empty_pages(self, app_module):
        result = app_module.build_context([])
        assert "No relevant wiki pages found" in result

    def test_single_page(self, app_module):
        pages = [
            {"title": "Fraud Patterns", "path": "claims/fraud", "content_text": "Some content", "page_type": "concept"},
        ]
        result = app_module.build_context(pages)
        assert "Fraud Patterns" in result
        assert "claims/fraud" in result
        assert "Some content" in result
        assert "concept" in result

    def test_multiple_pages_separated(self, app_module):
        pages = [
            {"title": "Page A", "path": "a", "content_text": "Content A", "page_type": "concept"},
            {"title": "Page B", "path": "b", "content_text": "Content B", "page_type": "entity"},
        ]
        result = app_module.build_context(pages)
        assert "Page A" in result
        assert "Page B" in result
        assert "---" in result

    def test_numbered_pages(self, app_module):
        pages = [
            {"title": "First", "path": "first", "content_text": "Content", "page_type": "concept"},
            {"title": "Second", "path": "second", "content_text": "Content", "page_type": "concept"},
        ]
        result = app_module.build_context(pages)
        assert "[1]" in result
        assert "[2]" in result

    def test_missing_fields_handled(self, app_module):
        pages = [{}]
        result = app_module.build_context(pages)
        assert "Untitled" in result


class TestConstants:
    def test_vs_index_name(self, app_module):
        assert app_module.VS_INDEX == "agent_marketplace_catalog.wiki.pages_index"

    def test_search_columns_include_essentials(self, app_module):
        cols = app_module.SEARCH_COLUMNS
        assert "path" in cols
        assert "title" in cols
        assert "content_text" in cols

    def test_system_prompt_mentions_wiki(self, app_module):
        assert "wiki" in app_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_instructs_citation(self, app_module):
        assert "cite" in app_module.SYSTEM_PROMPT.lower() or "path" in app_module.SYSTEM_PROMPT.lower()

    def test_page_types(self, app_module):
        assert "concept" in app_module.PAGE_TYPES
        assert "entity" in app_module.PAGE_TYPES
        assert "synthesis" in app_module.PAGE_TYPES
        assert "comparison" in app_module.PAGE_TYPES

    def test_warehouse_id(self, app_module):
        assert app_module.WAREHOUSE_ID == "41754a8563a43a49"


class TestValidateWriteForm:
    def test_valid_form(self, app_module):
        errors = app_module.validate_write_form("claims/fraud", "Title", "Summary", "Body text")
        assert errors == []

    def test_empty_path(self, app_module):
        errors = app_module.validate_write_form("", "Title", "Summary", "Body")
        assert any("Path" in e for e in errors)

    def test_path_without_slash(self, app_module):
        errors = app_module.validate_write_form("noslash", "Title", "Summary", "Body")
        assert any("slash" in e.lower() for e in errors)

    def test_empty_title(self, app_module):
        errors = app_module.validate_write_form("a/b", "", "Summary", "Body")
        assert any("Title" in e for e in errors)

    def test_empty_summary(self, app_module):
        errors = app_module.validate_write_form("a/b", "Title", "", "Body")
        assert any("Summary" in e for e in errors)

    def test_empty_body(self, app_module):
        errors = app_module.validate_write_form("a/b", "Title", "Summary", "")
        assert any("Body" in e for e in errors)

    def test_whitespace_only_path(self, app_module):
        errors = app_module.validate_write_form("   ", "Title", "Summary", "Body")
        assert len(errors) > 0

    def test_multiple_errors(self, app_module):
        errors = app_module.validate_write_form("", "", "", "")
        assert len(errors) == 4


class TestSearchWiki:
    def test_delegates_to_wiki_client(self, app_module):
        wiki = MagicMock()
        wiki.search.return_value = [{"path": "a/b", "title": "Test"}]
        result = app_module.search_wiki(wiki, "test query")
        assert len(result) == 1
        wiki.search.assert_called_once_with("test query", num_results=5)

    def test_custom_num_results(self, app_module):
        wiki = MagicMock()
        wiki.search.return_value = []
        app_module.search_wiki(wiki, "test", num_results=10)
        wiki.search.assert_called_once_with("test", num_results=10)

    def test_returns_empty_on_error(self, app_module):
        wiki = MagicMock()
        wiki.search.side_effect = Exception("connection failed")
        result = app_module.search_wiki(wiki, "test")
        assert result == []
