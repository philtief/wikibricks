# Curate Connect-Phase Parallelization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the wiki_curate connect phase from ~9 min to ~1-2 min by running `propose_edges` in a thread pool and batching the final `commit_edges` call into one MERGE.

**Architecture:** Add a pure helper `run_connect_phase` to `src/wikibricks/curate_logic.py` that takes injectable `propose_fn` / `commit_fn` callables, fans `propose_fn` out across a `ThreadPoolExecutor`, aggregates results in the main thread, and calls `commit_fn` exactly once with the union of high-confidence edges. The `wiki_curate` notebook becomes a thin caller that wires `wiki.propose_edges` and `wiki.commit_edges` into that helper. Library API unchanged; one new function added.

**Tech Stack:** Python 3.14, `concurrent.futures.ThreadPoolExecutor`, Databricks SDK (already thread-safe), pytest, ruff. Wheel ships from this repo via Databricks Asset Bundle.

---

## Pre-flight: Baseline state assumed

This plan starts from the post-#1+#2 state on the working tree:
- `src/wikibricks/client.py::propose_edges` already accepts `other_pages` kwarg.
- `notebooks/wiki_curate.py` connect SQL already filters `parent_id IS NULL` and `created_by NOT IN ('segregate', 'promote')`, and the loop already passes `other_pages=all_pages`.
- `tests/test_client.py` has 8 propose_edges tests.
- `tests/test_job_dag.py` curate-method-contract test already includes `list_pages`.
- 493 tests pass on baseline; lint clean.

If those are not already in place, stop and apply #1+#2 first.

---

## File structure

| File | Responsibility | Status |
|---|---|---|
| `src/wikibricks/curate_logic.py` | Pure curate-flow helpers, no SDK calls | **MODIFY** — add `run_connect_phase` |
| `tests/test_curate_logic.py` | Unit tests for the pure helpers | **MODIFY** — add `TestRunConnectPhase` (8 tests) |
| `notebooks/wiki_curate.py` | Workspace-side orchestrator | **MODIFY** — replace serial loop with `run_connect_phase` call; add `propose_concurrency` widget |
| `tests/test_job_dag.py` | Cross-task DAG contract test | **MODIFY** — assert `commit_edges` called ≤ 1 time |
| `pyproject.toml` | Library version | **MODIFY** — `0.3.0` → `0.3.1` |
| `plugin/.claude-plugin/plugin.json` | Plugin manifest version (test_plugin_manifest pins parity) | **MODIFY** — `0.3.0` → `0.3.1` |
| `notebooks/{wiki_curate,wiki_segregate,promote_from_traces,deploy_wiki_store,benchmark_hotpot,run_autoeval}.py` | Notebook %pip install lines | **MODIFY** — bump wheel filename to `wikibricks-0.3.1-py3-none-any.whl` |
| `CHANGELOG.md` | Release notes | **MODIFY** — add `## [0.3.1]` section |
| `README.md` | Test count + wheel filename | **MODIFY** — bump test count to 501, wheel to 0.3.1 |
| `scripts/bench_propose_edges_hoist.py` | Live benchmark | **MODIFY** — add `--concurrency` flag |
| `dist/wikibricks-0.3.1-py3-none-any.whl` | Build artifact | **CREATE** — `uv build` |

No new files. Total: 12 files modified, 1 build artifact produced.

---

## Task 1: Add `run_connect_phase` helper to `curate_logic.py`

**Files:**
- Modify: `src/wikibricks/curate_logic.py`
- Test: `tests/test_curate_logic.py`

- [ ] **Step 1.1: Write the failing tests**

Append to `tests/test_curate_logic.py`:

