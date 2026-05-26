import { describe, expect, it } from "vitest";
import { nodeRadius, runForceLayout } from "../forceLayout";
import type { EdgeOut, NodeOut } from "../types";

const n = (id: string, hub_score: number | null = null): NodeOut => ({
  id, label: id, community_id: null, hub_score, page_type: "concept",
  tags: [], in_degree: 0, out_degree: 0,
});
const e = (s: string, t: string): EdgeOut => ({
  source: s, target: t, kind: "related", weight: 1,
});

describe("runForceLayout", () => {
  it("returns one PositionedNode per input node", () => {
    const nodes = [n("a"), n("b"), n("c")];
    const result = runForceLayout(nodes, [e("a", "b"), e("b", "c")]);
    expect(result).toHaveLength(3);
    expect(result.map((p) => p.id).sort()).toEqual(["a", "b", "c"]);
  });

  it("produces finite positions (no NaN/Infinity) for connected graph", () => {
    const nodes = [n("a"), n("b"), n("c")];
    const result = runForceLayout(nodes, [e("a", "b"), e("b", "c")]);
    for (const p of result) {
      expect(Number.isFinite(p.x)).toBe(true);
      expect(Number.isFinite(p.y)).toBe(true);
    }
  });

  it("produces finite positions for disconnected components", () => {
    const nodes = [n("a"), n("b"), n("c"), n("d")];
    const edges = [e("a", "b"), e("c", "d")]; // two components
    const result = runForceLayout(nodes, edges);
    for (const p of result) {
      expect(Number.isFinite(p.x)).toBe(true);
      expect(Number.isFinite(p.y)).toBe(true);
    }
  });

  it("produces finite positions for isolated nodes", () => {
    const nodes = [n("a"), n("b"), n("c")];
    const result = runForceLayout(nodes, []); // no edges
    for (const p of result) {
      expect(Number.isFinite(p.x)).toBe(true);
      expect(Number.isFinite(p.y)).toBe(true);
    }
  });

  it("empty input returns empty array", () => {
    expect(runForceLayout([], [])).toEqual([]);
  });

  it("attaches original node data", () => {
    const node = n("a", 0.05);
    const [pos] = runForceLayout([node], []);
    expect(pos.data).toBe(node);
  });

  it("skips edges whose endpoints aren't in the node set", () => {
    // simulation shouldn't crash on ghost edges
    const nodes = [n("a"), n("b")];
    const result = runForceLayout(nodes, [e("a", "b"), e("a", "ghost")]);
    expect(result).toHaveLength(2);
    for (const p of result) expect(Number.isFinite(p.x)).toBe(true);
  });
});

describe("nodeRadius", () => {
  it("minimum radius for no hub_score", () => {
    expect(nodeRadius(n("a", null))).toBe(12);
  });

  it("scales up with hub_score", () => {
    const small = nodeRadius(n("a", 0.001));
    const big = nodeRadius(n("a", 0.05));
    expect(big).toBeGreaterThan(small);
  });

  it("caps the radius for very high hub_score", () => {
    // hub_score >>> what's plausible — should still be finite + bounded
    const huge = nodeRadius(n("a", 1.0));
    expect(huge).toBeGreaterThan(12);
    expect(huge).toBeLessThanOrEqual(40);
    expect(Number.isFinite(huge)).toBe(true);
  });
});
