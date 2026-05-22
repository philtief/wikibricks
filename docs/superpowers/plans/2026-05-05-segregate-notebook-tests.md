# Segregate Notebook Test Coverage Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the test-surface gap on `notebooks/wiki_segregate.py` so a refactor of `WikiClient` cannot silently break the nightly segregate task.

**Architecture:** Add a `TestWikiSegregateNotebook` class to `tests/test_deploy_notebook.py` (string-grep checks like the existing `TestWikiCurateNotebook`). Extend `tests/test_job_dag.py` to exec the segregate notebook against the same `spec_set=WikiClient` mock used by curate/promote, plus a static method-contract guard listing the WikiClient methods segregate calls. Both are pure-Python unit tests — no workspace, no SDK, no LLM.

**Tech Stack:** Python 3.14, pytest, `unittest.mock.MagicMock(spec_set=WikiClient)`. The segregate notebook exits via `dbutils.notebook.exit("no oversize pages")` when no rows match; the test rides that early-exit path so no LLM mock is needed.

---

## Pre-flight

Baseline: 501 tests pass on `main` after fix #3. `notebooks/wiki_segregate.py` is unchanged on this branch — exists at version 0.3.1 wheel reference.

Methods segregate calls on `WikiClient`: `write_pages`, `sync_index`, `_log`.
Other surface: `dbutils.widgets.get/text`, `dbutils.notebook.exit`, `w.serving_endpoints.query`, `w.statement_execution.execute_statement`.

---

## File structure