```python
import threading
import time
from unittest.mock import MagicMock

from wikibricks.curate_logic import run_connect_phase


class TestRunConnectPhase:
    def _commit_recorder(self):
        commits: list[list[dict]] = []
        def commit(edges):
            commits.append(list(edges))
            return len(edges)
        return commit, commits

    def test_propose_called_once_per_path(self):
        propose = MagicMock(return_value=[])
        commit, _ = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "b", "c"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert propose.call_count == 3
        called_paths = sorted(c.args[0] for c in propose.call_args_list)
        assert called_paths == ["a", "b", "c"]
        assert result["edges_proposed"] == 0

    def test_commit_called_exactly_once_with_aggregated_high_edges(self):
        def propose(path):
            return [{"source_page_id": path, "target_page_id": "t",
                     "link_type": "related", "confidence": 0.9, "origin": "auto-vs"}]
        commit, commits = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "b", "c"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert len(commits) == 1, "commit_fn must be called exactly once"
        assert len(commits[0]) == 3
        assert result["edges_committed"] == 3
        assert result["edges_proposed"] == 3
        assert result["deferred_low_confidence"] == []

    def test_commit_not_called_when_no_high_confidence_edges(self):
        def propose(path):
            return [{"source_page_id": path, "target_page_id": "t",
                     "link_type": "related", "confidence": 0.5, "origin": "auto-vs"}]
        commit, commits = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "b"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=2,
        )
        assert commits == []
        assert result["edges_committed"] == 0
        assert result["edges_proposed"] == 2
        assert len(result["deferred_low_confidence"]) == 2

    def test_deferred_edges_tagged_with_source_path(self):
        def propose(path):
            return [{"source_page_id": path, "target_page_id": f"t-{path}",
                     "link_type": "related", "confidence": 0.5, "origin": "auto-vs"}]
        commit, _ = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "b"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=2,
        )
        deferred_paths = sorted(d["path"] for d in result["deferred_low_confidence"])
        assert deferred_paths == ["a", "b"]

    def test_propose_exception_does_not_crash_and_counts_as_zero(self):
        def propose(path):
            if path == "bad":
                raise RuntimeError("boom")
            return [{"source_page_id": path, "target_page_id": "t",
                     "link_type": "related", "confidence": 0.9, "origin": "auto-vs"}]
        commit, commits = self._commit_recorder()
        result = run_connect_phase(
            paths=["a", "bad", "c"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert result["edges_proposed"] == 2  # bad page contributed nothing
        assert result["failed_paths"] == ["bad"]
        assert len(commits[0]) == 2

    def test_max_workers_one_runs_sequentially(self):
        propose = MagicMock(return_value=[])
        commit, _ = self._commit_recorder()
        run_connect_phase(
            paths=["a", "b"],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=1,
        )
        assert propose.call_count == 2

    def test_propose_runs_concurrently_when_max_workers_gt_one(self):
        # Barrier of size 4 only releases when 4 threads enter — proves
        # concurrency. With max_workers=1 the barrier would time out.
        barrier = threading.Barrier(4, timeout=2.0)
        seen_threads: set[int] = set()
        lock = threading.Lock()

        def propose(path):
            with lock:
                seen_threads.add(threading.get_ident())
            barrier.wait()
            return []

        commit, _ = self._commit_recorder()
        run_connect_phase(
            paths=[f"p{i}" for i in range(4)],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert len(seen_threads) >= 2, \
            f"expected concurrent execution, only saw {len(seen_threads)} thread(s)"

    def test_empty_paths_calls_nothing(self):
        propose = MagicMock(return_value=[])
        commit, commits = self._commit_recorder()
        result = run_connect_phase(
            paths=[],
            propose_fn=propose,
            commit_fn=commit,
            auto_commit_threshold=0.85,
            max_workers=4,
        )
        assert propose.call_count == 0
        assert commits == []
        assert result == {
            "edges_proposed": 0,
            "edges_committed": 0,
            "deferred_low_confidence": [],
            "failed_paths": [],
        }
```

- [ ] **Step 1.2: Run tests, verify they fail**

```bash
cd ~/code/wikibricks/dev && uv run pytest tests/test_curate_logic.py::TestRunConnectPhase -q 2>&1 | tail -10
```
Expected: 8 failures, all `ImportError: cannot import name 'run_connect_phase'`.

