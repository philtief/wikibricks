import { useEffect, useState } from "react";

import { fetchPage } from "../lib/api";
import { useGraphStore } from "../lib/graphStore";
import type { PageDetail } from "../lib/types";

interface Props {
  path: string;
  onClose: () => void;
  onNavigateTo: (nextPath: string) => void;
}

export function PageDetailDrawer({ path, onClose, onNavigateTo }: Props) {
  const [page, setPage] = useState<PageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const edges = useGraphStore((s) => s.edges);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPage(null);
    fetchPage(path)
      .then((p) => {
        if (!cancelled) {
          setPage(p);
          setLoading(false);
        }
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  // 1-hop neighbor paths derived from the global edge list
  const neighbors = (() => {
    const set = new Set<string>();
    for (const e of edges) {
      if (e.source === path) set.add(e.target);
      else if (e.target === path) set.add(e.source);
    }
    return Array.from(set).sort();
  })();

  return (
    <aside className="fixed right-0 top-0 bottom-0 w-96 bg-white border-l border-gray-200 shadow-lg overflow-y-auto z-30">
      <div className="flex items-center justify-between p-4 border-b border-gray-200 sticky top-0 bg-white">
        <h2 className="font-semibold text-sm truncate" title={path}>
          {page?.title || path}
        </h2>
        <button
          onClick={onClose}
          className="text-gray-500 hover:text-gray-800 text-xl leading-none"
          aria-label="Close"
        >
          ×
        </button>
      </div>
      <div className="p-4 text-sm flex flex-col gap-3">
        {loading && <div className="text-gray-500">Loading…</div>}
        {error && (
          <div className="bg-red-50 text-red-700 px-3 py-2 rounded text-xs">
            {error}
          </div>
        )}
        {page && (
          <>
            <div className="flex items-center gap-2 flex-wrap">
              {page.page_type && (
                <span className="px-2 py-0.5 rounded text-xs bg-gray-100">
                  {page.page_type}
                </span>
              )}
              {page.community_id !== null && (
                <span className="px-2 py-0.5 rounded text-xs bg-blue-100 text-blue-800">
                  community #{page.community_id}
                </span>
              )}
              {page.hub_score !== null && (
                <span className="px-2 py-0.5 rounded text-xs bg-amber-100 text-amber-800">
                  hub_score: {page.hub_score.toFixed(4)}
                </span>
              )}
            </div>
            <code className="text-xs text-gray-500 break-all">{page.path}</code>

            {page.tags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {page.tags.map((tag) => (
                  <span
                    key={tag}
                    className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-700"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}

            {page.summary && (
              <section>
                <h3 className="text-xs font-semibold uppercase text-gray-500 mb-1">
                  Summary
                </h3>
                <pre className="whitespace-pre-wrap text-xs bg-gray-50 p-2 rounded border border-gray-100">
                  {page.summary}
                </pre>
              </section>
            )}

            {neighbors.length > 0 && (
              <section>
                <h3 className="text-xs font-semibold uppercase text-gray-500 mb-1">
                  Neighbors ({neighbors.length})
                </h3>
                <div className="flex flex-wrap gap-1">
                  {neighbors.slice(0, 20).map((n) => (
                    <button
                      key={n}
                      onClick={() => onNavigateTo(n)}
                      className="text-xs px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-100 hover:bg-blue-100"
                      title={n}
                    >
                      {n.split("/").pop() || n}
                    </button>
                  ))}
                  {neighbors.length > 20 && (
                    <span className="text-xs text-gray-500">
                      +{neighbors.length - 20} more
                    </span>
                  )}
                </div>
              </section>
            )}

            {page.body && (
              <section>
                <h3 className="text-xs font-semibold uppercase text-gray-500 mb-1">
                  Body
                </h3>
                <pre className="whitespace-pre-wrap text-xs bg-gray-50 p-2 rounded border border-gray-100 max-h-64 overflow-y-auto">
                  {page.body.slice(0, 4000)}
                  {page.body.length > 4000 && "\n…(truncated)"}
                </pre>
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