| File | Responsibility | Status |
|---|---|---|
| `tests/test_deploy_notebook.py` | Static notebook-syntax + grep tests | **MODIFY** — add `TestWikiSegregateNotebook` (7 tests) |
| `tests/test_job_dag.py` | Cross-task DAG contract (spec_set'd wiki, real notebook exec) | **MODIFY** — add segregate to the DAG run + a method-contract guard |

No new files. No source code changes.

---

## Task 1: Add `TestWikiSegregateNotebook` to `test_deploy_notebook.py`

**Files:**
- Modify: `tests/test_deploy_notebook.py`

- [ ] **Step 1.1: Write the failing tests**

Append to `tests/test_deploy_notebook.py`:

```python
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
```

- [ ] **Step 1.2: Run tests, verify they pass**

```bash
cd ~/code/wikibricks/dev && uv run pytest tests/test_deploy_notebook.py::TestWikiSegregateNotebook -q 2>&1 | tail -5
```
Expected: `8 passed`. (These are static checks against a notebook that already satisfies them — they pass on first write because their job is to *guard* the current state, not introduce new behavior. This is the intended TDD shape for "drift detector" tests.)

- [ ] **Step 1.3: Confirm by mutating the notebook locally to verify the guards bite**

Pick one assertion to prove the test would catch a regression. Run:

```bash
cd ~/code/wikibricks/dev && cp notebooks/wiki_segregate.py /tmp/wiki_segregate.bak \
  && python3 -c "
src = open('notebooks/wiki_segregate.py').read()
# break the parent_id filter — would re-segregate already-segregated chunks
broken = src.replace('AND parent_id IS NULL', '')
open('notebooks/wiki_segregate.py', 'w').write(broken)
" \
  && uv run pytest tests/test_deploy_notebook.py::TestWikiSegregateNotebook::test_filters_to_oversize_parents_only -q 2>&1 | tail -5; \
  cp /tmp/wiki_segregate.bak notebooks/wiki_segregate.py
```
Expected: 1 failure (`assert 'parent_id IS NULL' in source`), then the file is restored. Re-run the test class and confirm green again.

- [ ] **Step 1.4: Run full suite + lint**

```bash
cd ~/code/wikibricks/dev && uv run pytest -q 2>&1 | tail -3 && uv run ruff check src tests notebooks 2>&1 | tail -3
```
Expected: `509 passed` (501 baseline + 8 new), `All checks passed!`.

- [ ] **Step 1.5: Commit**

```bash
cd ~/code/wikibricks/dev && git add tests/test_deploy_notebook.py && git commit -m "$(cat <<'EOF'
test(segregate-notebook): add TestWikiSegregateNotebook drift guards

Eight static checks: parses-as-Python, imports wikibricks +
segregate_logic helpers, filters to oversize parents only,
exits early when nothing matches, writes via batched write_pages
(not per-page write_page), syncs index, uses WikiClient. Catches
silent breakage of the nightly segregate task from a future
WikiClient API rename or notebook refactor.

Co-authored-by: Isaac
EOF
)"
```

---

## Task 2: Add segregate to the DAG contract test

**Files:**
- Modify: `tests/test_job_dag.py`

- [ ] **Step 2.1: Inspect the existing fixtures**

```bash
cd ~/code/wikibricks/dev && grep -n "_make_spec_wiki\|_exec_notebook\|_make_ws\|_make_dbutils\|class TestJobDag" tests/test_job_dag.py
```
Expected: prints function names and class start. The existing `_make_spec_wiki()` already returns a mock with `write_pages`, `sync_index`, `_log` configured (verify by reading the function body). If any of those three are missing, add them to `_make_spec_wiki` first as part of Step 2.4.

- [ ] **Step 2.2: Add a segregate-spark fixture and extend the DAG test**

Insert this helper above `class TestJobDagSchemaContract` in `tests/test_job_dag.py`:

```python
SEGREGATE_NB = REPO_ROOT / "notebooks" / "wiki_segregate.py"


def _make_spark_segregate() -> MagicMock:
    """Segregate-side spark: empty oversize result → notebook exits early.

    The notebook calls dbutils.notebook.exit("no oversize pages") when
    no rows match. That early-exit path still proves the import block,
    parameter parsing, and the initial SQL all work against the
    spec_set'd wiki — without needing to mock the LLM serving endpoint.
    """
    return _make_spark_curate()  # same shape: returns empty .collect() everywhere


def _make_ws_segregate() -> MagicMock:
    """Workspace client for segregate. Statements API returns no rows
    so `oversize` is empty and the notebook exits via dbutils.notebook.exit.
    """
    ws = _make_ws()
    # Defensive: if the notebook ever queries serving_endpoints, return a
    # stub so `.choices[0].message.content` is at least navigable.
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = '{"summary": "x", "titles": []}'
    ws.serving_endpoints.query.return_value = resp
    return ws
```

Then extend the existing test method to also exec segregate. Replace:

```python
        _exec_notebook(CURATE_NB, _make_spark_curate(), ws, wiki, dbutils)
        _exec_notebook(PROMOTE_NB, _make_spark_promote(), ws, wiki, _make_dbutils())
```

with:

```python
        _exec_notebook(CURATE_NB, _make_spark_curate(), ws, wiki, dbutils)
        _exec_notebook(
            SEGREGATE_NB, _make_spark_segregate(),
            _make_ws_segregate(), wiki, _make_dbutils(),
        )
        _exec_notebook(PROMOTE_NB, _make_spark_promote(), ws, wiki, _make_dbutils())
```

- [ ] **Step 2.3: Add a method-contract guard test for segregate**

Append to `class TestJobDagSchemaContract` (after `test_all_wiki_methods_used_by_curate_exist_on_class`):

```python
    def test_all_wiki_methods_used_by_segregate_exist_on_class(self):
        """Same guard as curate/promote, for the segregate notebook."""
        expected = {"write_pages", "sync_index", "_log"}
        missing = expected - set(dir(WikiClient))
        assert not missing, (
            f"segregate uses methods missing from WikiClient: {missing}"
        )
```

- [ ] **Step 2.4: Verify spec_wiki has the methods segregate needs**

```bash
cd ~/code/wikibricks/dev && grep -n "write_pages\|sync_index" tests/test_job_dag.py | head -5
```
Expected: `_make_spec_wiki` already wires `write_pages` and `sync_index` (and `_log`). If any are missing — add them with `wiki.<method>.return_value = ...` matching what segregate expects (`write_pages` returns nothing meaningful; `sync_index` returns `None`; `_log` is a MagicMock attr on the class).

If `write_pages` is missing from the existing fixture, add this line near the existing `wiki.write_page.return_value = "written"`:

```python
    wiki.write_pages.return_value = None
```

- [ ] **Step 2.5: Run the DAG test**

```bash
cd ~/code/wikibricks/dev && uv run pytest tests/test_job_dag.py -v 2>&1 | tail -10
```
Expected: 4 tests pass (was 3 — added one). The DAG run test now executes three notebooks in order: curate → segregate → promote, all against the same `spec_set=WikiClient` mock.

- [ ] **Step 2.6: Run full suite + lint**

```bash
cd ~/code/wikibricks/dev && uv run pytest -q 2>&1 | tail -3 && uv run ruff check src tests notebooks 2>&1 | tail -3
```
Expected: `510 passed` (509 from Task 1 + 1 from this task), `All checks passed!`.

- [ ] **Step 2.7: Commit**

```bash
cd ~/code/wikibricks/dev && git add tests/test_job_dag.py && git commit -m "$(cat <<'EOF'
test(job-dag): exec segregate notebook + assert method contract

Extends the cross-task DAG schema-contract test to also exec
notebooks/wiki_segregate.py against the spec_set'd WikiClient.
Adds test_all_wiki_methods_used_by_segregate_exist_on_class
listing the three methods segregate calls (write_pages,
sync_index, _log) so a future API rename fails the suite
instead of breaking the nightly job silently.

The segregate run rides the "no oversize pages" early-exit
path, so no LLM mock is needed — the test still proves
imports, parameter parsing, and the initial SQL all work
against the contract'd mock.

Co-authored-by: Isaac
EOF
)"
```

---

## Done criteria

1. `uv run pytest -q` reports `510 passed`.
2. `uv run ruff check src tests notebooks` reports `All checks passed!`.
3. Two commits on the working branch: `test(segregate-notebook)` and `test(job-dag)`.
4. The mutation check in Step 1.3 demonstrated that one of the new guards bites a regression and the file was restored.

## Rollback

These are pure test additions — no source changes. Rollback is `git revert <sha-of-task-2> <sha-of-task-1>`. The existing test suite + production code are unaffected.
