import { describe, expect, it } from "vitest";
import type { EdgeOut, NodeOut } from "../types";
import { useGraphStore, visibleEdges, visibleNodes } from "../graphStore";

const n = (id: string, extras: Partial<NodeOut> = {}): NodeOut => ({
  id, label: id, community_id: null, hub_score: null,
  page_type: "concept", tags: [], in_degree: 0, out_degree: 0,
  ...extras,
});

describe("visibleNodes", () => {
  it("returns all nodes when filters empty", () => {
    const nodes = [n("a"), n("b")];
    const result = visibleNodes(nodes, {
      communities: new Set(), pageTypes: new Set(),
      showOnlyTypedEdges: false, includeChunks: false, search: "",
    });
    expect(result).toHaveLength(2);
  });

  it("filters by community", () => {
    const nodes = [n("a", { community_id: 1 }), n("b", { community_id: 2 })];
    const result = visibleNodes(nodes, {
      communities: new Set([1]), pageTypes: new Set(),
      showOnlyTypedEdges: false, includeChunks: false, search: "",
    });
    expect(result.map((x) => x.id)).toEqual(["a"]);
  });

  it("filters by page_type", () => {
    const nodes = [n("a", { page_type: "concept" }), n("b", { page_type: "synthesis" })];
    const result = visibleNodes(nodes, {
      communities: new Set(), pageTypes: new Set(["synthesis"]),
      showOnlyTypedEdges: false, includeChunks: false, search: "",
    });
    expect(result.map((x) => x.id)).toEqual(["b"]);
  });

  it("filters by search (label/id/tags substring, case-insensitive)", () => {
    const nodes = [n("topics/foo", { label: "Stripe webhook", tags: ["domain:payments"] }),
                   n("topics/bar", { label: "Lakeflow Job", tags: [] })];
    const r1 = visibleNodes(nodes, {
      communities: new Set(), pageTypes: new Set(),
      showOnlyTypedEdges: false, includeChunks: false, search: "stripe",
    });
    expect(r1.map((x) => x.id)).toEqual(["topics/foo"]);
    const r2 = visibleNodes(nodes, {
      communities: new Set(), pageTypes: new Set(),
      showOnlyTypedEdges: false, includeChunks: false, search: "payments",
    });
    expect(r2.map((x) => x.id)).toEqual(["topics/foo"]);
  });
});

describe("visibleEdges", () => {
  const edges: EdgeOut[] = [
    { source: "a", target: "b", kind: "related", weight: 1 },
    { source: "a", target: "c", kind: "cites", weight: 1 },
  ];

  it("drops edges whose endpoint isn't visible", () => {
    const visible = new Set(["a", "b"]);
    expect(visibleEdges(edges, visible, false).map((e) => e.target)).toEqual(["b"]);
  });

  it("filters out 'related' when showOnlyTyped is true", () => {
    const visible = new Set(["a", "b", "c"]);
    expect(visibleEdges(edges, visible, true).map((e) => e.target)).toEqual(["c"]);
  });
});

describe("useGraphStore actions", () => {
  it("setGraph replaces nodes + edges + etag", () => {
    useGraphStore.setState({
      nodes: [], edges: [], etag: null, generatedAt: null,
      loading: false, error: null,
      filters: { communities: new Set(), pageTypes: new Set(),
                 showOnlyTypedEdges: false, includeChunks: false, search: "" },
    });
    useGraphStore.getState().setGraph({
      nodes: [n("a")], edges: [], etag: "abc", generated_at: "2026-05-26T00:00:00Z",
    });
    expect(useGraphStore.getState().etag).toBe("abc");
    expect(useGraphStore.getState().nodes).toHaveLength(1);
  });

  it("toggleCommunity adds and removes", () => {
    useGraphStore.getState().clearFilters();
    useGraphStore.getState().toggleCommunity(5);
    expect(useGraphStore.getState().filters.communities.has(5)).toBe(true);
    useGraphStore.getState().toggleCommunity(5);
    expect(useGraphStore.getState().filters.communities.has(5)).toBe(false);
  });
});
