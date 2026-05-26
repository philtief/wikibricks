import { useSearchParams } from "react-router-dom";

import { FilterSidebar } from "../components/FilterSidebar";
import { GraphCanvas } from "../components/GraphCanvas";
import { useGraphLoader } from "../lib/useGraphLoader";
import { useGraphStore } from "../lib/graphStore";

const ALLOWED_DEPTH = new Set([0, 1, 2, 3]);

export function IndexRoute() {
  useGraphLoader();
  const [searchParams, setSearchParams] = useSearchParams();
  const loading = useGraphStore((s) => s.loading);
  const error = useGraphStore((s) => s.error);
  const nodes = useGraphStore((s) => s.nodes);

  const focus = searchParams.get("focus") || undefined;
  const depthParam = Number.parseInt(searchParams.get("depth") || "2", 10);
  const depth = ALLOWED_DEPTH.has(depthParam) ? depthParam : 2;

  function handleNodeClick(nodeId: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("focus", nodeId);
      next.set("depth", String(depth));
      return next;
    });
  }

  function clearFocus() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("focus");
      next.delete("depth");
      return next;
    });
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <FilterSidebar />
      <main className="flex-1 relative overflow-hidden">
        <header className="absolute top-0 left-0 right-0 z-10 bg-white/80 backdrop-blur px-4 py-2 flex items-center justify-between border-b border-gray-200">
          <div className="text-sm">
            <span className="font-semibold">WikiBricks Graph</span>
            <span className="ml-3 text-gray-500">{nodes.length} pages</span>
            {focus && (
              <span className="ml-3 px-2 py-0.5 bg-amber-100 text-amber-800 rounded text-xs">
                Focus: <code>{focus}</code> (depth {depth})
                <button
                  onClick={clearFocus}
                  className="ml-2 underline text-amber-700 hover:text-amber-900"
                >
                  clear
                </button>
              </span>
            )}
          </div>
          <a
            href="/queue"
            className="text-xs text-blue-600 hover:underline"
          >
            Proposed-edges queue →
          </a>
        </header>
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/50 z-20">
            <div className="text-gray-600">Loading graph…</div>
          </div>
        )}
        {error && (
          <div className="absolute inset-x-0 top-12 z-20 bg-red-50 text-red-700 px-4 py-2 text-sm">
            Failed to load graph: {error}
          </div>
        )}
        <div className="pt-10 h-full">
          <GraphCanvas
            focus={focus}
            depth={depth}
            onNodeClick={handleNodeClick}
            width={window.innerWidth - 256 /* sidebar */}
            height={window.innerHeight - 40 /* header */}
          />
        </div>
      </main>
    </div>
  );
}
