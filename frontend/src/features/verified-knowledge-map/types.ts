export type KnowledgeMapStatus = "COMPLETE" | "PARTIAL" | "FAILED";

export type KnowledgeEdgeType =
  | "PREREQUISITE"
  | "SUPPORTS"
  | "RELATED"
  | "POSSIBLE_PREREQUISITE";

export type VerificationStatus =
  | "VERIFIED_COVERED"
  | "VERIFIED_REPAIR_CANDIDATE";

export type NodeCriticality = "CRITICAL" | "NORMAL";

export type RepairLineage = {
  patch_id: string;
  stable_candidate_id: string;
  proposed_item_id: string;
  classification: string;
  verifier_version: string;
};

export type KnowledgeNode = {
  concept_id: string;
  canonical_name: string;
  aliases: string[];
  knowledge_type: string;
  evidence_ids: string[];
  confidence: number;
  verification_status: VerificationStatus;
  repair_lineage: RepairLineage[];
  criticality: NodeCriticality;
  effort_lookup_key: string | null;
  dependency_hints: string[];
  source_hints: string[];
};

export type KnowledgeEdge = {
  edge_id: string;
  source_concept_id: string;
  target_concept_id: string;
  edge_type: KnowledgeEdgeType;
  basis: string;
  evidence_ids: string[];
  confidence: number;
};

export type CycleDiagnostic = {
  cycle_path: string[];
  involved_concept_ids: string[];
  edge_evidence: Array<Record<string, unknown>>;
  resolution_required: boolean;
};

export type KnowledgeGraphDiagnostics = {
  alias_collisions: Array<Record<string, unknown>>;
  rejected_relations: Array<Record<string, unknown>>;
  unresolved_relation_hints: Array<Record<string, unknown>>;
  cycles: CycleDiagnostic[];
  orphan_concept_ids: string[];
  disconnected_subgraphs: string[][];
  missing_evidence_concept_ids: string[];
  unknown_evidence_ids: string[];
  cycle_diagnostics_truncated: boolean;
  resolution_required: boolean;
};

export type KnowledgeMapSnapshot = {
  schema_version: string;
  compiler_version: string;
  snapshot_id: string;
  source_identity: string;
  source_revision: string;
  status: KnowledgeMapStatus;
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
  topological_order: string[];
  diagnostics: KnowledgeGraphDiagnostics;
  receipt: Record<string, unknown>;
};

export type KnowledgeMapDisplay = {
  renderable: boolean;
  display_state: "READY" | "PARTIAL" | "FAILED" | "CONFLICT" | "EMPTY";
  user_message: string;
  is_partial: boolean;
  is_demo: boolean;
  demo_label: "DEMO DATA" | null;
};

export type KnowledgeMapEnvelope = {
  api_version: string;
  snapshot: KnowledgeMapSnapshot;
  display: KnowledgeMapDisplay;
};

export type SourceReference = {
  evidence_id: string;
  source_identity: string;
  source_reference: string;
  href: string | null;
  link_status: string;
};

export type ConceptDetail = {
  snapshot_id: string;
  concept: KnowledgeNode;
  evidence_count: number;
  source_references: SourceReference[];
  repair_lineage: RepairLineage[];
  prerequisites: Array<Pick<KnowledgeNode, "concept_id" | "canonical_name">>;
  dependents: Array<Pick<KnowledgeNode, "concept_id" | "canonical_name">>;
};

export type KnowledgeMapFilterMode =
  | "all"
  | "critical"
  | "uncertain"
  | "orphan";

export type KnowledgeMapFilters = {
  mode: KnowledgeMapFilterMode;
  source: string;
  verificationStatus: "all" | VerificationStatus;
};
