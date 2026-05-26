import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { useEffect, useRef, useState } from "react";

import type { EdgeOut, NodeOut } from "./types";

export interface PositionedNode {
  id: string;
  x: number;
  y: number;
  data: NodeOut;
}

interface InternalNode extends SimulationNodeDatum {
  id: string;
  data: NodeOut;
  radius: number;
}

interface InternalLink extends SimulationLinkDatum<InternalNode> {
  source: string | InternalNode;
  target: string | InternalNode;
}

/**
 * Pure function (no React) — runs the simulation synchronously for
 * `tickCount` iterations and returns positioned nodes. Exposed so unit
 * tests can verify positions stabilize without a DOM.
 */
export function runForceLayout(
  nodes: ReadonlyArray<NodeOut>,
  edges: ReadonlyArray<EdgeOut>,
  opts: { tickCount?: number; width?: number; height?: number } = {},
): PositionedNode[] {
  const tickCount = opts.tickCount ?? 200;
  const width = opts.width ?? 1200;
  const height = opts.height ?? 800;

  const internalNodes: InternalNode[] = nodes.map((n) => ({
    id: n.id,
    data: n,
    radius: nodeRadius(n),
  }));
  const idSet = new Set(internalNodes.map((n) => n.id));
  // Skip edges whose endpoints aren't in the visible node set
  const internalLinks: InternalLink[] = edges
    .filter((e) => idSet.has(e.source) && idSet.has(e.target))
    .map((e) => ({ source: e.source, target: e.target }));

  const simulation = forceSimulation<InternalNode>(internalNodes)
    .force("charge", forceManyBody<InternalNode>().strength(-200))
    .force(
      "link",
      forceLink<InternalNode, InternalLink>(internalLinks).id((n) => n.id).distance(60),
    )
    .force("center", forceCenter(width / 2, height / 2))
    .force("collide", forceCollide<InternalNode>().radius((n) => n.radius + 4).strength(0.7))
    .stop();

  for (let i = 0; i < tickCount; i++) simulation.tick();

  return internalNodes.map((n) => ({
    id: n.id,
    x: Number.isFinite(n.x) ? (n.x as number) : width / 2,
    y: Number.isFinite(n.y) ? (n.y as number) : height / 2,
    data: n.data,
  }));
}

/**
 * Map a node's hub_score to a circle radius. Sqrt-scaled so high-PageRank
 * outliers don't dwarf the rest. Range: 12px (no score) to ~40px (max).
 */
export function nodeRadius(n: NodeOut): number {
  const score = n.hub_score ?? 0;
  // sqrt scale: clamp to [0,1], then 12..40
  const t = Math.sqrt(Math.max(0, Math.min(1, score * 20))); // hub_score is usually 0..0.05; *20 normalizes
  return 12 + t * 28;
}

/**
 * React hook: runs the simulation in an effect, updates positions over
 * animation frames until alpha drops below ALPHA_MIN. Returns the
 * current positioned nodes.
 *
 * For unit tests, prefer `runForceLayout` directly — this hook needs a
 * DOM / requestAnimationFrame.
 */
const ALPHA_MIN = 0.05;

export function useForceLayout(
  nodes: ReadonlyArray<NodeOut>,
  edges: ReadonlyArray<EdgeOut>,
  opts: { width?: number; height?: number } = {},
): PositionedNode[] {
  const [positions, setPositions] = useState<PositionedNode[]>([]);
  const rafRef = useRef<number | null>(null);
  const simRef = useRef<Simulation<InternalNode, InternalLink> | null>(null);

  useEffect(() => {
    if (nodes.length === 0) {
      setPositions([]);
      return;
    }

    const width = opts.width ?? 1200;
    const height = opts.height ?? 800;

    const internalNodes: InternalNode[] = nodes.map((n) => ({
      id: n.id, data: n, radius: nodeRadius(n),
    }));
    const idSet = new Set(internalNodes.map((n) => n.id));
    const internalLinks: InternalLink[] = edges
      .filter((e) => idSet.has(e.source) && idSet.has(e.target))
      .map((e) => ({ source: e.source, target: e.target }));

    const sim = forceSimulation<InternalNode>(internalNodes)
      .force("charge", forceManyBody<InternalNode>().strength(-200))
      .force(
        "link",
        forceLink<InternalNode, InternalLink>(internalLinks).id((n) => n.id).distance(60),
      )
      .force("center", forceCenter(width / 2, height / 2))
      .force("collide", forceCollide<InternalNode>().radius((n) => n.radius + 4).strength(0.7));
    simRef.current = sim;

    function tick() {
      sim.tick();
      setPositions(
        internalNodes.map((n) => ({
          id: n.id,
          x: Number.isFinite(n.x) ? (n.x as number) : width / 2,
          y: Number.isFinite(n.y) ? (n.y as number) : height / 2,
          data: n.data,
        })),
      );
      if (sim.alpha() > ALPHA_MIN) {
        rafRef.current = requestAnimationFrame(tick);
      }
    }
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      sim.stop();
    };
  }, [nodes, edges, opts.width, opts.height]);

  return positions;
}
