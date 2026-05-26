# Research notes — v0.7.12 graph interface (APX + React Flow)

**Date:** 2026-05-26
**Plan:** `docs/superpowers/plans/2026-05-26-graph-interface.md`
**Question:** What stack + patterns build a force-directed graph view of the WikiBricks pages as a Databricks App? Target ~200 visible nodes with click-to-expand, filter sidebar, and `?focus=...&depth=...` deep-link.

## Findings

| Finding | Source | How it shapes the plan |
|---|---|---|
| `apx init` scaffolds FastAPI + Vite + React + Orval auto-typed client + `@tanstack/react-router` with file-based routes + Tailwind + shadcn/ui + bun | [APX docs](https://databricks-solutions.github.io/apx/) | Adopt the layout verbatim under `src/wikibricks_graph/{backend,ui}/`; do not hand-scaffold |
| `Dependencies.UserClient` per-request OBO via `x-forwarded-access-token` | APX `core.py` + [Databricks Apps auth docs](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth) | Every endpoint uses `user_ws: Dependencies.UserClient` — never app-singleton; OBO token is request-scoped |
| `@xyflow/react@^12` (MIT, peer React 18+) + `d3-force@^3` (BSD) is the open-source recipe; React Flow Pro's Force Layout is paid and not needed at our scale | [React Flow npm](https://www.npmjs.com/package/@xyflow/react), [Force Layout example](https://reactflow.dev/examples/layout/force-layout) | One simulation effect, `requestAnimationFrame` ticks, `setNodes()` on each tick |
| 200 nodes is comfortable; wall is ~500–1000 for React Flow with default settings | [Performance docs](https://reactflow.dev/learn/advanced-use/performance) | After chunk-hiding we have ~187 visible nodes — well under the limit |
| TTLCache (single in-process snapshot) + ETag, no Redis sidecar — Databricks Apps don't run sidecars cleanly | n/a | Backend uses `cachetools.TTLCache(maxsize=8, ttl=600)` keyed by `(catalog, schema)`; ETag via `blake2b(...)[:16]` |
| `@tanstack/react-router` search params with `validateSearch: z.object({...})` for `?focus=...&depth=...` deep-link | [tanstack-router docs](https://tanstack.com/router/v1/docs/framework/react/guide/search-params) | URL is the source of truth for ego-mode state; component state derives via `useMemo` |
| Two-tier client state: Zustand (full graph + filter) + Orval React Query (server). React Query handles ETag automatically; Zustand handles UI state | APX convention | Three slices in Zustand: graph, filters, queue; no overlap with React Query cache |

## Stack picks (locked)

| Concern | Choice | Why |
|---|---|---|
| App scaffold | `apx init` (latest v0.3.8+) | Mandatory per global CLAUDE.md; saves ~2 days of OpenAPI/codegen plumbing |
| Backend framework | FastAPI + Pydantic | What APX scaffolds |
| Caching | `cachetools.TTLCache` (in-process) + `blake2b` ETag | No sidecar infra; ~1 MB payloads fit easily |
| Graph viz | `@xyflow/react@^12` | Mature, MIT, ~200 nodes is comfortable |
| Layout | `d3-force@^3` | React Flow community standard; right knobs (manyBody, link, center, collide) |
| Coloring | `d3-scale-chromatic` `schemeTableau10` | 8-9 distinct hues, accessible, well-tested |
| Client state | Zustand 4.x | Light, ~3KB, no Redux boilerplate; URL as deep-link source |
| Router | `@tanstack/react-router` | What APX scaffolds; first-class typed search params |
| Server state | Orval-generated React Query hooks | Auto-typed from FastAPI OpenAPI; no hand-written fetchers |
| UI components | shadcn/ui (Radix + Tailwind) | What APX scaffolds; copy-paste primitives |

## Anti-patterns identified

- **Don't pay for React Flow Pro** — at 200 nodes the open API + d3-force is sufficient
- **Don't WebSocket/SSE** — 1 MB payloads + manual revalidate covers all live-update needs
- **Don't Redis** — Databricks Apps don't run sidecars cleanly; in-process TTLCache is the idiomatic choice
- **Don't cache the OBO `WorkspaceClient`** — tokens are per-request; APX's `Dependencies.UserClient` builds fresh
- **Don't WebSocket/SSE/polling for live updates** — REST + React Query staleTime + manual refresh button
- **Don't mix renderers** — React Flow + d3-force is one stack; Cytoscape.js or Sigma.js would compete with React Flow's DOM renderer

## Sources to verify before external quoting

| Source | URL |
|---|---|
| APX repo | https://github.com/databricks-solutions/apx |
| APX docs | https://databricks-solutions.github.io/apx/ |
| Databricks Apps auth | https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth |
| React Flow npm | https://www.npmjs.com/package/@xyflow/react |
| React Flow Force Layout example | https://reactflow.dev/examples/layout/force-layout |
| React Flow performance | https://reactflow.dev/learn/advanced-use/performance |
| TanStack Router search params | https://tanstack.com/router/v1/docs/framework/react/guide/search-params |

## Data shape (queried on FEVM, 2026-05-26)

| Total | Visualized |
|---|---|
| 1750 pages | ~187 real pages (concept/synthesis/entity); 1563 chunks hidden by default |
| 5990 edges | All shown but `related` thin grey, `cites` bold blue; toggle reveals only typed edges |
| 1468 communities | Top 8 (131, 66, 39, 11, 11, 8, 7, 4 pages) get distinct colors; long tail grey |
| 1746 / 1750 with `hub_score` | Node size ∝ `sqrt(hub_score)` |

Existing schema fields used: `pages.community_id` + `pages.hub_score` (written nightly by `wiki_graph_analytics` notebook since v0.7.0).
