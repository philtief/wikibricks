import { useEffect } from "react";

import { fetchGraph } from "./api";
import { useGraphStore } from "./graphStore";

/**
 * Fetches /api/graph once on mount (and again when `includeChunks` flips).
 * Writes the result into Zustand. Errors are recorded on the store; the
 * UI shows the message banner.
 */
export function useGraphLoader() {
  const includeChunks = useGraphStore((s) => s.filters.includeChunks);
  const etag = useGraphStore((s) => s.etag);
  const setGraph = useGraphStore((s) => s.setGraph);
  const setLoading = useGraphStore((s) => s.setLoading);
  const setError = useGraphStore((s) => s.setError);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchGraph({ includeChunks, etag: etag ?? undefined })
      .then((data) => {
        if (cancelled) return;
        if (data === null) {
          // 304 — server says nothing changed, keep existing store data
          setLoading(false);
          return;
        }
        setGraph({
          nodes: data.nodes,
          edges: data.edges,
          etag: data.etag,
          generated_at: data.generated_at,
        });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeChunks]); // intentionally exclude etag — only re-fetch on chunks toggle
}
