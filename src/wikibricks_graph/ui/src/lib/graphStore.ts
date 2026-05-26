import { create } from "zustand";
import type { EdgeOut, FilterState, NodeOut } from "./types";

interface GraphState {
  // Server data
  nodes: NodeOut[];
  edges: EdgeOut[];
  etag: string | null;
  generatedAt: string | null;
  loading: boolean;
  error: string | null;

  // Filters
  filters: FilterState;

  // Actions
  setGraph: (data: { nodes: NodeOut[]; edges: EdgeOut[]; etag: string; generated_at: string }) => void;
  setLoading: (loading: boolean) => void;
  setError: (err: string | null) => void;
  toggleCommunity: (id: number) => void;
  togglePageType: (t: string) => void;
  setShowOnlyTypedEdges: (v: boolean) => void;
  setIncludeChunks: (v: boolean) => void;
  setSearch: (q: string) => void;
  clearFilters: () => void;
}

const initialFilters: FilterState = {
  communities: new Set(),
  pageTypes: new Set(),
  showOnlyTypedEdges: false,
  includeChunks: false,
  search: "",
};

export const useGraphStore = create<GraphState>((set) => ({
  nodes: [],
  edges: [],
  etag: null,
  generatedAt: null,
  loading: false,
  error: null,
  filters: initialFilters,

  setGraph: (data) =>
    set({
      nodes: data.nodes,
      edges: data.edges,
      etag: data.etag,
      generatedAt: data.generated_at,
      loading: false,
      error: null,
    }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),

  toggleCommunity: (id) =>
    set((s) => {
      const next = new Set(s.filters.communities);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { filters: { ...s.filters, communities: next } };
    }),

  togglePageType: (t) =>
    set((s) => {
      const next = new Set(s.filters.pageTypes);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return { filters: { ...s.filters, pageTypes: next } };
    }),

  setShowOnlyTypedEdges: (v) =>
    set((s) => ({ filters: { ...s.filters, showOnlyTypedEdges: v } })),

  setIncludeChunks: (v) =>
    set((s) => ({ filters: { ...s.filters, includeChunks: v } })),

  setSearch: (q) => set((s) => ({ filters: { ...s.filters, search: q } })),

  clearFilters: () => set({ filters: initialFilters }),
}));

/** Pure helper: apply filters → return visible node IDs.
 *  Lives outside the store so it's easily testable and memoizable. */
export function visibleNodes(
  nodes: ReadonlyArray<NodeOut>,
  filters: FilterState,
): NodeOut[] {
  const search = filters.search.trim().toLowerCase();
  return nodes.filter((n) => {
    if (filters.communities.size > 0 && (n.community_id === null || !filters.communities.has(n.community_id)))
      return false;
    if (filters.pageTypes.size > 0 && (n.page_type === null || !filters.pageTypes.has(n.page_type)))
      return false;
    if (search) {
      const hay = `${n.label} ${n.id} ${n.tags.join(" ")}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });
}

/** Pure helper: filter edges to visible-node set, optionally typed-only. */
export function visibleEdges(
  edges: ReadonlyArray<EdgeOut>,
  visibleNodeIds: ReadonlySet<string>,
  showOnlyTyped: boolean,
): EdgeOut[] {
  return edges.filter((e) => {
    if (!visibleNodeIds.has(e.source) || !visibleNodeIds.has(e.target)) return false;
    if (showOnlyTyped && e.kind === "related") return false;
    return true;
  });
}