- [ ] **Step 1.3: Implement `run_connect_phase`**

Append to `src/wikibricks/curate_logic.py`:

```python
from concurrent.futures import ThreadPoolExecutor
from typing import Callable


def run_connect_phase(
    *,
    paths: list[str],
    propose_fn: Callable[[str], list[dict]],
    commit_fn: Callable[[list[dict]], int],
    auto_commit_threshold: float,
    max_workers: int = 8,
) -> dict:
    """Fan-out propose_fn across paths, batch one commit_fn call at the end.

    `propose_fn(path)` is called once per path, in parallel up to
    `max_workers`. High-confidence edges (>= `auto_commit_threshold`) from
    every path are aggregated and passed to `commit_fn` in one call so the
    underlying MERGE is a single Delta transaction (no concurrent-write
    contention on the links table).

    Per-path exceptions in `propose_fn` are swallowed: the path is recorded
    in `failed_paths` and contributes zero edges. The whole phase keeps
    running so one bad page doesn't block 99 good ones.

    Returns::

        {
          "edges_proposed":      int,         # sum of all proposed edges
          "edges_committed":     int,         # commit_fn return (0 if not called)
          "deferred_low_confidence": list[dict],  # each tagged with `path`
          "failed_paths":        list[str],
        }
    """
    if not paths:
        return {
            "edges_proposed": 0,
            "edges_committed": 0,
            "deferred_low_confidence": [],
            "failed_paths": [],
        }

    proposed_total = 0
    high_edges: list[dict] = []
    deferred: list[dict] = []
    failed: list[str] = []

    def _safe_propose(path: str) -> tuple[str, list[dict] | None]:
        try:
            return path, propose_fn(path)
        except Exception:
            return path, None

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        for path, edges in ex.map(_safe_propose, paths):
            if edges is None:
                failed.append(path)
                continue
            proposed_total += len(edges)
            high, low = partition_by_confidence(edges, auto_commit_threshold)
            high_edges.extend(high)
            deferred.extend({"path": path, **e} for e in low)

    committed_total = commit_fn(high_edges) if high_edges else 0

    return {
        "edges_proposed": proposed_total,
        "edges_committed": committed_total,
        "deferred_low_confidence": deferred,
        "failed_paths": failed,
    }
```

- [ ] **Step 1.4: Run tests, verify they pass**

```bash
cd ~/code/wikibricks/dev && uv run pytest tests/test_curate_logic.py::TestRunConnectPhase -q 2>&1 | tail -5
```
Expected: `8 passed`.

- [ ] **Step 1.5: Run full suite and lint**

```bash
cd ~/code/wikibricks/dev && uv run pytest -q 2>&1 | tail -3 && uv run ruff check src tests notebooks 2>&1 | tail -3
```
Expected: `501 passed` (493 baseline + 8 new), `All checks passed!`.

- [ ] **Step 1.6: Commit**

```bash
cd ~/code/wikibricks/dev && git add src/wikibricks/curate_logic.py tests/test_curate_logic.py && git commit -m "$(cat <<'EOF'
feat(curate): add run_connect_phase pure helper for parallel propose + batched commit

Pure function in curate_logic.py that fans propose_fn across paths via
ThreadPoolExecutor and batches one commit_fn call at the end. Read-only
propose work runs in parallel; the single MERGE INTO links avoids
concurrent-write contention. Per-path exceptions are recorded but don't
stop the phase.

Co-authored-by: Isaac
EOF
)"
```

---

## Task 2: Wire the notebook to use `run_connect_phase`

**Files:**
- Modify: `notebooks/wiki_curate.py`
- Modify: `tests/test_job_dag.py`

- [ ] **Step 2.1: Update the DAG contract test to assert single batched commit**

Replace the existing `test_curate_then_promote_succeed_with_spec_set_wiki` body in `tests/test_job_dag.py` so it asserts `commit_edges` is called at most once:

