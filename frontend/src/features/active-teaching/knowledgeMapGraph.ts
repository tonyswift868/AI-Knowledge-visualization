import dagre from "@dagrejs/dagre";

export type KnowledgeGraphNodeLike = {
  id: string;
  label?: string;
  summary?: string;
  layer?: string;
  kind?: string;
  reviewStatus?: string;
  [key: string]: unknown;
};

export type KnowledgeGraphEdgeLike = {
  id?: string;
  source: string;
  target: string;
  relation?: string;
  reviewStatus?: string;
  [key: string]: unknown;
};

/**
 * Pre-indexed theme labels keyed by the concept node they contain.  A map is
 * accepted here so callers rendering a large graph can build this once and
 * reuse it for subsequent queries; the search helper also accepts raw edges
 * for the small/normal call site.
 */
export type KnowledgeGraphSearchContext = ReadonlyMap<string, readonly string[] | string>;

export type KnowledgeGraphPosition = {
  x: number;
  y: number;
};

const text = (value: unknown) => String(value ?? "").trim().toLocaleLowerCase();

const nodeKind = (node: KnowledgeGraphNodeLike) => text(node.kind || node.node_type || node.type);
const nodeLabel = (node: KnowledgeGraphNodeLike) => text(node.label || node.name || node.title);

const asStringList = (value: unknown): string[] => {
  if (Array.isArray(value)) return value.flatMap(asStringList);
  if (typeof value === "string" || typeof value === "number") {
    const valueText = text(value);
    return valueText ? [valueText] : [];
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return asStringList(record.id ?? record.node_id ?? record.theme_id ?? record.themeId ?? record.label ?? record.name ?? record.title);
  }
  return [];
};

const asCanonicalStringList = (value: unknown): string[] => {
  if (Array.isArray(value)) return value.flatMap(asCanonicalStringList);
  if (typeof value === "string" || typeof value === "number") {
    const valueText = String(value).trim();
    return valueText ? [valueText] : [];
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return asCanonicalStringList(record.id ?? record.node_id ?? record.theme_id ?? record.themeId ?? record.label ?? record.name ?? record.title);
  }
  return [];
};

const canonicalThemeMembershipIds = (node: KnowledgeGraphNodeLike): string[] => {
  const records = [node, node.raw].filter((value): value is Record<string, unknown> => Boolean(value && typeof value === "object"));
  // These aliases cover the canonical payload and older snapshots without
  // treating arbitrary node metadata as a relationship.
  const fields = [
    "theme_id", "themeId", "theme_ids", "themeIds", "theme_node_id", "themeNodeId",
    "parent_theme_id", "parentThemeId", "parent_theme_ids", "parentThemeIds",
    "parent_theme_node_id", "parentThemeNodeId", "parent_id", "parentId",
    "theme", "themes", "parent_theme", "parentTheme",
  ];
  const ids: string[] = [];
  records.forEach((record) => fields.forEach((field) => {
    if (!(field in record)) return;
    ids.push(...asCanonicalStringList(record[field]));
  }));
  return Array.from(new Set(ids));
};

/** Build an O(nodes + edges) index of parent theme labels for each node. */
export function buildKnowledgeMapSearchContext(
  nodes: KnowledgeGraphNodeLike[],
  edges: KnowledgeGraphEdgeLike[] = [],
): KnowledgeGraphSearchContext {
  const byId = new Map(nodes.map((node) => [String(node.id), node]));
  const labelsByNodeId = new Map<string, string[]>();
  const add = (nodeId: string, label: string) => {
    if (!byId.has(nodeId) || !label) return;
    const labels = labelsByNodeId.get(nodeId) || [];
    if (!labels.includes(label)) labels.push(label);
    labelsByNodeId.set(nodeId, labels);
  };

  // Canonical membership fields are useful when a provider has already
  // materialized the parent relationship on the concept node.
  nodes.forEach((node) => {
    const parentIds = canonicalThemeMembershipIds(node);
    parentIds.forEach((parentId) => {
      const parent = byId.get(parentId);
      if (parent && nodeKind(parent) === "theme") add(String(node.id), nodeLabel(parent));
    });
  });

  // The v1 graph contract uses theme --contains--> concept.  Keep the index
  // graph-local and fail closed for missing/unknown endpoints.
  edges.forEach((edge) => {
    if (text(edge.relation || edge.type || edge.relationship) !== "contains") return;
    const source = byId.get(String(edge.source));
    const target = byId.get(String(edge.target));
    if (!source || !target) return;
    if (nodeKind(source) === "theme") add(String(target.id), nodeLabel(source));
    else if (nodeKind(target) === "theme") add(String(source.id), nodeLabel(target));
  });
  return labelsByNodeId;
}

