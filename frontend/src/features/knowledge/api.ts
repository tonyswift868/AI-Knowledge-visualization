export type KnowledgeSourceReference = {
  source_id: string
  chunk_id: string
  quote: string
  heading?: string
  page_start?: number | null
  page_end?: number | null
  content_hash?: string
}

export type KnowledgeNode = {
  node_id: string
  label: string
  summary?: string
  kind?: string
  status?: string
  revision?: number
  source_refs: KnowledgeSourceReference[]
}

export type KnowledgeEdge = {
  edge_id: string
  source_node_id: string
  target_node_id: string
  relation: string
  source_refs?: KnowledgeSourceReference[]
}

export type KnowledgeMapQuality = {
  node_count?: number
  edge_count?: number
  sourced_node_ratio?: number
  chunk_coverage_ratio?: number
  duplicate_node_ratio?: number
  orphan_count?: number
  invalid_prerequisite_count?: number
  superseded_node_count?: number
}

export type KnowledgeMapSnapshot = {
  map_id: string
  workspace_id: string
  revision: number
  generation_mode?: string
  provider_used?: boolean
  nodes: KnowledgeNode[]
  edges: KnowledgeEdge[]
  quality: KnowledgeMapQuality
  warnings?: string[]
}

export class KnowledgeApiError extends Error {
  code: string
  status: number
  details?: Record<string, unknown>

  constructor(message: string, code = 'KNOWLEDGE_MAP_REQUEST_FAILED', status = 0, details?: Record<string, unknown>) {
    super(message)
    this.name = 'KnowledgeApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

async function readResponse<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = body?.error
    throw new KnowledgeApiError(
      error?.message || `知识地图请求失败（HTTP ${response.status}）`,
      error?.code || 'KNOWLEDGE_MAP_REQUEST_FAILED',
      response.status,
      error?.details,
    )
  }
  return (body?.knowledge_map || body?.map || body) as T
}

export const knowledgeApi = {
  get: async (workspaceId: string, signal?: AbortSignal): Promise<KnowledgeMapSnapshot> => {
    const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/knowledge-map`, { signal })
    return readResponse<KnowledgeMapSnapshot>(response)
  },
  regenerate: async (workspaceId: string, signal?: AbortSignal): Promise<KnowledgeMapSnapshot> => {
    const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/knowledge-map/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preserve_previous_on_failure: true }),
      signal,
    })
    return readResponse<KnowledgeMapSnapshot>(response)
  },
}

export type KnowledgeApi = typeof knowledgeApi
