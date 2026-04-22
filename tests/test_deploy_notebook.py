"""Tests for the deployment notebook - validates it's valid Python and uses wiki_ops correctly."""

import ast


class TestDeployNotebookSyntax:
    def test_notebook_is_valid_python(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        ast.parse(source)

    def test_notebook_imports_wikibricks_ops(self):
        with open("notebooks/deploy_wiki_store.py") as f:
            source = f.read()
        assert "from wikibricks.ops import" in source

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

    def test_notebook_uses_eval_queries(self):
        with open("notebooks/run_autoeval.py") as f:
            source = f.read()
        assert "eval_queries" in source

    def test_notebook_uses_vs_index(self):
        with open("notebooks/run_autoeval.py") as f:
            source = f.read()
        assert "pages_index" in source or "VS_INDEX" in source or "autoeval" in source.lower()


class TestWikiCurateNotebook:
    def test_notebook_is_valid_python(self):
        with open("notebooks/wiki_curate.py") as f:
            source = f.read()
        ast.parse(source)

    def test_imports_wikibricks(self):
        with open("notebooks/wiki_curate.py") as f:
            source = f.read()
        assert "from wikibricks" in source

    def test_runs_connect_phase(self):
        with open("notebooks/wiki_curate.py") as f:
            source = f.read()
        assert "propose_edges" in source
        assert "commit_edges" in source

    def test_runs_lint_phase(self):
        with open("notebooks/wiki_curate.py") as f:
            source = f.read()
        for check in ("orphan_pages_sql", "stale_pages_sql",
                      "duplicate_paths_sql", "broken_links_sql"):
            assert check in source, f"missing {check}"

    def test_optional_repair(self):
        with open("notebooks/wiki_curate.py") as f:
            source = f.read()
        assert "fix_broken_links" in source

    def test_uses_wiki_client(self):
        with open("notebooks/wiki_curate.py") as f:
            source = f.read()
        assert "WikiClient" in source
