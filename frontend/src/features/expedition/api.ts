export type ExpeditionProfile = {
  profile_id: string
  training_exp: number
  crystals: number
  total_verified_minutes: number
  current_stage: number
  active_lineup_id?: string | null
  revision: number
  heroes_owned: number
  training_slots: Array<{ slot_id: string; level: number; spent_exp: number; max_level: number }>
  gacha_state?: Record<string, number>
}

export type Hero = {
  hero_id: string
  name: string
  rarity: string
  role: string
  tags: string[]
  asset_id: string
  owned: boolean
  stars: number
  weakness?: string
  portrait_url?: string
  skill_cut_in_url?: string
  skill_icon_url?: string
  base_stats?: Record<string, number>
  active_skill?: { skill_id: string; name: string; summary: string }
  passive_one?: { skill_id: string; name: string; summary: string }
  passive_two?: { skill_id: string; name: string; summary: string }
  awakening?: { skill_id: string; name: string; summary: string }
  assets?: Record<AssetKind, ExpeditionAsset>
  fallback_used?: boolean
}

export type AssetKind = "portrait" | "avatar" | "skill_cutin" | "skill_icon"
export type ExpeditionAsset = { asset_id: string; url: string; width: number; height: number; focal_point: { x: number; y: number }; scale: number; offset: { x: number; y: number }; fallback_used: boolean; version: string }

export type ArtPackVersion = { pack_id: string; version: string; status: string; installed_at?: string | null; activated_at?: string | null; manifest_hash: string; hero_count: number; source: string; validation_result?: { assets?: Array<{ hero_id: string; asset_kind: AssetKind; path: string; width: number; height: number; mime: string; sha256: string }> }; reviewed_assets: number; required_reviews: number; previous_active_pack_id?: string; previous_active_version?: string; reviews?: Array<{ hero_id: string; asset_kind: AssetKind; review_status: string; review_note: string; reviewed_at?: string | null }> }

export type LineupMember = { slot_id: string; hero_id: string }
export type Lineup = {
  lineup_id: string
  name: string
  stance: string
  active: boolean
  revision: number
  members: LineupMember[]
}

export type GachaBanner = {
  banner_id: string
  banner_type: "character" | "equipment"
  name: string
  cost_single: number
  cost_ten: number
  pity_limit: number
  config?: Record<string, unknown>
}

export type Stage = {
  stage_number: number
  seed: number
  chapter: number
  theme: string
  affix: string
  enemy_ids: string[]
  boss?: { boss_id: string; name: string; mechanic: string; strategies: string[] } | null
  rewards: Record<string, unknown>
}

export type BattleEvent = {
  event_index: number
  event_type: string
  actor_id: string
  target_ids: string[]
  source_skill_id?: string | null
  numeric_values?: Record<string, number>
  status_ids?: string[]
  is_critical?: boolean
  is_miss?: boolean
  animation_hint?: string
  timestamp_offset?: number
}

export type BattleSummary = {
  battle_id: string
  stage_number: number
  winner: string
  event_count?: number
  result?: { winner?: string; rounds?: number; units?: Record<string, unknown> }
}