const searchContextFor = (
  nodes: KnowledgeGraphNodeLike[],
  input?: KnowledgeGraphEdgeLike[] | KnowledgeGraphSearchContext,
): KnowledgeGraphSearchContext => Array.isArray(input)
  ? buildKnowledgeMapSearchContext(nodes, input)
  : input || new Map<string, readonly string[]>();

/**
 * Search is deliberately local and allocation-light: each token must occur in
 * the node label, summary, or layer/kind/theme context. This keeps large maps
 * responsive while making a parent theme name useful without network or
 * semantic search.
 */
export function searchKnowledgeMapNodes<T extends KnowledgeGraphNodeLike>(
  nodes: T[],
  query: string,
  edgesOrContext?: KnowledgeGraphEdgeLike[] | KnowledgeGraphSearchContext,
): T[] {
  const tokens = text(query).split(/\s+/).filter(Boolean);
  if (!tokens.length) return [];
  const themeContext = searchContextFor(nodes, edgesOrContext);
  const scored = nodes.map((node, index) => {
    const label = nodeLabel(node);
    const summary = text(node.summary || node.description || node.explanation);
    const context = [node.layer, node.kind, node.reviewStatus].map(text).join(" ");
    const parentThemeLabels = asStringList(themeContext.get(String(node.id)));
    const themeContextText = parentThemeLabels.join(" ");
    const haystack = `${label} ${summary} ${context} ${themeContextText}`;
    if (!tokens.every((token) => haystack.includes(token))) return null;
    let labelScore = 0;
    let contextScore = 0;
    for (const token of tokens) {
      if (label === token) labelScore += 1000;
      else if (label.startsWith(token)) labelScore += 500;
      else if (label.includes(token)) labelScore += 300;
      else if (summary.includes(token)) labelScore += 120;
      else if (themeContextText.includes(token)) contextScore += 40;
      else contextScore += 5;
    }
    // An exact/label match on a theme is intentionally ahead of concepts that
    // inherit the same theme through a contains edge. Stable input order still
    // breaks ties, including multiple concepts under one theme.
    const themeLabelPriority = nodeKind(node) === "theme" && labelScore > 0 ? 10_000 : 0;
    return { node, score: themeLabelPriority + labelScore + contextScore, index };
  }).filter(Boolean) as Array<{ node: T; score: number; index: number }>;
  return scored.sort((left, right) => right.score - left.score || left.index - right.index).map((item) => item.node);
}

const canonicalPrerequisite = (edge: KnowledgeGraphEdgeLike) => {
  const relation = text(edge.relation).replace(/[ -]/g, "_");
  if (!["prerequisite", "prerequisites", "depends_on", "depends", "requires", "required_before"].includes(relation)) return false;
  const status = text(edge.reviewStatus);
  // Empty, unknown, and legacy statuses are not proof that a prerequisite
  // was reviewed. Only explicit reviewed statuses may enter the path.
  return ["approved", "confirmed", "accepted"].includes(status);
};

export type KnowledgeLearningPath<T extends KnowledgeGraphNodeLike> = {
  upstream: T[];
  current: T | null;
  downstream: T[];
};

