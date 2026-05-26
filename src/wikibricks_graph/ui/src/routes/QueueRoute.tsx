import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  approveProposedEdge,
  listProposedEdges,
  rejectProposedEdge,
} from "../lib/api";
import type { ProposedEdgeOut } from "../lib/types";
import { ProposedEdgeRow } from "../components/ProposedEdgeRow";

export function QueueRoute() {
  const [edges, setEdges] = useState<ProposedEdgeOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listProposedEdges();
      setEdges(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleApprove(id: string) {
    await approveProposedEdge(id);
    setEdges((prev) => prev.filter((e) => e.proposal_id !== id));
  }

  async function handleReject(id: string, reason: string) {
    await rejectProposedEdge(id, reason);
    setEdges((prev) => prev.filter((e) => e.proposal_id !== id));
  }

  return (
    <div className="h-screen w-screen overflow-y-auto bg-slate-50">
      <header className="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/" className="text-blue-600 hover:underline text-sm">
            ← Graph
          </Link>
          <h1 className="text-lg font-semibold">Proposed edges queue</h1>
          <span className="text-sm text-gray-500">{edges.length} pending</span>
        </div>
        <button
          onClick={refresh}
          className="text-xs px-3 py-1 border border-gray-300 rounded hover:bg-gray-50"
        >
          Refresh
        </button>
      </header>
      <main className="max-w-3xl mx-auto p-6 flex flex-col gap-3">
        {loading && <div className="text-gray-500">Loading…</div>}
        {error && (
          <div className="bg-red-50 text-red-700 px-3 py-2 rounded text-sm">
            Failed to load: {error}
          </div>
        )}
        {!loading && !error && edges.length === 0 && (
          <div className="text-gray-500 text-sm">No pending proposed edges.</div>
        )}
        {edges.map((edge) => (
          <ProposedEdgeRow
            key={edge.proposal_id}
            edge={edge}
            onApprove={handleApprove}
            onReject={handleReject}
          />
        ))}
      </main>
    </div>
  );
}
