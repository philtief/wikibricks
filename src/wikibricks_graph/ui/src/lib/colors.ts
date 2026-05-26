import { schemeTableau10 } from "d3-scale-chromatic";

/**
 * Deterministic community → color. Top-N most-frequent communities get
 * distinct palette entries; everything else gets the grey fallback.
 */
const FALLBACK = "#9ca3af"; // tailwind gray-400
const PALETTE = schemeTableau10 as readonly string[];

export function buildCommunityPalette(
  communityFrequencies: ReadonlyMap<number, number>,
  topN: number = PALETTE.length,
): ReadonlyMap<number, string> {
  // Sort communities by frequency desc, take top-N, assign palette colors
  const sorted = Array.from(communityFrequencies.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, topN);
  const map = new Map<number, string>();
  sorted.forEach(([communityId], i) => {
    map.set(communityId, PALETTE[i % PALETTE.length]);
  });
  return map;
}

export function colorFor(
  communityId: number | null,
  palette: ReadonlyMap<number, string>,
): string {
  if (communityId === null) return FALLBACK;
  return palette.get(communityId) ?? FALLBACK;
}
