import type { GraphOut, PageDetail, ProposedEdgeOut } from "./types";

const BASE = "/api";

async function _get<T>(path: string, headers: Record<string, string> = {}): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { headers });
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

async function _post(path: string, body?: unknown): Promise<void> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok && r.status !== 204) throw new Error(`POST ${path} → ${r.status}`);
}

export async function fetchGraph(
  opts: { includeChunks?: boolean; etag?: string } = {},
): Promise<GraphOut | null> {
  const params = new URLSearchParams();
  if (opts.includeChunks) params.set("include_chunks", "true");
  const path = `/graph${params.toString() ? `?${params}` : ""}`;
  const headers: Record<string, string> = opts.etag ? { "If-None-Match": opts.etag } : {};
  const r = await fetch(`${BASE}${path}`, { headers });
  if (r.status === 304) return null;
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

export async function refreshGraph(): Promise<void> {
  await _post("/graph/refresh");
}

export async function fetchPage(path: string): Promise<PageDetail> {
  return _get<PageDetail>(`/pages/${encodeURI(path)}`);
}

export async function listProposedEdges(): Promise<ProposedEdgeOut[]> {
  return _get<ProposedEdgeOut[]>(`/edges/proposed`);
}

export async function approveProposedEdge(proposalId: string): Promise<void> {
  await _post(`/edges/proposed/${encodeURIComponent(proposalId)}/approve`);
}

export async function rejectProposedEdge(proposalId: string, reason: string): Promise<void> {
  await _post(`/edges/proposed/${encodeURIComponent(proposalId)}/reject`, { reason });
}