```python
    def test_curate_then_promote_succeed_with_spec_set_wiki(self):
        """Run both notebooks in DAG order against a spec_set'd WikiClient.

        If either notebook calls a method that doesn't exist on the real
        WikiClient (typo, removed method, renamed), spec_set raises
        AttributeError and this test fails. MagicMock without spec_set
        would silently accept any attribute.
        """
        wiki = _make_spec_wiki()
        ws = _make_ws()
        dbutils = _make_dbutils()

        _exec_notebook(CURATE_NB, _make_spark_curate(), ws, wiki, dbutils)
        _exec_notebook(PROMOTE_NB, _make_spark_promote(), ws, wiki, _make_dbutils())

        # Curate's two phases both ran: propose_edges may have been called
        # zero times (no recent pages) but fix_broken_links unconditionally
        # runs when REPAIR_BROKEN_LINKS is true (default).
        wiki.fix_broken_links.assert_called()
        # commit_edges is now called at most once per curate run (batched).
        # With zero recent paths → zero high edges → zero calls.
        assert wiki.commit_edges.call_count <= 1, (
            f"commit_edges called {wiki.commit_edges.call_count} times — "
            "connect phase should batch into a single MERGE"
        )
        # Curate logs a `curate_run` summary at the end.
        assert any(
            c.args and c.args[0] == "curate_run"
            for c in wiki._log.call_args_list
        )
```

- [ ] **Step 2.2: Run the DAG test, verify it still passes (no behavior change yet)**

```bash
cd ~/code/wikibricks/dev && uv run pytest tests/test_job_dag.py -q 2>&1 | tail -5
```
Expected: pass (the spec_wiki returns `[]` so commit_edges is called 0 or 1 times either way).

- [ ] **Step 2.3: Refactor `notebooks/wiki_curate.py` connect phase**

Replace the import block — change this:

```python
from wikibricks.curate_logic import (
    build_curate_summary,
    build_health_summary,
    classify_page_health,
    find_duplicate_paths,
    partition_by_confidence,
)
```

to:

```python
from wikibricks.curate_logic import (
    build_curate_summary,
    build_health_summary,
    classify_page_health,
    find_duplicate_paths,
    run_connect_phase,
)
```

Add a new widget read after the existing widget reads (around line 80):

```python
PROPOSE_CONCURRENCY = int(_param("propose_concurrency", "8"))
```

Replace the existing connect-phase loop (the block from `committed_total = 0` through `print(f"connect: proposed=...")`):

```python
def _propose_one(path: str) -> list[dict]:
    return wiki.propose_edges(
        path, min_similarity=MIN_SIMILARITY, other_pages=all_pages,
    )


connect = run_connect_phase(
    paths=paths,
    propose_fn=_propose_one,
    commit_fn=wiki.commit_edges,
    auto_commit_threshold=AUTO_COMMIT_THRESHOLD,
    max_workers=PROPOSE_CONCURRENCY,
)
proposed_total = connect["edges_proposed"]
committed_total = connect["edges_committed"]
deferred_low_confidence = connect["deferred_low_confidence"]

if connect["failed_paths"]:
    print(f"connect: {len(connect['failed_paths'])} paths failed: "
          f"{connect['failed_paths'][:5]}{'...' if len(connect['failed_paths']) > 5 else ''}")
print(f"connect: proposed={proposed_total} committed={committed_total} "
      f"deferred_for_agent={len(deferred_low_confidence)} "
      f"workers={PROPOSE_CONCURRENCY}")
```

Also update the bundle resource so the new widget has a default in scheduled runs. In `resources/wiki_curate_job.yml`, find the `curate` task's `base_parameters:` and add `propose_concurrency: "8"` (look for the existing siblings like `auto_commit_threshold`).

- [ ] **Step 2.4: Verify notebook is still valid Python**

```bash
cd ~/code/wikibricks/dev && uv run python -c "import ast; ast.parse(open('notebooks/wiki_curate.py').read())" && echo OK
```
Expected: `OK`.

