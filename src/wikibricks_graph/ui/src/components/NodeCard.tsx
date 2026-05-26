import { Handle, Position } from "@xyflow/react";
import { memo } from "react";

import type { NodeOut } from "../lib/types";

export interface NodeCardData {
  node: NodeOut;
  color: string;
  radius: number;
  isFocused?: boolean;
}

function NodeCardComponent({ data }: { data: { data: NodeCardData } }) {
  const { node, color, radius, isFocused } = data.data;
  const size = radius * 2;
  return (
    <div
      className="rounded-full flex items-center justify-center text-[10px] font-medium text-white shadow-sm"
      style={{
        width: size,
        height: size,
        backgroundColor: color,
        border: isFocused ? "2px solid #fbbf24" : "1px solid rgba(0,0,0,0.15)",
      }}
      title={`${node.label}\n${node.id}\ncommunity: ${node.community_id ?? "—"}\nhub_score: ${node.hub_score?.toFixed(4) ?? "—"}`}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <span className="truncate px-1 max-w-full">{shortLabel(node.label)}</span>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

function shortLabel(label: string): string {
  if (label.length <= 14) return label;
  return label.slice(0, 12) + "…";
}

export const NodeCard = memo(NodeCardComponent);