async function expeditionRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData
  const response = await fetch(`/api/expedition${path}`, {
    headers: isForm ? { ...(init?.headers || {}) } : { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = body?.error || body?.detail?.error || body?.detail
    throw new Error(error?.message || body?.message || "远征请求失败")
  }
  return body as T
}

export const expeditionApi = {
  profile: () => expeditionRequest<{ profile: ExpeditionProfile } | ExpeditionProfile>("/profile"),
  ledger: () => expeditionRequest<{ items: Array<Record<string, unknown>> }>("/ledger"),
  heroes: () => expeditionRequest<{ items: Hero[] }>("/heroes"),
  hero: async (heroId: string) => {
    const payload = await expeditionRequest<{ item: Hero }>(`/heroes/${encodeURIComponent(heroId)}`)
    return payload.item
  },
  trainingSlots: () => expeditionRequest<{ items: ExpeditionProfile["training_slots"] }>("/training-slots"),
  upgradeSlot: (slotId: string) => expeditionRequest<{ profile: ExpeditionProfile } | ExpeditionProfile>(`/training-slots/${slotId}/upgrade`, { method: "POST", body: JSON.stringify({ levels: 1 }) }),
  lineups: () => expeditionRequest<{ items: Lineup[] }>("/lineups"),
  saveLineup: (body: { lineup_id?: string; name: string; stance: string; members: LineupMember[]; expected_revision?: number }) => expeditionRequest<{ item: Lineup }>(body.lineup_id ? `/lineups/${body.lineup_id}` : "/lineups", { method: body.lineup_id ? "PUT" : "POST", body: JSON.stringify(body) }),
  activateLineup: (lineupId: string) => expeditionRequest<{ item: Lineup }>(`/lineups/${lineupId}/activate`, { method: "POST", body: "{}" }),
  banners: () => expeditionRequest<{ items: GachaBanner[] }>("/gacha/banners"),
  pull: (bannerId: string, count: 1 | 10) => expeditionRequest<{ results: Array<Record<string, unknown>>; cost: number; crystals: number }>("/gacha/pull", { method: "POST", body: JSON.stringify({ banner_id: bannerId, count, idempotency_key: `ui-${bannerId}-${count}-${Date.now()}-${Math.random().toString(36).slice(2)}` }) }),
  history: () => expeditionRequest<{ items: Array<Record<string, unknown>> }>("/gacha/history"),
  equipment: () => expeditionRequest<{ definitions: Array<Record<string, unknown>>; instances: Array<Record<string, unknown>> }>("/equipment"),
  pets: () => expeditionRequest<{ definitions: Array<Record<string, unknown>>; owned: Array<Record<string, unknown>> }>("/pets"),
  stages: () => expeditionRequest<{ current_stage: number; current: Stage; infinite: boolean; rules: string }>("/stages/current"),
  stage: (stageNumber: number) => expeditionRequest<Stage>(`/stages/${stageNumber}`),
  challenge: (stageNumber: number) => expeditionRequest<BattleSummary & { stage: Stage }>(`/stages/${stageNumber}/challenge`, { method: "POST", body: "{}" }),
  battleEvents: (battleId: string) => expeditionRequest<{ events: BattleEvent[] }>(`/battles/${encodeURIComponent(battleId)}/events`),
  milestones: () => expeditionRequest<{ items: Array<{ milestone_id: string; label: string; required_minutes: number; claimed: boolean }>; total_verified_minutes: number }>("/milestones"),
  claimMilestone: (milestoneId: string) => expeditionRequest<Record<string, unknown>>(`/milestones/${encodeURIComponent(milestoneId)}/claim`, { method: "POST", body: "{}" }),
  heroAssets: (heroId: string) => expeditionRequest<{ hero_id: string; assets: Record<AssetKind, ExpeditionAsset>; fallback_used: boolean }>(`/heroes/${encodeURIComponent(heroId)}/assets`),
  artPacks: () => expeditionRequest<{ items: ArtPackVersion[]; fallback: { active: boolean } }>("/art-packs"),
  activeArtPack: () => expeditionRequest<{ active: ArtPackVersion | null; fallback: boolean }>("/art-packs/active"),
  previewArtPack: (file: File) => { const body = new FormData(); body.append("file", file); return expeditionRequest<Record<string, unknown>>("/art-packs/preview", { method: "POST", body }) },
  installArtPack: (previewId: string, confirm = true) => expeditionRequest<{ item: ArtPackVersion }>("/art-packs/install", { method: "POST", body: JSON.stringify({ preview_id: previewId, confirm }) }),
  activateArtPack: (packId: string, version: string) => expeditionRequest<{ item: ArtPackVersion }>(`/art-packs/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/activate`, { method: "POST" }),
  rollbackArtPack: (packId: string, version: string) => expeditionRequest<{ item: { active: ArtPackVersion | null; fallback: boolean } }>(`/art-packs/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/rollback`, { method: "POST" }),
  reviewArtAsset: (packId: string, version: string, heroId: string, kind: AssetKind, reviewStatus: "APPROVED" | "NEEDS_REDO" | "PENDING", reviewNote = "") => expeditionRequest<{ item: ArtPackVersion }>(`/art-packs/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/reviews/${encodeURIComponent(heroId)}/${kind}`, { method: "POST", body: JSON.stringify({ review_status: reviewStatus, review_note: reviewNote }) }),
}

export function unwrapProfile(payload: { profile: ExpeditionProfile } | ExpeditionProfile): ExpeditionProfile {
  return "profile" in payload ? payload.profile : payload
}