- [ ] **Step 2.5: Run job-DAG and deploy-notebook tests**

```bash
cd ~/code/wikibricks/dev && uv run pytest tests/test_job_dag.py tests/test_deploy_notebook.py -q 2>&1 | tail -5
```
Expected: all pass. The deploy-notebook test (`test_runs_connect_phase`) just greps for `propose_edges` and `commit_edges` strings, which are still present (passed as callables).

- [ ] **Step 2.6: Run full suite and lint**

```bash
cd ~/code/wikibricks/dev && uv run pytest -q 2>&1 | tail -3 && uv run ruff check src tests notebooks 2>&1 | tail -3
```
Expected: `501 passed`, `All checks passed!`.

- [ ] **Step 2.7: Commit**

```bash
cd ~/code/wikibricks/dev && git add notebooks/wiki_curate.py resources/wiki_curate_job.yml tests/test_job_dag.py && git commit -m "$(cat <<'EOF'
feat(curate-notebook): use run_connect_phase for parallel propose + batched commit

Replaces the serial per-page propose/commit loop with run_connect_phase.
Adds a propose_concurrency widget (default 8) and a single MERGE at the
end. Connect-phase wall time projected to drop from ~9 min to ~1-2 min
on the personal philipp wiki.

Co-authored-by: Isaac
EOF
)"
```

---

## Task 3: Bump library version to 0.3.1

**Files:**
- Modify: `pyproject.toml`
- Modify: `plugin/.claude-plugin/plugin.json`
- Modify: `notebooks/wiki_curate.py`, `notebooks/wiki_segregate.py`, `notebooks/promote_from_traces.py`, `notebooks/deploy_wiki_store.py`, `notebooks/benchmark_hotpot.py`, `notebooks/run_autoeval.py`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 3.1: Bump `pyproject.toml`**

In `pyproject.toml`, change `version = "0.3.0"` to `version = "0.3.1"`.

- [ ] **Step 3.2: Bump plugin manifest**

In `plugin/.claude-plugin/plugin.json`, change `"version": "0.3.0"` to `"version": "0.3.1"`.

- [ ] **Step 3.3: Bump every notebook's %pip line**

```bash
cd ~/code/wikibricks/dev && grep -rln "wikibricks-0\.3\.0-py3-none-any\.whl" notebooks/
```

For each file listed, replace `wikibricks-0.3.0-py3-none-any.whl` with `wikibricks-0.3.1-py3-none-any.whl`. Verify all six are bumped:

```bash
cd ~/code/wikibricks/dev && grep -c "wikibricks-0.3.1-py3-none-any.whl" notebooks/*.py
```
Expected: 6 lines, one each in deploy_wiki_store, wiki_curate, wiki_segregate, promote_from_traces, benchmark_hotpot, run_autoeval.

- [ ] **Step 3.4: Add CHANGELOG section**

