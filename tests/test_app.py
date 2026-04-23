"""Tests for WikiBricks app logic."""

import ast
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

APP_DIR = Path(__file__).parent.parent / "app"

# Env vars the app requires at import time (fails fast via `st.stop()` if unset,
# which is the documented behavior for a production deploy). Tests stub both.
_TEST_WAREHOUSE_ID = "test-warehouse"
_TEST_VS_INDEX = "test_catalog.test_schema.pages_index"


@pytest.fixture(autouse=True)
def _app_env(monkeypatch):
    monkeypatch.setenv("WIKIBRICKS_WAREHOUSE_ID", _TEST_WAREHOUSE_ID)
    monkeypatch.setenv("WIKIBRICKS_VS_INDEX", _TEST_VS_INDEX)


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
            {
                "title": "Example Topic", "path": "topics/example",
                "content_text": "Some content", "page_type": "concept",
            },
        ]
        result = app_module.build_context(pages)
        assert "Example Topic" in result
        assert "topics/example" in result
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
        assert app_module.VS_INDEX == _TEST_VS_INDEX

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
        assert app_module.WAREHOUSE_ID == _TEST_WAREHOUSE_ID

    def test_missing_required_env_vars_stops_app(self):
        """App must fail fast when required env is missing; silent defaults
        would point at the wrong workspace.
        """
        for var in ("WIKIBRICKS_WAREHOUSE_ID", "WIKIBRICKS_VS_INDEX"):
            os.environ.pop(var, None)

        mock_st = MagicMock()
        mock_st.session_state = _SessionState()
        mock_st.stop.side_effect = SystemExit

        with patch.dict(sys.modules, {"streamlit": mock_st,
                                      "openai": MagicMock(),
                                      "wikibricks": MagicMock()}):
            spec = importlib.util.spec_from_file_location("app_missing_env",
                                                          APP_DIR / "app.py")
            mod = importlib.util.module_from_spec(spec)
            with pytest.raises(SystemExit):
                spec.loader.exec_module(mod)
        mock_st.error.assert_called_once()


class TestValidateWriteForm:
    def test_valid_form(self, app_module):
        errors = app_module.validate_write_form("topics/example", "Title", "Summary", "Body text")
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


class TestBrowseMode:
    """End-to-end AppTest drive of Browse Mode.

    Guards the Streamlit session_state round-trip fixed in #38/#39: tree-button
    click → queued_read_path → rerun → text_input pre-fill → read_page call.
    """

    @staticmethod
    def _mocks():
        ws = MagicMock()
        ws.config.authenticate.return_value = {"Authorization": "Bearer test"}
        ws.config.host = "https://test"

        wiki = MagicMock()
        wiki.list_pages.return_value = [
            {"path": "promoted/foo", "title": "Foo", "page_type": "synthesis"},
            {"path": "topics/bar", "title": "Bar", "page_type": "concept"},
        ]
        wiki.search.return_value = [{
            "path": "promoted/foo", "title": "Foo",
            "page_type": "synthesis", "version": 1,
            "content_text": "Foo full text.",
        }]
        wiki.read_page.return_value = {
            "path": "promoted/foo", "title": "Foo",
            "page_type": "synthesis", "version": 1,
            "content_text": "Foo full text.",
        }
        wiki.history.return_value = [{
            "version": 1, "created_by": "x",
            "created_at": "t", "summary": "s",
        }]
        return ws, wiki

    @staticmethod
    def _open_browse():
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(APP_DIR / "app.py"))
        at.run(timeout=10)
        at.sidebar.radio[0].set_value("Browse").run(timeout=10)
        return at

    def test_browse_title_and_list_pages_called(self):
        ws, wiki = self._mocks()
        with patch("databricks.sdk.WorkspaceClient", return_value=ws), \
             patch("wikibricks.WikiClient", return_value=wiki), \
             patch("openai.OpenAI"):
            at = self._open_browse()
            assert any(t.value == "Browse Wiki" for t in at.title)
            assert wiki.list_pages.called

    def test_tree_button_click_loads_page(self):
        ws, wiki = self._mocks()
        with patch("databricks.sdk.WorkspaceClient", return_value=ws), \
             patch("wikibricks.WikiClient", return_value=wiki), \
             patch("openai.OpenAI"):
            at = self._open_browse()
            at.button(key="tree_promoted/foo").click().run(timeout=10)
            wiki.read_page.assert_called_with("promoted/foo")
            assert at.session_state["read_path"] == "promoted/foo"

    def test_read_page_by_path_loads_and_shows_history(self):
        ws, wiki = self._mocks()
        with patch("databricks.sdk.WorkspaceClient", return_value=ws), \
             patch("wikibricks.WikiClient", return_value=wiki), \
             patch("openai.OpenAI"):
            at = self._open_browse()
            at.text_input(key="read_path").set_value("promoted/foo")
            # Click the "Read" button (first non-tree button without key).
            read_btn = next(b for b in at.button if b.label == "Read")
            read_btn.click().run(timeout=10)
            wiki.read_page.assert_called_with("promoted/foo")
            wiki.history.assert_called_with("promoted/foo")

    def test_search_returns_results_and_open_button(self):
        ws, wiki = self._mocks()
        with patch("databricks.sdk.WorkspaceClient", return_value=ws), \
             patch("wikibricks.WikiClient", return_value=wiki), \
             patch("openai.OpenAI"):
            at = self._open_browse()
            next(t for t in at.text_input if t.label == "Search pages").set_value("foo")
            search_btn = next(b for b in at.button if b.label == "Search")
            search_btn.click().run(timeout=10)
            wiki.search.assert_called_with("foo", num_results=5)
            assert any(b.key == "open_promoted/foo" for b in at.button)

    def test_open_full_page_button_queues_read(self):
        ws, wiki = self._mocks()
        with patch("databricks.sdk.WorkspaceClient", return_value=ws), \
             patch("wikibricks.WikiClient", return_value=wiki), \
             patch("openai.OpenAI"):
            at = self._open_browse()
            next(t for t in at.text_input if t.label == "Search pages").set_value("foo")
            next(b for b in at.button if b.label == "Search").click().run(timeout=10)
            at.button(key="open_promoted/foo").click().run(timeout=10)
            wiki.read_page.assert_called_with("promoted/foo")

    def test_list_pages_error_shown_gracefully(self):
        ws, wiki = self._mocks()
        wiki.list_pages.side_effect = Exception("warehouse down")
        with patch("databricks.sdk.WorkspaceClient", return_value=ws), \
             patch("wikibricks.WikiClient", return_value=wiki), \
             patch("openai.OpenAI"):
            at = self._open_browse()
            assert any("warehouse down" in (e.value or "") for e in at.error)
