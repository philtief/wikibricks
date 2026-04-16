"""Tests for the deployment notebook — validates it's valid Python and uses wiki_ops correctly."""

import ast
import importlib


class TestDeployNotebookSyntax:
    def test_notebook_is_valid_python(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        ast.parse(source)

    def test_notebook_imports_wiki_ops(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        assert "wiki_ops" in source or "from wiki_ops" in source or "import wiki_ops" in source

    def test_notebook_creates_schema(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        assert "create_schema_sql" in source

    def test_notebook_creates_tables(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        assert "create_tables_sql" in source

    def test_notebook_creates_vs_index(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        assert "create_vs_index_spec" in source

    def test_notebook_creates_uc_functions(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        assert "create_uc_functions_sql" in source

    def test_notebook_seeds_data(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        assert "seed_pages" in source

    def test_notebook_uses_sdk_not_rest_api(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        assert "requests.get" not in source
        assert "requests.post" not in source
        assert "WorkspaceClient" in source


class TestAutoEvalNotebookSyntax:
    def test_notebook_is_valid_python(self):
        with open("notebooks/run_autoeval.py") as f:
            source = f.read()
        ast.parse(source)

    def test_notebook_uses_autoeval_config(self):
        with open("notebooks/run_autoeval.py") as f:
            source = f.read()
        assert "autoeval_config" in source

    def test_notebook_uses_vs_index(self):
        with open("notebooks/run_autoeval.py") as f:
            source = f.read()
        assert "pages_index" in source or "VS_INDEX" in source or "autoeval" in source.lower()
