import { describe, expect, it } from "vitest";
import { neighborhoodAtDepth } from "../egoNetwork";

const e = (s: string, t: string) => ({ source: s, target: t, kind: "related", weight: 1 });

describe("neighborhoodAtDepth", () => {
  it("depth 0 returns only focus", () => {
    const r = neighborhoodAtDepth("a", 0, [e("a", "b")]);
    expect(Array.from(r).sort()).toEqual(["a"]);
  });

  it("depth 1 returns immediate neighbors", () => {
    const r = neighborhoodAtDepth("a", 1, [e("a", "b"), e("a", "c"), e("c", "d")]);
    expect(Array.from(r).sort()).toEqual(["a", "b", "c"]);
  });

  it("depth 2 expands one more hop", () => {
    const r = neighborhoodAtDepth("a", 2, [e("a", "b"), e("a", "c"), e("c", "d")]);
    expect(Array.from(r).sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("isolated focus returns singleton", () => {
    const r = neighborhoodAtDepth("z", 3, [e("a", "b")]);
    expect(Array.from(r)).toEqual(["z"]);
  });

  it("treats edges as undirected", () => {
    const r = neighborhoodAtDepth("b", 1, [e("a", "b")]);
    expect(Array.from(r).sort()).toEqual(["a", "b"]);
  });
});
