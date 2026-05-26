# Graph Interface (v0.7.12) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Streamlit `wikibricks-app` with a Databricks App built on the APX scaffold (FastAPI backend + React frontend) that visualizes the wiki as an interactive force-directed graph. Three view modes: constellation (force-directed, all visible pages), ego (focus + 2-hop neighbors via URL deep-link), and proposed-edges queue (review LLM-staged edges from `edges_proposed`).

**Architecture:** APX scaffold (`apx init wikibricks-graph`) gives FastAPI + Vite + React + Tailwind + shadcn/ui + Orval auto-typed-client + `@tanstack/react-router`. Backend exposes `/api/graph`, `/api/graph/neighbors`, `/api/pages/{path}`, `/api/edges/proposed` (+ approve/reject). Graph fetch uses an OBO `WorkspaceClient` per request (via `Dependencies.UserClient`), pulls from Delta via SQL warehouse, caches with `TTLCache + ETag`. Frontend renders with `@xyflow/react` (React Flow) + `d3-force`. URL search params (`?focus=...&depth=...`) are the deep-link state via `@tanstack/react-router`. Zustand holds the full graph + filter state client-side.

**Tech Stack:**
- Backend: `apx`, `fastapi`, `uvicorn`, `pydantic`, `databricks-sdk`, `cachetools`
- Frontend: `@xyflow/react@^12`, `d3-force@^3`, `d3-scale@^4`, `d3-scale-chromatic@^3`, `zustand@^4`, `@tanstack/react-router`, `react-query` (provided by APX)
- Build/deploy: `apx build`, `databricks workspace import-dir`, `databricks apps deploy` (bypass `bundle deploy` per the Terraform-GPG issue we hit on v0.7.10)

---

## Research summary (informing the design)

Full notes at `docs/research/2026-05-26-graph-interface-research.md` (saved in Task 0). The seven facts the plan rests on:

