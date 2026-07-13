export type StoryCatalogResult = {
  catalog_id: string
  title?: string
  title_zh?: string
  record_tier: string
  story_pack_status?: string
  story_pack_id?: string | null
  genres?: string[]
  tags?: string[]
  narrative_signals?: string[]
  why_fit?: string
  curation_status?: string
  duration_days?: number[]
  match_score: number
  match_reasons: string[]
  task_shape_fit: number
  duration_fit: number
  coupling_fit: number
  narrative_structure: string
  relationship_engine: string
  interaction_style: string
  emotional_tone: string
  time_mechanic: string
  pack_status: 'ready' | 'PACK_REQUIRED'
  playable: boolean
}

export type StoryCatalogMatch = { results: StoryCatalogResult[]; task_profile?: Record<string, unknown>; count: number }

export async function matchStoryCatalog(taskProfile: Record<string, unknown>, workspaceId?: string): Promise<StoryCatalogMatch> {
  const response = await fetch('/api/story-catalog/match', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workspace_id: workspaceId, task_profile: taskProfile }) })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error?.message || '故事目录匹配失败')
  return { results: data.results || [], task_profile: data.task_profile || taskProfile, count: data.count || 0 }
}

export async function requestCuration(catalogId: string, workspaceId: string, taskProfile: Record<string, unknown>) {
  const response = await fetch(`/api/story-catalog/${encodeURIComponent(catalogId)}/curation-request`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workspace_id: workspaceId, task_profile: taskProfile }) })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error?.message || '策展请求失败')
  return data
}
