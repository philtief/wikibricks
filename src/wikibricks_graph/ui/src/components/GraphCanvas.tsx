import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import { buildCommunityPalette, colorFor } from "../lib/colors";
import { nodeRadius, useForceLayout } from "../lib/forceLayout";
import { useGraphStore, visibleEdges, visibleNodes } from "../lib/graphStore";
import { neighborhoodAtDepth } from "../lib/egoNetwork";
import type { EdgeOut } from "../lib/types";

import { NodeCard, type NodeCardData } from "./NodeCard";

const nodeTypes = { wikiNode: NodeCard };

export interface GraphCanvasProps {
  focus?: string;
  depth?: number;
  onNodeClick?: (nodeId: string) => void;
  width?: number;
  height?: number;
}

const EDGE_STYLES: Record<string, { stroke: string; strokeWidth: number; strokeDasharray?: string }> = {
  cites:       { stroke: "#2563eb", strokeWidth: 1.5 },                            // blue
  extends:     { stroke: "#16a34a", strokeWidth: 1.5 },                            // green
  contradicts: { stroke: "#dc2626", strokeWidth: 1.5 },                            // red
  supersedes:  { stroke: "#7c3aed", strokeWidth: 1.5 },                            // purple
  related:     { stroke: "#9ca3af", strokeWidth: 0.5, strokeDasharray: "3 3" },    // grey dashed
};

function edgeStyleFor(kind: string) {
  return EDGE_STYLES[kind] ?? EDGE_STYLES.related;
}

export function GraphCanvas({
  focus,
  depth = 2,
  onNodeClick,
  width = 1200,
  height = 800,
}: GraphCanvasProps) {
  const rawNodes = useGraphStore((s) => s.nodes);
  const rawEdges = useGraphStore((s) => s.edges);
  const filters = useGraphStore((s) => s.filters);

  // Filter to visible nodes (community/type/search) + ego subgraph (if focus set)
  const { nodes: filteredNodes, edges: filteredEdges } = useMemo(() => {
    let nodes = visibleNodes(rawNodes, filters);
    if (focus) {
      const inEgo = neighborhoodAtDepth(focus, depth, rawEdges);
      nodes = nodes.filter((n) => inEgo.has(n.id));
    }
    const visibleNodeIds = new Set(nodes.map((n) => n.id));
    const edges = visibleEdges(rawEdges, visibleNodeIds, filters.showOnlyTypedEdges);
    return { nodes, edges };
  }, [rawNodes, rawEdges, filters, focus, depth]);

  // Community palette
  const palette = useMemo(() => {
    const freq = new Map<number, number>();
    for (const n of filteredNodes) {
      if (n.community_id !== null) {
        freq.set(n.community_id, (freq.get(n.community_id) ?? 0) + 1);
      }
    }
    return buildCommunityPalette(freq);
  }, [filteredNodes]);

  // Force-layout positions
  const positioned = useForceLayout(filteredNodes, filteredEdges, { width, height });

  // Convert to React Flow nodes/edges
  const rfNodes: Node[] = useMemo(() => {
    return positioned.map((p) => {
      const data: NodeCardData = {
        node: p.data,
        color: colorFor(p.data.community_id, palette),
        radius: nodeRadius(p.data),
        isFocused: p.data.id === focus,
      };
      return {
        id: p.id,
        type: "wikiNode",
        position: { x: p.x, y: p.y },
        data: { data },
        draggable: true,
      };
    });
  }, [positioned, palette, focus]);

  const rfEdges: Edge[] = useMemo(() => {
    return filteredEdges.map((e: EdgeOut, idx) => ({
      id: `${e.source}->${e.target}-${idx}`,
      source: e.source,
      target: e.target,
      style: edgeStyleFor(e.kind),
      data: { kind: e.kind, weight: e.weight },
    }));
  }, [filteredEdges]);

  return (
    <div style={{ width, height }} className="bg-slate-50">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.1}
        maxZoom={4}
        onNodeClick={(_, node) => onNodeClick?.(node.id)}
      >
        <Background />
        <Controls />
        <MiniMap nodeColor={(n: Node) => {
          const data = (n.data as { data?: NodeCardData })?.data;
          return data?.color ?? "#9ca3af";
        }} />
      </ReactFlow>
    </div>
  );
}
