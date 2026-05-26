import type { EdgeOut } from "./types";

/**
 * BFS from `focus` up to `depth` hops over the undirected edge list.
 * Returns the set of reachable node IDs (including `focus` itself).
 * If `focus` isn't in any edge, returns `{focus}` (singleton).
 */
export function neighborhoodAtDepth(
  focus: string,
  depth: number,
  edges: ReadonlyArray<EdgeOut>,
): Set<string> {
  const adjacency = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
    if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
    adjacency.get(e.source)!.add(e.target);
    adjacency.get(e.target)!.add(e.source);
  }
  const visited = new Set<string>([focus]);
  let frontier = new Set<string>([focus]);
  for (let d = 0; d < depth; d++) {
    const next = new Set<string>();
    for (const node of frontier) {
      for (const neighbor of adjacency.get(node) ?? []) {
        if (!visited.has(neighbor)) {
          visited.add(neighbor);
          next.add(neighbor);
        }
      }
    }
    if (next.size === 0) break;
    frontier = next;
  }
  return visited;
}