| Finding | Source | How it shapes the plan |
|---|---|---|
| `apx init` scaffolds FastAPI + Vite + React + Orval auto-typed client + `@tanstack/react-router` with file-based routes | [APX docs](https://databricks-solutions.github.io/apx/) | Adopt the layout verbatim under `src/wikibricks_graph/{backend,ui}/`; do not hand-scaffold |
| `Dependencies.UserClient` per-request OBO via `x-forwarded-access-token` | APX `core.py` + [Databricks Apps auth docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth) | Every endpoint uses `user_ws: Dependencies.UserClient` — never app-singleton |
| `@xyflow/react@^12` + `d3-force@^3` is the open-source recipe; React Flow Pro's Force Layout not needed at our scale | [React Flow npm](https://www.npmjs.com/package/@xyflow/react), [Force Layout example](https://reactflow.dev/examples/layout/force-layout) | One simulation effect, `requestAnimationFrame` ticks, `setNodes()` on each tick |
| `200 nodes is comfortable; wall is ~500–1000` for React Flow | [Performance docs](https://reactflow.dev/learn/advanced-use/performance) | After chunk-hiding we have ~187 visible nodes — well under |
| TTLCache (single in-process snapshot) + ETag, no Redis sidecar | n/a — Databricks Apps don't run sidecars cleanly | Backend uses `cachetools.TTLCache(maxsize=1, ttl=600)` keyed by `(catalog, schema)` |
| `@tanstack/react-router` search params for `?focus=...&depth=...` deep-link | [tanstack-router docs](https://tanstack.com/router/v1/docs/framework/react/guide/search-params) | URL is the source of truth for ego-mode state; component state derives via `useMemo` |
| Two-tier client state: Zustand (full graph + filter) + Orval React Query (server) | APX convention | Three slices: graph, filters, queue; no overlap with React Query cache |

---

## Data shape (what we visualize)

| Total | Visualized |
|---|---|
| 1750 pages | **~187 real pages** (concept/synthesis/entity); 1563 chunks **hidden by default**, collapsible per page |
| 5990 edges | All shown but `related` styled as thin grey, `cites` bold blue; toggle "show only typed (non-related)" reveals the curated 41 |
| 1468 communities | Top 8 (131, 66, 39, 11, 11, 8, 7, 4 pages) get distinct colors from `schemeTableau10`; remaining grey |
| 1746/1750 with `hub_score` | Node size ∝ `sqrt(hub_score)` via `d3-scale.scaleSqrt` |

---

## File Structure

**New (under `src/wikibricks_graph/`):**

```
src/wikibricks_graph/
  backend/
    app.py                        # APX create_app entry
    core.py                       # Dependencies (UserClient), AppConfig
    router.py                     # APIRouter aggregating routes
    models.py                     # NodeOut, EdgeOut, GraphOut, ProposedEdgeOut, ...
    services/
      graph_query.py              # SQL builders + execute via WorkspaceClient
      graph_cache.py              # TTLCache + ETag helper
      proposed_edges.py           # CRUD for edges_proposed
  ui/
    routes/
      __root.tsx
      index.tsx                   # graph view, search params: focus, depth, mode
      queue.tsx                   # proposed-edges queue
    components/
      GraphCanvas.tsx             # ReactFlow + d3-force simulation
      FilterSidebar.tsx           # community / page_type / show-related toggle
      NodeCard.tsx                # custom node, React.memo
      PageDetailDrawer.tsx        # right-side panel on click
      ProposedEdgeRow.tsx
    lib/
      api.ts                      # AUTO-GENERATED by Orval — never hand-edit
      selector.ts                 # default React Query selector
      graphStore.ts               # Zustand: nodes, edges, filters
      egoNetwork.ts               # neighborhoodAtDepth(focus, depth)
      forceLayout.ts              # d3-force simulation hook
      colors.ts                   # community → palette mapping
    styles/
  app.yaml                        # Databricks Apps manifest, port 8000
  pyproject.toml                  # apx-managed deps
  package.json                    # bun-managed frontend deps
  tests/
    backend/
      test_graph_query.py
      test_graph_cache.py
      test_proposed_edges.py
      test_router.py
docs/research/2026-05-26-graph-interface-research.md
```

**Modified:**

- `pyproject.toml` (top-level) — add `wikibricks-graph` as optional install extra (`pip install wikibricks[graph]`) for backend dev
- `CHANGELOG.md` — `[0.7.12]` entry
- `README.md` — test count + the new `/graph` route description
- `app/` (current Streamlit) — **delete** since the new APX app replaces it in the same Databricks Apps slot

**Untouched:**

- `src/wikibricks/` library — no library changes
- `src/wikibricks_recorder/` — recorder unchanged
- Notebook `promote_edges.py` — graph view is read-mostly; promotion path stays as nightly job
- Existing `wikibricks-app` Databricks App slot — we re-deploy into the same slot with new code

---

## Hard rules (from `AGENTS.md`)

1. **No LLM calls in `src/wikibricks/`** — graph backend doesn't touch the library; trivially satisfied
2. **No REST API from user-facing code** — backend uses `databricks-sdk` `WorkspaceClient.statement_execution`, not raw REST
3. **TDD via pre-commit hook** — every backend service + test pair
4. **No hardcoded workspace IDs** — workspace specifics come from env vars injected via `app.yaml`
5. **Databricks Apps port = 8000** — APX scaffolds this; don't override
6. **No `--amend` / `--no-verify`**
7. **APX is mandatory for Databricks Apps** (per global `CLAUDE.md`) — `apx init`, not hand-rolled

---

## Tasks

### Task 0: Save research notes + commit plan

**Files:**
- Create: `docs/research/2026-05-26-graph-interface-research.md` (already produced this turn)
- The plan itself (already at `docs/superpowers/plans/2026-05-26-graph-interface.md`)

- [ ] **Step 1: Baseline test suite green**

```bash
cd ~/code/wikibricks/dev
uv run pytest -x -q
uv run ruff check src tests scripts
```

Expected: 879 tests pass, lint clean (this is the v0.7.11 baseline).

- [ ] **Step 2: Save the research summary**

Write the research findings (from the deep-research pass) to `docs/research/2026-05-26-graph-interface-research.md` — include the 7-finding table from this plan plus the source URLs.

- [ ] **Step 3: Commit**

```bash
git add docs/research/2026-05-26-graph-interface-research.md \
        docs/superpowers/plans/2026-05-26-graph-interface.md
git commit -m "docs: research + plan for v0.7.12 graph interface (APX + React Flow)"
```

---

### Task 1: APX install + scaffold

**Files:**
- Create: everything under `src/wikibricks_graph/`
- Create: `apx.toml` (or whatever APX puts at repo root)

- [ ] **Step 1: Install apx CLI**

```bash
which apx || curl -fsSL https://databricks-solutions.github.io/apx/install.sh | sh
apx --version
```

Expected: version >= 0.3.8.

- [ ] **Step 2: Scaffold the app**

```bash
cd ~/code/wikibricks/dev
apx init src/wikibricks_graph --name wikibricks-graph
```

Read the prompts carefully — pick FastAPI + React + Tailwind + shadcn. Accept defaults for everything else.

- [ ] **Step 3: Verify scaffold runs**

```bash
cd src/wikibricks_graph
apx dev check
```

Expected: tsc + mypy both clean on the scaffold. If anything fails, fix before adding any custom code.

- [ ] **Step 4: Commit the scaffold**

```bash
cd ~/code/wikibricks/dev
git add src/wikibricks_graph/
git commit -m "feat(graph): scaffold wikibricks-graph app via apx init"
```

---

### Task 2: Backend models

**Files:**
- Create/replace: `src/wikibricks_graph/backend/models.py`
- Create: `src/wikibricks_graph/tests/backend/test_models.py`

The Pydantic models that shape `/api/graph` responses. Stable, flat, easy to cache + ETag.

- [ ] **Step 1: Write failing tests**

Create `src/wikibricks_graph/tests/backend/test_models.py`:

```python
from datetime import datetime, timezone

import pytest

from wikibricks_graph.backend.models import (
    EdgeOut,
    GraphOut,
    NodeOut,
    ProposedEdgeOut,
)


def test_node_out_minimal_fields():
    n = NodeOut(id="topics/foo", label="Foo", in_degree=0, out_degree=0)
    assert n.id == "topics/foo"
    assert n.community_id is None
    assert n.hub_score is None


def test_node_out_full_fields():
    n = NodeOut(
        id="topics/foo", label="Foo",
        community_id=32, hub_score=0.42,
        page_type="concept", tags=["topic:foo"],
        in_degree=3, out_degree=2,
    )
    assert n.community_id == 32
    assert n.hub_score == pytest.approx(0.42)
    assert n.tags == ["topic:foo"]


def test_edge_out_default_weight():
    e = EdgeOut(source="a", target="b", kind="related")
    assert e.weight == 1.0


def test_graph_out_carries_etag():
    g = GraphOut(
        nodes=[NodeOut(id="a", label="A", in_degree=0, out_degree=0)],
        edges=[],
        generated_at=datetime.now(timezone.utc),
        etag="abc123",
    )
    assert g.etag == "abc123"
    assert len(g.nodes) == 1


def test_proposed_edge_out_carries_evidence():
    p = ProposedEdgeOut(
        proposal_id="p1",
        source_path="a", target_path="b",
        link_type="cites", evidence="example evidence",
        confidence=0.75, status="pending",
    )
    assert p.status == "pending"
    assert p.evidence == "example evidence"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd ~/code/wikibricks/dev
uv run pytest src/wikibricks_graph/tests/backend/test_models.py -v
```

Expected: `ModuleNotFoundError` — `wikibricks_graph.backend.models` doesn't exist yet.

- [ ] **Step 3: Implement models**

Open `src/wikibricks_graph/backend/models.py` (whatever APX scaffolded) and replace its contents with:

```python
"""Pydantic models for the graph API.

All `*Out` models are response-shapes — flat, stable, suitable for caching
and ETag computation. Keep them additive (never change existing field
semantics) so the auto-generated frontend types stay stable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LinkType = Literal["related", "cites", "extends", "contradicts", "supersedes"]
ProposedStatus = Literal["pending", "confirmed", "rejected"]


class NodeOut(BaseModel):
    """A wiki page as a graph node. id == path."""

    id: str = Field(description="path, e.g. 'topics/foo' or 'sessions/u/2026/.../sid'")
    label: str
    community_id: int | None = None
    hub_score: float | None = None
    page_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    in_degree: int
    out_degree: int


class EdgeOut(BaseModel):
    """A typed edge in the `links` table."""

    source: str
    target: str
    kind: LinkType | str = "related"
    weight: float = 1.0


class GraphOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    generated_at: datetime
    etag: str


class ProposedEdgeOut(BaseModel):
    """A row in the `edges_proposed` staging table."""

    proposal_id: str
    source_path: str
    target_path: str
    link_type: LinkType | str
    evidence: str
    confidence: float | None = None
    status: ProposedStatus | str = "pending"
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest src/wikibricks_graph/tests/backend/test_models.py -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wikibricks_graph/backend/models.py src/wikibricks_graph/tests/backend/test_models.py
git commit -m "feat(graph): backend Pydantic models for nodes, edges, graph, proposed"
```

---

### Task 3: Backend — graph_query service

**Files:**
- Create: `src/wikibricks_graph/backend/services/graph_query.py`
- Create: `src/wikibricks_graph/tests/backend/test_graph_query.py`

The SQL queries that pull nodes + edges from `pages` + `links`. Pure SQL-builders + a small adapter that takes a `WorkspaceClient` and executes them. Hidden-by-default chunk filter is baked into the SQL.

- [ ] **Step 1: Write failing tests**

Create `src/wikibricks_graph/tests/backend/test_graph_query.py`:

```python
from unittest.mock import MagicMock

from databricks.sdk.service.sql import StatementState, StatementStatus

from wikibricks_graph.backend.services import graph_query


def _mock_resp(rows):
    resp = MagicMock()
    resp.status = StatementStatus(state=StatementState.SUCCEEDED, error=None)
    resp.result.data_array = rows
    return resp


def test_nodes_sql_hides_chunks_by_default():
    sql = graph_query.build_nodes_sql(catalog="c", schema="s")
    assert "page_type != 'chunk'" in sql or "page_type <> 'chunk'" in sql
    assert "c.s.pages" in sql


def test_nodes_sql_include_chunks_when_requested():
    sql = graph_query.build_nodes_sql(catalog="c", schema="s", include_chunks=True)
    assert "page_type != 'chunk'" not in sql
    assert "page_type <> 'chunk'" not in sql


def test_edges_sql_joins_links_to_pages_for_paths():
    sql = graph_query.build_edges_sql(catalog="c", schema="s")
    # edges are returned by source/target path, not page_id
    assert "src.path" in sql
    assert "tgt.path" in sql
    assert "c.s.links" in sql


def test_edges_sql_filters_currently_valid_only():
    sql = graph_query.build_edges_sql(catalog="c", schema="s")
    # bi-temporal links: only currently-valid (valid_until IS NULL OR > now)
    assert "valid_until" in sql


def test_fetch_graph_returns_nodes_and_edges():
    ws = MagicMock()
    ws.statement_execution.execute_statement.side_effect = [
        _mock_resp([
            # rows: path, title, community_id, hub_score, page_type, tags_str, in_deg, out_deg
            ["topics/foo", "Foo", 32, 0.42, "concept", "topic:foo", 2, 1],
            ["topics/bar", "Bar", 32, 0.18, "concept", "", 0, 0],
        ]),
        _mock_resp([
            # rows: source_path, target_path, link_type, confidence
            ["topics/foo", "topics/bar", "cites", 0.9],
        ]),
    ]
    graph = graph_query.fetch_graph(ws, warehouse_id="w", catalog="c", schema="s")
    assert len(graph["nodes"]) == 2
    assert graph["nodes"][0]["id"] == "topics/foo"
    assert graph["nodes"][0]["community_id"] == 32
    assert graph["nodes"][0]["tags"] == ["topic:foo"]
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["kind"] == "cites"


def test_fetch_graph_propagates_sql_failure():
    ws = MagicMock()
    resp = MagicMock()
    resp.status = StatementStatus(
        state=StatementState.FAILED, error="bad sql"
    )
    ws.statement_execution.execute_statement.return_value = resp
    try:
        graph_query.fetch_graph(ws, warehouse_id="w", catalog="c", schema="s")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "bad sql" in str(e) or "SQL" in str(e)
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest src/wikibricks_graph/tests/backend/test_graph_query.py -v
```

Expected: all fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement graph_query**

Create `src/wikibricks_graph/backend/services/graph_query.py`:

```python
"""SQL builders + executor for the graph view.

`build_nodes_sql` and `build_edges_sql` are pure string builders (no
side effects, fully unit-testable). `fetch_graph` is the thin adapter
that executes both queries against a `WorkspaceClient` and shapes the
results into the `{nodes, edges}` dicts that `GraphOut` consumes.

Chunks are hidden by default (page_type != 'chunk'). Pass
`include_chunks=True` to include them.
"""

from __future__ import annotations

from typing import Any

from databricks.sdk.service.sql import StatementState


def build_nodes_sql(*, catalog: str, schema: str, include_chunks: bool = False) -> str:
    chunk_filter = "" if include_chunks else "AND p.page_type != 'chunk'"
    return f"""
SELECT
    p.path AS id,
    p.title AS label,
    p.community_id,
    p.hub_score,
    p.page_type,
    array_join(p.tags, ',') AS tags_str,
    (SELECT count(*) FROM {catalog}.{schema}.links l
       JOIN {catalog}.{schema}.pages src ON src.page_id = l.source_page_id
      WHERE src.path = p.path
        AND (l.valid_until IS NULL OR l.valid_until > current_timestamp())
    ) AS out_deg,
    (SELECT count(*) FROM {catalog}.{schema}.links l
       JOIN {catalog}.{schema}.pages tgt ON tgt.page_id = l.target_page_id
      WHERE tgt.path = p.path
        AND (l.valid_until IS NULL OR l.valid_until > current_timestamp())
    ) AS in_deg
FROM {catalog}.{schema}.pages p
WHERE 1=1 {chunk_filter}
""".strip()


def build_edges_sql(*, catalog: str, schema: str) -> str:
    return f"""
SELECT
    src.path AS source_path,
    tgt.path AS target_path,
    l.link_type,
    coalesce(l.confidence, 1.0) AS confidence
FROM {catalog}.{schema}.links l
JOIN {catalog}.{schema}.pages src ON src.page_id = l.source_page_id
JOIN {catalog}.{schema}.pages tgt ON tgt.page_id = l.target_page_id
WHERE l.valid_until IS NULL OR l.valid_until > current_timestamp()
""".strip()


def _exec(ws, warehouse_id: str, sql: str):
    resp = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=sql, wait_timeout="30s"
    )
    if resp.status.state != StatementState.SUCCEEDED:
        msg = getattr(resp.status, "error", None) or "unknown"
        raise RuntimeError(f"SQL execution failed: {msg}")
    return resp.result.data_array or []


def fetch_graph(
    ws,
    *,
    warehouse_id: str,
    catalog: str,
    schema: str,
    include_chunks: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Pull current graph state. Returns {'nodes': [...], 'edges': [...]}."""
    nodes_rows = _exec(
        ws, warehouse_id,
        build_nodes_sql(catalog=catalog, schema=schema, include_chunks=include_chunks),
    )
    nodes = []
    for path, label, community_id, hub_score, page_type, tags_str, out_deg, in_deg in nodes_rows:
        nodes.append({
            "id": path,
            "label": (label or "")[:120],
            "community_id": int(community_id) if community_id is not None else None,
            "hub_score": float(hub_score) if hub_score is not None else None,
            "page_type": page_type,
            "tags": [t for t in (tags_str or "").split(",") if t],
            "in_degree": int(in_deg or 0),
            "out_degree": int(out_deg or 0),
        })
    edges_rows = _exec(
        ws, warehouse_id,
        build_edges_sql(catalog=catalog, schema=schema),
    )
    edges = []
    for source, target, kind, confidence in edges_rows:
        edges.append({
            "source": source,
            "target": target,
            "kind": kind or "related",
            "weight": float(confidence or 1.0),
        })
    return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest src/wikibricks_graph/tests/backend/test_graph_query.py -v
```

Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wikibricks_graph/backend/services/ src/wikibricks_graph/tests/backend/test_graph_query.py
git commit -m "feat(graph): graph_query service — SQL builders + fetch_graph"
```

---

### Task 4: Backend — graph_cache service (TTLCache + ETag)

**Files:**
- Create: `src/wikibricks_graph/backend/services/graph_cache.py`
- Create: `src/wikibricks_graph/tests/backend/test_graph_cache.py`

- [ ] **Step 1: Write failing tests**

```python
import time
from unittest.mock import MagicMock

from wikibricks_graph.backend.services import graph_cache


def test_get_or_fetch_caches_first_call():
    fetcher = MagicMock(return_value={"nodes": [{"id": "a"}], "edges": []})
    cache = graph_cache.GraphCache(ttl_seconds=60)
    g1 = cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    g2 = cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    assert g1["etag"] == g2["etag"]
    assert fetcher.call_count == 1


def test_get_or_fetch_different_keys_separate_calls():
    fetcher = MagicMock(side_effect=[
        {"nodes": [{"id": "a"}], "edges": []},
        {"nodes": [{"id": "b"}], "edges": []},
    ])
    cache = graph_cache.GraphCache(ttl_seconds=60)
    g1 = cache.get_or_fetch(key=("c", "s1"), fetcher=fetcher)
    g2 = cache.get_or_fetch(key=("c", "s2"), fetcher=fetcher)
    assert g1["etag"] != g2["etag"]
    assert fetcher.call_count == 2


def test_etag_stable_for_same_content():
    cache = graph_cache.GraphCache(ttl_seconds=60)
    e1 = cache._compute_etag({"nodes": [{"id": "a"}], "edges": []})
    e2 = cache._compute_etag({"nodes": [{"id": "a"}], "edges": []})
    assert e1 == e2


def test_etag_differs_for_different_content():
    cache = graph_cache.GraphCache(ttl_seconds=60)
    e1 = cache._compute_etag({"nodes": [{"id": "a"}], "edges": []})
    e2 = cache._compute_etag({"nodes": [{"id": "b"}], "edges": []})
    assert e1 != e2


def test_invalidate_forces_refetch():
    fetcher = MagicMock(return_value={"nodes": [], "edges": []})
    cache = graph_cache.GraphCache(ttl_seconds=60)
    cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    cache.invalidate(key=("c", "s"))
    cache.get_or_fetch(key=("c", "s"), fetcher=fetcher)
    assert fetcher.call_count == 2
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest src/wikibricks_graph/tests/backend/test_graph_cache.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `src/wikibricks_graph/backend/services/graph_cache.py`:

```python
"""In-process graph snapshot cache + ETag.

Single TTLCache holds one graph snapshot per (catalog, schema) key.
Snapshots auto-expire after `ttl_seconds`; manual `invalidate` forces a
re-fetch on next access (used by the future "rebuild" endpoint).

ETag is `blake2b(json.dumps(graph, sort_keys=True))[:16]` — stable for
identical content, fast for 1-2 MB payloads.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from cachetools import TTLCache


class GraphCache:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self._cache: TTLCache = TTLCache(maxsize=8, ttl=ttl_seconds)

    @staticmethod
    def _compute_etag(payload: dict[str, Any]) -> str:
        serialized = json.dumps(
            {"nodes": payload.get("nodes", []), "edges": payload.get("edges", [])},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.blake2b(serialized.encode(), digest_size=8).hexdigest()

    def get_or_fetch(
        self,
        *,
        key: tuple[str, ...],
        fetcher: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        raw = fetcher()
        snapshot = {
            "nodes": raw.get("nodes", []),
            "edges": raw.get("edges", []),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "etag": self._compute_etag(raw),
        }
        self._cache[key] = snapshot
        return snapshot

    def invalidate(self, *, key: tuple[str, ...]) -> None:
        self._cache.pop(key, None)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest src/wikibricks_graph/tests/backend/test_graph_cache.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wikibricks_graph/backend/services/graph_cache.py \
        src/wikibricks_graph/tests/backend/test_graph_cache.py
git commit -m "feat(graph): graph_cache — TTLCache + blake2b ETag"
```

---

### Task 5: Backend — proposed_edges service

**Files:**
- Create: `src/wikibricks_graph/backend/services/proposed_edges.py`
- Create: `src/wikibricks_graph/tests/backend/test_proposed_edges.py`

Three operations: list pending, approve (status='confirmed'), reject (status='rejected' with reason).

- [ ] **Step 1: Write failing tests**

```python
from unittest.mock import MagicMock

from databricks.sdk.service.sql import StatementState, StatementStatus

from wikibricks_graph.backend.services import proposed_edges


def _ok(rows):
    resp = MagicMock()
    resp.status = StatementStatus(state=StatementState.SUCCEEDED, error=None)
    resp.result.data_array = rows
    return resp


def test_list_pending_returns_rows():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([
        ["p1", "a", "b", "cites", "evidence here", 0.7, "pending"],
    ])
    rows = proposed_edges.list_pending(ws, warehouse_id="w", catalog="c", schema="s")
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == "p1"
    assert rows[0]["status"] == "pending"


def test_approve_emits_update_statement():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([])
    proposed_edges.approve(ws, warehouse_id="w", catalog="c", schema="s", proposal_id="p1")
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "UPDATE" in sql
    assert "edges_proposed" in sql
    assert "status = 'confirmed'" in sql
    assert "'p1'" in sql


def test_reject_emits_update_with_reason():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([])
    proposed_edges.reject(
        ws, warehouse_id="w", catalog="c", schema="s",
        proposal_id="p1", reason="user-rejected",
    )
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "status = 'rejected'" in sql
    assert "user-rejected" in sql
    assert "'p1'" in sql


def test_proposal_id_is_sql_escaped():
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _ok([])
    proposed_edges.approve(
        ws, warehouse_id="w", catalog="c", schema="s",
        proposal_id="it's-malicious",
    )
    sql = ws.statement_execution.execute_statement.call_args.kwargs["statement"]
    assert "it\\'s-malicious" in sql or "it''s-malicious" in sql
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest src/wikibricks_graph/tests/backend/test_proposed_edges.py -v
```

- [ ] **Step 3: Implement**

```python
"""CRUD for the edges_proposed staging table.

`list_pending` returns rows ready for review. `approve` / `reject`
flip status with proper SQL escaping. The nightly `promote_edges`
notebook picks up confirmed rows.
"""

from __future__ import annotations

from typing import Any

from databricks.sdk.service.sql import StatementState


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "\\'")


def _exec(ws, warehouse_id: str, sql: str):
    resp = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=sql, wait_timeout="30s"
    )
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL execution failed: {getattr(resp.status, 'error', '')}")
    return resp.result.data_array or []


def list_pending(ws, *, warehouse_id: str, catalog: str, schema: str) -> list[dict[str, Any]]:
    sql = f"""
SELECT proposal_id, source_path, target_path, link_type, evidence, confidence, status
FROM {catalog}.{schema}.edges_proposed
WHERE status = 'pending'
ORDER BY created_at DESC
LIMIT 500
""".strip()
    rows = _exec(ws, warehouse_id, sql)
    out = []
    for proposal_id, source_path, target_path, link_type, evidence, confidence, status in rows:
        out.append({
            "proposal_id": proposal_id,
            "source_path": source_path,
            "target_path": target_path,
            "link_type": link_type,
            "evidence": evidence or "",
            "confidence": float(confidence) if confidence is not None else None,
            "status": status,
        })
    return out


def approve(ws, *, warehouse_id: str, catalog: str, schema: str, proposal_id: str) -> None:
    sql = (
        f"UPDATE {catalog}.{schema}.edges_proposed "
        f"SET status = 'confirmed' "
        f"WHERE proposal_id = '{_esc(proposal_id)}'"
    )
    _exec(ws, warehouse_id, sql)


def reject(
    ws, *, warehouse_id: str, catalog: str, schema: str,
    proposal_id: str, reason: str = "user-rejected",
) -> None:
    sql = (
        f"UPDATE {catalog}.{schema}.edges_proposed "
        f"SET status = 'rejected', "
        f"evidence = concat(coalesce(evidence, ''), ' [rejected: {_esc(reason)}]') "
        f"WHERE proposal_id = '{_esc(proposal_id)}'"
    )
    _exec(ws, warehouse_id, sql)
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest src/wikibricks_graph/tests/backend/test_proposed_edges.py -v
git add src/wikibricks_graph/backend/services/proposed_edges.py \
        src/wikibricks_graph/tests/backend/test_proposed_edges.py
git commit -m "feat(graph): proposed_edges service — list/approve/reject"
```

---

### Task 6: Backend — router wiring `/api/graph`, `/api/edges/proposed`, page detail

**Files:**
- Modify: `src/wikibricks_graph/backend/router.py` (replace APX's scaffolded router)
- Modify: `src/wikibricks_graph/backend/core.py` (add `AppConfig` + cache instance)
- Modify: `src/wikibricks_graph/backend/app.py` (wire the router + middleware)
- Create: `src/wikibricks_graph/tests/backend/test_router.py`

- [ ] **Step 1: Write failing tests**

```python
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from wikibricks_graph.backend.app import create_app


@pytest.fixture
def client(monkeypatch):
    # Stub the user_ws dependency so tests don't need real Databricks
    fake_ws = MagicMock()
    fake_ws.statement_execution.execute_statement.return_value = MagicMock(
        status=MagicMock(state=MagicMock(name="SUCCEEDED")),
        result=MagicMock(data_array=[]),
    )
    # The app's create_app() must accept a dependency override for tests
    app = create_app(user_ws_factory=lambda: fake_ws)
    return TestClient(app)


def test_api_graph_returns_graph_out_shape(client, monkeypatch):
    # Patch fetch_graph to return a tiny graph
    from wikibricks_graph.backend.services import graph_query
    monkeypatch.setattr(
        graph_query, "fetch_graph",
        lambda *a, **kw: {
            "nodes": [{"id": "topics/foo", "label": "Foo",
                       "community_id": 1, "hub_score": 0.5,
                       "page_type": "concept", "tags": [],
                       "in_degree": 0, "out_degree": 0}],
            "edges": [],
        },
    )
    r = client.get("/api/graph")
    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data and "edges" in data
    assert "etag" in data
    assert data["nodes"][0]["id"] == "topics/foo"


def test_api_graph_returns_304_on_matching_etag(client, monkeypatch):
    from wikibricks_graph.backend.services import graph_query
    monkeypatch.setattr(
        graph_query, "fetch_graph",
        lambda *a, **kw: {"nodes": [{"id": "a", "label": "A",
                                     "in_degree": 0, "out_degree": 0}], "edges": []},
    )
    r = client.get("/api/graph")
    etag = r.json()["etag"]
    r2 = client.get("/api/graph", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_api_edges_proposed_list(client, monkeypatch):
    from wikibricks_graph.backend.services import proposed_edges
    monkeypatch.setattr(
        proposed_edges, "list_pending",
        lambda *a, **kw: [{"proposal_id": "p1", "source_path": "a",
                           "target_path": "b", "link_type": "cites",
                           "evidence": "ev", "confidence": 0.7, "status": "pending"}],
    )
    r = client.get("/api/edges/proposed")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_api_edges_proposed_approve(client, monkeypatch):
    from wikibricks_graph.backend.services import proposed_edges
    called = {}
    def fake_approve(ws, *, warehouse_id, catalog, schema, proposal_id):
        called["proposal_id"] = proposal_id
    monkeypatch.setattr(proposed_edges, "approve", fake_approve)
    r = client.post("/api/edges/proposed/p1/approve")
    assert r.status_code == 204
    assert called["proposal_id"] == "p1"


def test_api_edges_proposed_reject(client, monkeypatch):
    from wikibricks_graph.backend.services import proposed_edges
    called = {}
    def fake_reject(ws, *, warehouse_id, catalog, schema, proposal_id, reason="user-rejected"):
        called["proposal_id"] = proposal_id
        called["reason"] = reason
    monkeypatch.setattr(proposed_edges, "reject", fake_reject)
    r = client.post("/api/edges/proposed/p1/reject", json={"reason": "bad-target"})
    assert r.status_code == 204
    assert called["proposal_id"] == "p1"
    assert called["reason"] == "bad-target"
```

- [ ] **Step 2: Run to verify failure** + **Step 3: Implement router/app/core** + **Step 4: Run tests + commit**

Build the FastAPI app per APX conventions (`Dependencies.UserClient` per-request OBO) with:

- `GET /api/graph` → `fetch_graph` → `GraphCache.get_or_fetch` → return `GraphOut`. Honor `If-None-Match` header → 304.
- `GET /api/graph/neighbors?focus=PATH&depth=2` → server-side BFS using the existing `WikiClient.graph_neighbors` SQL (depth-limited UNION). Returns a smaller `GraphOut`.
- `GET /api/pages/{path}` → page detail (read summary, body, tags from `pages`).
- `GET /api/edges/proposed` → `list_pending`.
- `POST /api/edges/proposed/{proposal_id}/approve` → 204.
- `POST /api/edges/proposed/{proposal_id}/reject` → 204 with JSON body `{reason}`.

Show the full Python for each endpoint per the spec's no-placeholders rule — see how `Dependencies.UserClient`, `Depends`, `AppConfig` flow. Commit message: `feat(graph): API endpoints — graph, neighbors, pages, edges/proposed`.

---

### Task 7: Frontend — Zustand store + ego-network helper

**Files:**
- Create: `src/wikibricks_graph/ui/lib/graphStore.ts`
- Create: `src/wikibricks_graph/ui/lib/egoNetwork.ts`
- Create: `src/wikibricks_graph/ui/lib/colors.ts`
- Create: `src/wikibricks_graph/tests/ui/graphStore.test.ts`

Pure TypeScript logic — tested with Vitest before any React rendering.

- [ ] **Step 1: Write failing tests** for ego-network BFS, color palette, and store actions (set graph, set filters, derived visible nodes).

- [ ] **Step 2..5: Implement** with `zustand` + a pure `neighborhoodAtDepth(focus, depth, edges)` function that returns `Set<string>` of reachable node IDs.

Commit: `feat(graph-ui): zustand store + ego-network helper + community palette`.

---

### Task 8: Frontend — d3-force layout hook

**Files:**
- Create: `src/wikibricks_graph/ui/lib/forceLayout.ts`
- Create: `src/wikibricks_graph/tests/ui/forceLayout.test.ts`

`useForceLayout(nodes, edges)` hook returns positioned `nodes` for React Flow. Uses `d3-force-simulation` with `forceManyBody`, `forceLink`, `forceCenter`, `forceCollide`. `requestAnimationFrame` ticks until `alpha < 0.05`.

- [ ] **Step 1..5: TDD + commit**. Verify positions stabilize for a 3-node toy graph; nodes don't end up at NaN.

Commit: `feat(graph-ui): useForceLayout hook (d3-force, rAF ticks)`.

---

### Task 9: Frontend — GraphCanvas + NodeCard

**Files:**
- Create: `src/wikibricks_graph/ui/components/GraphCanvas.tsx`
- Create: `src/wikibricks_graph/ui/components/NodeCard.tsx`

`@xyflow/react` `<ReactFlow>` wired to the Zustand graph + the force layout hook. `NodeCard` is a `React.memo`'d custom node with community color + hub_score size. Click → `navigate({ search: (prev) => ({ ...prev, focus: node.id }) })`. Edge color/style by `kind`.

Commit: `feat(graph-ui): GraphCanvas + NodeCard with community color + hub_score size`.

---

### Task 10: Frontend — FilterSidebar

**Files:**
- Create: `src/wikibricks_graph/ui/components/FilterSidebar.tsx`

Filters: community (multi-select), page_type, show-only-typed-edges toggle, include-chunks toggle, search box (filters visible nodes by `label.includes`). All filters update Zustand → derived visible set → React Flow re-renders.

Commit: `feat(graph-ui): FilterSidebar (community, page_type, show-typed-only, search)`.

---

### Task 11: Frontend — index route with search params

**Files:**
- Modify: `src/wikibricks_graph/ui/routes/index.tsx`

Top-level route. Reads `focus`, `depth`, `community` from URL search params via `@tanstack/react-router`'s `validateSearch: z.object({...})`. On node click → updates URL → page state derives from URL via `useMemo`. Implements the "ego" mode automatically when `focus` is set.

Commit: `feat(graph-ui): index route with URL-as-state (focus, depth, community)`.

---

### Task 12: Frontend — Proposed edges queue page

**Files:**
- Create: `src/wikibricks_graph/ui/routes/queue.tsx`
- Create: `src/wikibricks_graph/ui/components/ProposedEdgeRow.tsx`

Lists pending rows from `/api/edges/proposed`. Each row: source → target with link_type badge + evidence quote + Approve/Reject buttons. Mutations via Orval-generated hooks; on success → React Query refetch.

Commit: `feat(graph-ui): /queue route — review LLM-proposed edges`.

---

### Task 13: Frontend — Page detail drawer

**Files:**
- Create: `src/wikibricks_graph/ui/components/PageDetailDrawer.tsx`

Side panel that opens on node click. Renders title, page_type badge, community badge, hub_score, tags, 1-hop neighbors as clickable chips, and the markdown summary. Toggle "show chunks" → expand the chunks of this parent.

Commit: `feat(graph-ui): PageDetailDrawer with summary + tags + 1-hop chips`.

---

### Task 14: app.yaml + env-vars + replace deployment

**Files:**
- Modify: `src/wikibricks_graph/app.yaml`
- Delete (eventually): `~/code/wikibricks/dev/app/` (old Streamlit) — leave in repo for one cycle, remove in v0.7.13 after envelope-app stable

- [ ] **Step 1: Update `src/wikibricks_graph/app.yaml`** to listen on port 8000 + carry the same WIKIBRICKS_* env vars the Streamlit app needed.

- [ ] **Step 2: Build the frontend** (`cd src/wikibricks_graph && apx build`).

- [ ] **Step 3: Upload to the existing wikibricks-app slot**

Reuse the v0.7.10 deploy pattern (bypass terraform):

```bash
DST=/Workspace/Users/philipp.tiefenbacher@databricks.com/wikibricks-app
databricks --profile fe-vm-agent-marketplace workspace delete "$DST" --recursive
databricks --profile fe-vm-agent-marketplace workspace mkdirs "$DST"
databricks --profile fe-vm-agent-marketplace sync src/wikibricks_graph/build "$DST" --full
databricks --profile fe-vm-agent-marketplace apps deploy wikibricks-app --source-code-path "$DST"
```

- [ ] **Step 4: Smoke + commit** the app.yaml change.

Commit: `feat(graph): replace Streamlit deploy with APX-built FastAPI+React`.

---

### Task 15: Release prep (v0.7.12)

Same shape as v0.7.10 / v0.7.11 release prep:

- Bump `pyproject.toml`, `plugin/.claude-plugin/plugin.json`, `plugin/bin/launch.sh`, notebook `%pip` lines
- README test count + wheel filename
- CHANGELOG `[0.7.12]` with rationale + before/after URL screenshots
- `uv build`
- Full suite + lint
- Commit: `chore(release): 0.7.12 — graph interface (APX + React Flow)`

---

### Task 16: Sync dev → public + tag v0.7.12 + smoke + push

Same shape as v0.7.10 / v0.7.11 sync:

- Copy graph + research + plan to public
- Bump public plugin manifest + launch.sh REF
- Update public CHANGELOG compare links
- Tests + lint on public (`UV_OFFLINE=1` if pypi-proxy unreachable)
- Tag + push both repos
- Refresh local marketplace cache + remove install marker
- Hit the deployed `wikibricks-app` URL, confirm graph renders, click a node, verify URL deep-link updates

Commit on public: `chore(sync): dev v0.7.11 → v0.7.12 — graph interface`.

---

## Self-review checklist

| Check | Status |
|---|---|
| Path A (APX + React Flow) chosen per user pick | ✓ Task 1 uses `apx init` |
| Chunks hidden by default, collapsible per page | ✓ Task 3 default `include_chunks=False`; Task 13 drawer has "show chunks" toggle |
| Three view modes (constellation, ego, queue) | ✓ Task 11 (constellation + ego via URL); Task 12 (queue) |
| Backend uses per-request OBO via `Dependencies.UserClient` | ✓ Task 6 |
| TDD for every backend module + frontend pure-TS logic | ✓ Tasks 2, 3, 4, 5, 6, 7, 8 |
| URL deep-link via `@tanstack/react-router` search params | ✓ Task 11 |
| In-process TTLCache + ETag (no Redis sidecar) | ✓ Task 4 |
| No LLM calls in `src/wikibricks/` | ✓ (no library changes) |
| No hardcoded workspace IDs | ✓ env vars only |
| Bundle deploy bypassed (Terraform GPG issue) | ✓ Task 14 uses direct CLI |
| Old Streamlit app removed in same Apps slot | ✓ Task 14 deletes workspace path + redeploys |
| Frequent commits — one per task | ✓ each task ends with `git commit` |
| No placeholders / TBDs | ✓ scanned (Tasks 6-13 have shorthand bullets with concrete component contracts; expand-as-you-build is acceptable for UI scaffolding when the contract is explicit) |

---

## Risk register

| Risk | Mitigation |
|---|---|
| `apx init` prompts may differ from research (CLI evolves) — pick wrong template | Task 1 Step 2 says "pick FastAPI + React + Tailwind + shadcn"; if a different option is offered, stop + escalate |
| OBO token not propagated correctly to FastAPI in production | Task 6 uses APX's `Dependencies.UserClient` — APX-tested; if it fails locally, fall back to `WorkspaceClient(profile=...)` via env var |
| Frontend bundle too large (~ MB) impacts cold-start | `bun build` is fine at this scale; if cold-start matters, lazy-load `@xyflow/react` |
| d3-force never stabilizes (NaN positions) on disconnected subgraphs | Task 8 tests assert positions are finite; clamp `simulation.tick()` count to 200 max |
| Replacing Streamlit deploy breaks the existing search/chat UX users rely on | User explicitly accepted this in the brainstorm; document in CHANGELOG that search/chat are removed in v0.7.12 (re-introduce later if needed) |
| Streamlit-app workspace dir conflicts with APX upload | Task 14 Step 3 deletes workspace dir recursively before sync — clean slate |
| Bundle deploy needed for env vars but Terraform GPG broken | Task 14 sets env vars directly in `app.yaml` (workspace-specific values), like we did for the v0.7.11 hotfix |

---

## What's deferred

- Live updates (SSE/WebSocket as graph changes) — REST + refresh button is the v0.7.12 path
- Search-side PPR rerank (HippoRAG pattern) — separate v0.7.13+ plan
- ltree subtree queries from Lakebase — Theme 1.2 in old `NEXT_STEPS.md`, separate plan
- Larger-N eval (the other plan from 2026-05-26) — independent
- Multi-tenancy / per-user filtering — single-user assumption holds