/** Resolve confirmed canonical prerequisite ancestors and dependants. */
export function buildKnowledgeLearningPath<T extends KnowledgeGraphNodeLike>(
  nodes: T[],
  edges: KnowledgeGraphEdgeLike[],
  currentId: string | null | undefined,
): KnowledgeLearningPath<T> {
  const byId = new Map(nodes.map((node) => [String(node.id), node]));
  const canonicalEdges = edges.filter(canonicalPrerequisite);
  const upstreamByTarget = new Map<string, string[]>();
  const downstreamBySource = new Map<string, string[]>();
  for (const edge of canonicalEdges) {
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue;
    const upstream = upstreamByTarget.get(edge.target) || [];
    upstream.push(edge.source);
    upstreamByTarget.set(edge.target, upstream);
    const downstream = downstreamBySource.get(edge.source) || [];
    downstream.push(edge.target);
    downstreamBySource.set(edge.source, downstream);
  }
  const current = byId.get(String(currentId || "")) || null;
  if (!current) return { upstream: [], current: null, downstream: [] };
  const collect = (start: string, adjacency: Map<string, string[]>, topological = false) => {
    const seen = new Set<string>();
    const result: T[] = [];
    if (topological) {
      // Post-order traversal keeps every ancestor before its dependent while
      // preserving edge insertion order for deterministic branches.
      const visit = (id: string) => {
        if (id === start || seen.has(id)) return;
        seen.add(id);
        (adjacency.get(id) || []).forEach(visit);
        const node = byId.get(id);
        if (node) result.push(node);
      };
      (adjacency.get(start) || []).forEach(visit);
      return result;
    }
    const queue = [...(adjacency.get(start) || [])];
    while (queue.length) {
      const id = queue.shift()!;
      if (seen.has(id) || id === start) continue;
      seen.add(id);
      const node = byId.get(id);
      if (node) result.push(node);
      queue.push(...(adjacency.get(id) || []));
    }
    return result;
  };
  return {
    upstream: collect(current.id, upstreamByTarget, true),
    current,
    downstream: collect(current.id, downstreamBySource),
  };
}

/** A stable graph identity used to avoid relayout on filter/focus/selection. */
export function knowledgeMapGraphSignature(
  nodes: KnowledgeGraphNodeLike[],
  edges: KnowledgeGraphEdgeLike[],
  revision?: number | string | null,
) {
  const nodePart = nodes.map((node) => `${node.id}:${node.label || ""}`).join("|");
  const edgePart = edges.map((edge) => `${edge.id || ""}:${edge.source}>${edge.target}:${edge.relation || ""}`).join("|");
  return `${revision ?? "snapshot"}::${nodes.length}:${edges.length}::${nodePart}::${edgePart}`;
}

export function layoutKnowledgeMapGraph(
  nodes: KnowledgeGraphNodeLike[],
  edges: KnowledgeGraphEdgeLike[],
): Record<string, KnowledgeGraphPosition> {
  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "LR", nodesep: 50, ranksep: 100, marginx: 24, marginy: 24 });
  const width = 220;
  const height = 104;
  nodes.forEach((node) => graph.setNode(node.id, { width, height }));
  edges.forEach((edge) => graph.setEdge(edge.source, edge.target));
  dagre.layout(graph);
  return Object.fromEntries(nodes.map((node) => {
    const positioned = graph.node(node.id) || { x: width / 2, y: height / 2 };
    return [node.id, { x: positioned.x - width / 2, y: positioned.y - height / 2 }];
  }));
}

export class KnowledgeMapLayoutCache {
  private readonly entries = new Map<string, Record<string, KnowledgeGraphPosition>>();
  constructor(private readonly maxEntries = 6) {}
  get(signature: string) { return this.entries.get(signature); }
  set(signature: string, positions: Record<string, KnowledgeGraphPosition>) {
    this.entries.delete(signature);
    this.entries.set(signature, positions);
    while (this.entries.size > this.maxEntries) this.entries.delete(this.entries.keys().next().value!);
  }
  clear() { this.entries.clear(); }
}
