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


class TestWikiSegregateNotebook:
    def test_notebook_is_valid_python(self):
        with open("notebooks/wiki_segregate.py") as f:
            source = f.read()
        ast.parse(source)

    def test_imports_wikibricks(self):
        with open("notebooks/wiki_segregate.py") as f:
            source = f.read()
        assert "from wikibricks" in source

    def test_imports_segregate_logic_helpers(self):
        with open("notebooks/wiki_segregate.py") as f:
            source = f.read()
        for helper in ("build_parent_body", "child_path",
                       "child_title", "chunk_at_boundaries"):
            assert helper in source, f"missing import of segregate_logic.{helper}"

    def test_filters_to_oversize_parents_only(self):
        # Segregate must only pick `health_status='oversize' AND parent_id IS NULL`
        # so it doesn't recursively split chunk children. If this filter drifts,
        # one big oversize parent → infinite re-segregation across runs.
        with open("notebooks/wiki_segregate.py") as f:
            source = f.read()
        assert "health_status = 'oversize'" in source
        assert "parent_id IS NULL" in source

    def test_exits_early_when_no_oversize_pages(self):
        with open("notebooks/wiki_segregate.py") as f:
            source = f.read()
        assert 'dbutils.notebook.exit("no oversize pages")' in source

    def test_writes_via_batched_write_pages(self):
        # write_pages (plural) collapses N+1 sequential writes into 4 SQL
        # statements per page. Reverting to write_page (singular) inside
        # the loop would re-introduce the perf regression CHANGELOG 0.3.0
        # explicitly fixed.
        with open("notebooks/wiki_segregate.py") as f:
            source = f.read()
        assert "wiki.write_pages(" in source
        assert "wiki.write_page(" not in source, (
            "segregate must use batched write_pages, not per-page write_page"
        )

    def test_syncs_index_after_segregation(self):
        with open("notebooks/wiki_segregate.py") as f:
            source = f.read()
        assert "wiki.sync_index()" in source

    def test_uses_wiki_client(self):
        with open("notebooks/wiki_segregate.py") as f:
            source = f.read()
        assert "WikiClient" in source
