import { useState } from "react";

import type { ProposedEdgeOut } from "../lib/types";

interface Props {
  edge: ProposedEdgeOut;
  onApprove: (id: string) => Promise<void>;
  onReject: (id: string, reason: string) => Promise<void>;
}

const LINK_BADGE: Record<string, string> = {
  cites:       "bg-blue-100 text-blue-800",
  extends:     "bg-green-100 text-green-800",
  contradicts: "bg-red-100 text-red-800",
  supersedes:  "bg-purple-100 text-purple-800",
  related:     "bg-gray-100 text-gray-700",
};

export function ProposedEdgeRow({ edge, onApprove, onReject }: Props) {
  const [busy, setBusy] = useState(false);
  const [rejectMode, setRejectMode] = useState(false);
  const [reason, setReason] = useState("user-rejected");

  async function doApprove() {
    setBusy(true);
    try {
      await onApprove(edge.proposal_id);
    } finally {
      setBusy(false);
    }
  }

  async function doReject() {
    setBusy(true);
    try {
      await onReject(edge.proposal_id, reason || "user-rejected");
    } finally {
      setBusy(false);
      setRejectMode(false);
    }
  }

  return (
    <div className="bg-white rounded border border-gray-200 p-4 shadow-sm flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <code className="bg-gray-100 px-1 rounded">{edge.source_path}</code>
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            LINK_BADGE[edge.link_type] ?? LINK_BADGE.related
          }`}
        >
          {edge.link_type}
        </span>
        <span className="text-gray-400">→</span>
        <code className="bg-gray-100 px-1 rounded">{edge.target_path}</code>
        {edge.confidence !== null && (
          <span className="ml-auto text-xs text-gray-500">
            confidence: {edge.confidence.toFixed(2)}
          </span>
        )}
      </div>
      <blockquote className="text-xs text-gray-700 border-l-2 border-gray-300 pl-3 italic">
        {edge.evidence || "(no evidence)"}
      </blockquote>
      <div className="flex items-center gap-2 mt-1">
        {!rejectMode ? (
          <>
            <button
              onClick={doApprove}
              disabled={busy}
              className="px-3 py-1 text-xs bg-blue-600 text-white rounded disabled:opacity-50"
            >
              {busy ? "..." : "Approve"}
            </button>
            <button
              onClick={() => setRejectMode(true)}
              disabled={busy}
              className="px-3 py-1 text-xs border border-gray-300 rounded disabled:opacity-50"
            >
              Reject
            </button>
          </>
        ) : (
          <>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="reason..."
              className="px-2 py-1 text-xs border border-gray-300 rounded flex-1"
              autoFocus
            />
            <button
              onClick={doReject}
              disabled={busy}
              className="px-3 py-1 text-xs bg-red-600 text-white rounded disabled:opacity-50"
            >
              Confirm reject
            </button>
            <button
              onClick={() => setRejectMode(false)}
              disabled={busy}
              className="px-3 py-1 text-xs border border-gray-300 rounded"
            >
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  );
}
