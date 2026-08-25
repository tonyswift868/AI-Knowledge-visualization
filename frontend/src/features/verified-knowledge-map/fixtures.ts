import type {
  KnowledgeEdge,
  KnowledgeGraphDiagnostics,
  KnowledgeMapDisplay,
  KnowledgeMapEnvelope,
  KnowledgeMapSnapshot,
  KnowledgeMapStatus,
  KnowledgeNode,
} from "./types";

export const LARGE_GRAPH_FIXTURE_SIZES = [50, 250, 1_000] as const;

function diagnostics(
  orphanConceptIds: string[],
  cycle = false,
): KnowledgeGraphDiagnostics {
  return {
    alias_collisions: [],
    rejected_relations: [],
    unresolved_relation_hints: [],
    cycles: cycle
      ? [{
        cycle_path: ["fixture-node-0", "fixture-node-1", "fixture-node-0"],
        involved_concept_ids: ["fixture-node-0", "fixture-node-1"],
        edge_evidence: [],
        resolution_required: true,
      }]
      : [],
    orphan_concept_ids: orphanConceptIds,
    disconnected_subgraphs: orphanConceptIds.map((item) => [item]),
    missing_evidence_concept_ids: [],
    unknown_evidence_ids: [],
    cycle_diagnostics_truncated: false,
    resolution_required: cycle,
  };
}

function displayFor(
  status: KnowledgeMapStatus,
  nodeCount: number,
  cycle: boolean,
): KnowledgeMapDisplay {
  if (cycle) {
    return {
      renderable: false,
      display_state: "CONFLICT",
      user_message: "发现知识依赖冲突，需要检查关系",
      is_partial: false,
      is_demo: false,
      demo_label: null,
    };
  }
  if (status === "FAILED") {
    return {
      renderable: false,
      display_state: "FAILED",
      user_message: "知识图验证失败，不能作为完整知识图显示",
      is_partial: false,
      is_demo: false,
      demo_label: null,
    };
  }
  if (status === "PARTIAL") {
    return {
      renderable: true,
      display_state: "PARTIAL",
      user_message: "资料尚未完全验证",
      is_partial: true,
      is_demo: false,
      demo_label: null,
    };
  }
  return {
    renderable: true,
    display_state: nodeCount ? "READY" : "EMPTY",
    user_message: nodeCount ? "知识图已完成验证" : "当前没有可显示的知识节点",
    is_partial: false,
    is_demo: false,
    demo_label: null,
  };
}

export function createKnowledgeMapFixture(
  nodeCount: number,
  options: {
    status?: KnowledgeMapStatus;
    cycle?: boolean;
  } = {},
): KnowledgeMapEnvelope {
  const count = Math.max(0, Math.floor(nodeCount));
  const status = options.cycle ? "FAILED" : options.status || "COMPLETE";
  const nodes: KnowledgeNode[] = Array.from({ length: count }, (_, index) => ({
    concept_id: `fixture-node-${index}`,
    canonical_name: `知识概念 ${index + 1}`,
    aliases: [`Concept ${index + 1}`],
    knowledge_type: index % 5 === 0 ? "MECHANISM" : "CONCEPT",
    evidence_ids: [`fixture-evidence-${index}`],
    confidence: 0.72 + (index % 25) / 100,
    verification_status: index % 23 === 0
      ? "VERIFIED_REPAIR_CANDIDATE"
      : "VERIFIED_COVERED",
    repair_lineage: index % 23 === 0
      ? [{
        patch_id: `fixture-patch-${index}`,
        stable_candidate_id: `fixture-candidate-${index}`,
        proposed_item_id: `fixture-node-${index}`,
        classification: "FIXTURE_REPAIR",
        verifier_version: "fixture-verifier.v1",
      }]
      : [],
    criticality: index % 17 === 0 ? "CRITICAL" : "NORMAL",
    effort_lookup_key: `fixture-effort-${index}`,
    dependency_hints: [],
    source_hints: [`fixture-source-${index % 5}`],
  }));
  const edges: KnowledgeEdge[] = [];
  for (let index = 1; index < count; index += 1) {
    edges.push({
      edge_id: `fixture-prerequisite-${index - 1}-${index}`,
      source_concept_id: `fixture-node-${index - 1}`,
      target_concept_id: `fixture-node-${index}`,
      edge_type: "PREREQUISITE",
      basis: "VERIFIED_EXPLICIT_RELATION",
      evidence_ids: [`fixture-edge-evidence-${index}`],
      confidence: 0.96,
    });
    if (index >= 2 && index % 2 === 0) {
      edges.push({
        edge_id: `fixture-supports-${index - 2}-${index}`,
        source_concept_id: `fixture-node-${index - 2}`,
        target_concept_id: `fixture-node-${index}`,
        edge_type: "SUPPORTS",
        basis: "VERIFIED_EXPLICIT_RELATION",
        evidence_ids: [`fixture-support-evidence-${index}`],
        confidence: 0.88,
      });
    }
    if (index >= 3 && index % 3 === 0) {
      edges.push({
        edge_id: `fixture-related-${index - 3}-${index}`,
        source_concept_id: `fixture-node-${index - 3}`,
        target_concept_id: `fixture-node-${index}`,
        edge_type: "RELATED",
        basis: "VERIFIED_EXPLICIT_RELATION",
        evidence_ids: [`fixture-related-evidence-${index}`],
        confidence: 0.79,
      });
    }
    if (index >= 7 && index % 7 === 0) {
      edges.push({
        edge_id: `fixture-possible-${index - 7}-${index}`,
        source_concept_id: `fixture-node-${index - 7}`,
        target_concept_id: `fixture-node-${index}`,
        edge_type: "POSSIBLE_PREREQUISITE",
        basis: "CANDIDATE_PREREQUISITE_HINT",
        evidence_ids: [`fixture-possible-evidence-${index}`],
        confidence: 0.58,
      });
    }
  }
  const orphanIds = count === 1 ? ["fixture-node-0"] : [];
  const graphDiagnostics = diagnostics(orphanIds, Boolean(options.cycle));
  const snapshot: KnowledgeMapSnapshot = {
    schema_version: "mentorflow.verified-knowledge-map.v2",
    compiler_version: "fixture-compiler.v1",
    snapshot_id: `fixture-snapshot-${count}-${status}-${options.cycle ? "cycle" : "dag"}`,
    source_identity: "fixture://verified-knowledge-map",
    source_revision: "fixture-v1",
    status,
    nodes,
    edges,
    topological_order: nodes.map((node) => node.concept_id),
    diagnostics: graphDiagnostics,
    receipt: {
      node_count: nodes.length,
      edge_count: edges.length,
    },
  };
  return {
    api_version: "fixture.knowledge-map-read-api.v1",
    snapshot,
    display: displayFor(status, count, Boolean(options.cycle)),
  };
}
