import { describe, expect, it } from "vitest";
import { buildCommunityPalette, colorFor } from "../colors";

describe("buildCommunityPalette", () => {
  it("assigns distinct colors to top-N most-frequent communities", () => {
    const freq = new Map([[1, 100], [2, 50], [3, 10], [4, 5]]);
    const palette = buildCommunityPalette(freq, 3);
    expect(palette.size).toBe(3);
    expect(palette.has(1)).toBe(true);
    expect(palette.has(2)).toBe(true);
    expect(palette.has(3)).toBe(true);
    expect(palette.has(4)).toBe(false);
  });

  it("colorFor returns palette hue for known community, fallback otherwise", () => {
    const palette = buildCommunityPalette(new Map([[1, 10], [2, 5]]), 2);
    expect(palette.get(1)).toMatch(/^#/);
    expect(colorFor(1, palette)).toBe(palette.get(1));
    expect(colorFor(999, palette)).toMatch(/^#/); // fallback grey
    expect(colorFor(null, palette)).toMatch(/^#/);
  });
});
