import { useMemo } from "react";

import { useGraphStore } from "../lib/graphStore";

import { Toggle } from "./Toggle";

const TOP_COMMUNITIES_TO_SHOW = 12;

export function FilterSidebar() {
  const nodes = useGraphStore((s) => s.nodes);
  const filters = useGraphStore((s) => s.filters);
  const toggleCommunity = useGraphStore((s) => s.toggleCommunity);
  const togglePageType = useGraphStore((s) => s.togglePageType);
  const setShowOnlyTypedEdges = useGraphStore((s) => s.setShowOnlyTypedEdges);
  const setIncludeChunks = useGraphStore((s) => s.setIncludeChunks);
  const setSearch = useGraphStore((s) => s.setSearch);
  const clearFilters = useGraphStore((s) => s.clearFilters);

  // Top-N communities by frequency
  const topCommunities = useMemo(() => {
    const freq = new Map<number, number>();
    for (const n of nodes) {
      if (n.community_id !== null) {
        freq.set(n.community_id, (freq.get(n.community_id) ?? 0) + 1);
      }
    }
    return Array.from(freq.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, TOP_COMMUNITIES_TO_SHOW);
  }, [nodes]);

  // All page_types present
  const pageTypes = useMemo(() => {
    const set = new Set<string>();
    for (const n of nodes) {
      if (n.page_type) set.add(n.page_type);
    }
    return Array.from(set).sort();
  }, [nodes]);

  return (
    <aside className="w-64 h-full bg-white border-r border-gray-200 p-4 flex flex-col gap-4 overflow-y-auto text-sm">
      <div>
        <h2 className="font-semibold mb-2 text-gray-800">Search</h2>
        <input
          type="text"
          value={filters.search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="title, path, or tag..."
          className="w-full px-2 py-1 border border-gray-300 rounded text-sm"
        />
      </div>

      <div>
        <h2 className="font-semibold mb-2 text-gray-800">Edges</h2>
        <Toggle
          checked={filters.showOnlyTypedEdges}
          onChange={setShowOnlyTypedEdges}
          label="Show only typed edges (hide 'related')"
        />
      </div>

      <div>
        <h2 className="font-semibold mb-2 text-gray-800">Chunks</h2>
        <Toggle
          checked={filters.includeChunks}
          onChange={setIncludeChunks}
          label="Include chunks (parent pages with subordinate text)"
        />
      </div>

      {pageTypes.length > 0 && (
        <div>
          <h2 className="font-semibold mb-2 text-gray-800">Page type</h2>
          <div className="flex flex-wrap gap-1">
            {pageTypes.map((pt) => {
              const active = filters.pageTypes.has(pt);
              return (
                <button
                  key={pt}
                  onClick={() => togglePageType(pt)}
                  className={`px-2 py-0.5 rounded text-xs border ${
                    active
                      ? "bg-blue-100 border-blue-400 text-blue-800"
                      : "bg-gray-50 border-gray-200 text-gray-700"
                  }`}
                >
                  {pt}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {topCommunities.length > 0 && (
        <div>
          <h2 className="font-semibold mb-2 text-gray-800">
            Communities (top {topCommunities.length})
          </h2>
          <div className="flex flex-col gap-1">
            {topCommunities.map(([id, count]) => {
              const active = filters.communities.has(id);
              return (
                <button
                  key={id}
                  onClick={() => toggleCommunity(id)}
                  className={`flex items-center justify-between px-2 py-0.5 rounded text-xs border ${
                    active
                      ? "bg-blue-100 border-blue-400 text-blue-800"
                      : "bg-gray-50 border-gray-200 text-gray-700"
                  }`}
                >
                  <span>community #{id}</span>
                  <span className="opacity-70">{count}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <button
        onClick={clearFilters}
        className="mt-auto px-3 py-1.5 text-xs border border-gray-300 rounded hover:bg-gray-50 self-start"
      >
        Clear all filters
      </button>
    </aside>
  );
}
