export type LinkType = "related" | "cites" | "extends" | "contradicts" | "supersedes";
export type ProposedStatus = "pending" | "confirmed" | "rejected";

export interface NodeOut {
  id: string;
  label: string;
  community_id: number | null;
  hub_score: number | null;
  page_type: string | null;
  tags: string[];
  in_degree: number;
  out_degree: number;
}

export interface EdgeOut {
  source: string;
  target: string;
  kind: LinkType | string;
  weight: number;
}

export interface GraphOut {
  nodes: NodeOut[];
  edges: EdgeOut[];
  generated_at: string;
  etag: string;
}

export interface ProposedEdgeOut {
  proposal_id: string;
  source_path: string;
  target_path: string;
  link_type: LinkType | string;
  evidence: string;
  confidence: number | null;
  status: ProposedStatus | string;
}

export interface PageDetail {
  path: string;
  title: string;
  page_type: string | null;
  tags: string[];
  summary: string;
  body: string;
  community_id: number | null;
  hub_score: number | null;
}

export interface FilterState {
  communities: Set<number>;   // empty == all
  pageTypes: Set<string>;     // empty == all
  showOnlyTypedEdges: boolean;
  includeChunks: boolean;
  search: string;
}