In `CHANGELOG.md`, insert a new section under `## [Unreleased]` (or above the existing top release section if there's no Unreleased):

```markdown
## [0.3.1] - 2026-05-05

### Added
- `wikibricks.curate_logic.run_connect_phase` — pure helper that fans `propose_fn` across paths via `ThreadPoolExecutor` and batches one `commit_fn` call. Used by `notebooks/wiki_curate.py`.
- `wiki_curate` notebook gains a `propose_concurrency` widget (default 8).

### Changed
- `notebooks/wiki_curate.py` connect phase now runs propose_edges in parallel and commits all high-confidence edges in a single MERGE. On the personal philipp wiki this drops the connect phase from ~9 min to ~1-2 min.
```

Update the `[Unreleased]` compare link at the bottom of CHANGELOG.md to point to `v0.3.1...HEAD`, and add a new `[0.3.1]: …compare/v0.3.0...v0.3.1` line.

- [ ] **Step 3.5: Bump README test count + wheel filename**

In `README.md`, find the Development section. Change references to `493 tests` (or whatever the prior number was — grep first) to `501 tests`, and `wikibricks-0.3.0-py3-none-any.whl` to `wikibricks-0.3.1-py3-none-any.whl`.

```bash
cd ~/code/wikibricks/dev && grep -n "wikibricks-0\.3\.0\|493 tests\| tests passing" README.md
```

- [ ] **Step 3.6: Run plugin-manifest test, then full suite**

```bash
cd ~/code/wikibricks/dev && uv run pytest tests/test_plugin_manifest.py -q 2>&1 | tail -5 \
  && uv run pytest -q 2>&1 | tail -3
```
Expected: all pass. The manifest test asserts plugin.json version == pyproject version.

- [ ] **Step 3.7: Build the wheel**

```bash
cd ~/code/wikibricks/dev && uv build 2>&1 | tail -5 && ls -l dist/wikibricks-0.3.1*
```
Expected: `Successfully built dist/wikibricks-0.3.1.tar.gz` and `dist/wikibricks-0.3.1-py3-none-any.whl`.

- [ ] **Step 3.8: Commit**

```bash
cd ~/code/wikibricks/dev && git add pyproject.toml plugin/.claude-plugin/plugin.json notebooks/*.py CHANGELOG.md README.md && git commit -m "$(cat <<'EOF'
chore(release): bump to 0.3.1 — parallel curate connect phase

Changelog:
- run_connect_phase pure helper, propose_concurrency widget
- single batched commit_edges per run instead of per-page
EOF
)"
```

---

## Task 4: Live validation against fevm-agent-marketplace

**Files:**
- Modify: `scripts/bench_propose_edges_hoist.py`

- [ ] **Step 4.1: Extend the benchmark script with a concurrency flag**

Replace the body of `scripts/bench_propose_edges_hoist.py` so it benchmarks BEFORE-style serial vs AFTER-style parallel batched. Append a new section after the existing AFTER block:

```python
    # --- AFTER+CONCURRENT: list_pages hoisted + parallel propose_edges ---
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.monotonic()
    all_pages = wiki.list_pages()
    def _one(p):
        return wiki.propose_edges(p, min_similarity=0.7, other_pages=all_pages)
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        list(ex.map(_one, paths))
    after_par = time.monotonic() - t0
    per_after_par = after_par / len(paths)
    print(f"AFTER+CONCURRENT (workers={args.concurrency}): "
          f"{after_par:6.2f}s total  {per_after_par:5.2f}s/page")
    if per_after > 0:
        print(f"speedup vs AFTER (serial):       {per_after / per_after_par:.1f}x")
```

And add to the argparser:

```python
    p.add_argument("--concurrency", type=int, default=8)
```

- [ ] **Step 4.2: Run the benchmark**

```bash
cd ~/code/wikibricks/dev && uv run python scripts/bench_propose_edges_hoist.py \
  --profile fe-vm-agent-marketplace \
  --warehouse-id 41754a8563a43a49 \
  --catalog agent_marketplace_catalog \
  --schema wikibricks_personal_philipp \
  --n 10 --concurrency 8 2>&1 | tail -10
```
Expected: AFTER+CONCURRENT total < AFTER (serial) total. Speedup ≥ 3× per-page on 8 workers (warehouse may serialize internally — 3-5× is realistic, not 8×).

- [ ] **Step 4.3: Upload the new wheel to the workspace Volume**

The deploy bundle keeps the wheel path symbolic; the actual wheel must be uploaded to the configured Volume. Find the Volume path the bundle uses:

```bash
cd ~/code/wikibricks/dev && grep -A2 "wheels" databricks.override.yml 2>&1 | head -10
```

Upload (replace `<VOL>` with the path printed above):

```bash
databricks fs cp dist/wikibricks-0.3.1-py3-none-any.whl \
  dbfs:<VOL>/wikibricks-0.3.1-py3-none-any.whl \
  --profile fe-vm-agent-marketplace --overwrite
```

- [ ] **Step 4.4: Redeploy the bundle so the notebook %pip lines pick up 0.3.1**

```bash
cd ~/code/wikibricks/dev && databricks bundle deploy --target dev 2>&1 | tail -10
```
Expected: bundle deploys; no `wheel not found` error.

- [ ] **Step 4.5: Trigger the curate task manually and time it**

```bash
databricks jobs run-now --job-id 1100963613497242 \
  --profile fe-vm-agent-marketplace 2>&1 | tee /tmp/curate_run.json
RUN_ID=$(jq -r '.run_id' /tmp/curate_run.json)
echo "Run ID: $RUN_ID — watch at https://fevm-agent-marketplace.cloud.databricks.com/?o=7474653189849615#job/1100963613497242/run/$RUN_ID"
```

After it finishes, fetch durations:

```bash
databricks jobs get-run "$RUN_ID" --profile fe-vm-agent-marketplace 2>&1 | python3 -c "
import json, sys
d = json.load(sys.stdin)
for t in d.get('tasks', []):
    s, e = t.get('start_time',0), t.get('end_time',0)
    dur = (e-s)/60000 if s and e else 0
    print(f\"  {t.get('task_key'):20s}  state={t.get('state',{}).get('result_state','?'):10s}  duration={dur:.1f}min\")
"
```
Expected: `curate` task duration < 5 min (was 21.6 min on the 2026-05-05 run we measured). `segregate` and `promote` unchanged.

- [ ] **Step 4.6: Sanity-check the link table grew (or didn't shrink)**

```bash
databricks api post /api/2.0/sql/statements --profile fe-vm-agent-marketplace --json '{
  "warehouse_id":"41754a8563a43a49",
  "statement":"SELECT COUNT(*) AS n FROM agent_marketplace_catalog.wikibricks_personal_philipp.links",
  "wait_timeout":"30s"
}' 2>&1 | python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d.get('result',{}).get('data_array',[]) or []: print('links rows:', r[0])
"
```
Expected: a positive integer; not zero. Compare against the pre-run count if you captured one before triggering. (No commit_edges call would print `committed=0` in the notebook output too.)

- [ ] **Step 4.7: Update the now.md work log entry**

Edit `~/.remember/now.md` to append a line:

```
## HH:MM | wikibricks 0.3.1
Parallel curate connect (run_connect_phase, ThreadPoolExecutor, batched commit). Curate task: 21.6→<measured> min on fevm-agent-marketplace.
```

(No commit needed for `~/.remember/`.)

- [ ] **Step 4.8: Commit the bench-script changes**

```bash
cd ~/code/wikibricks/dev && git add scripts/bench_propose_edges_hoist.py && git commit -m "$(cat <<'EOF'
test(bench): add concurrency comparison to bench_propose_edges_hoist

Co-authored-by: Isaac
EOF
)"
```

---

## Done criteria

All of these must hold before the plan is complete:

1. `uv run pytest -q` reports `501 passed`.
2. `uv run ruff check src tests notebooks scripts` reports `All checks passed!`.
3. `uv build` produces `dist/wikibricks-0.3.1-py3-none-any.whl`.
4. Live curate task on fevm-agent-marketplace finishes in < 5 min (down from 21.6 min).
5. `links` table row count after the run is ≥ pre-run count (no edges silently dropped).
6. Three commits on the working branch: `feat(curate)`, `feat(curate-notebook)`, `chore(release): 0.3.1`, plus optional bench-script commit.

## Rollback

If the live curate run fails or behaves unexpectedly:

1. Set the notebook widget `propose_concurrency=1` in the next run to force serial execution. The new `run_connect_phase` still runs sequentially with `max_workers=1` and is identical to the prior loop semantically.
2. If the function itself misbehaves, revert the three commits: `git revert <sha-of-release-bump> <sha-of-notebook-refactor> <sha-of-helper>`. The wheel can stay at 0.3.1 — old code paths are unchanged.
3. The `links` table is append-only via MERGE; no destructive writes happened.
