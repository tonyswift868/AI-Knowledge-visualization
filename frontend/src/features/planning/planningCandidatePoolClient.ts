import { candidateKey, type PlanningCandidate } from "./PlanningCandidatePool"

export type PlanningPoolPersistedState = {
  planning_session_id: string
  revision?: number
}

/**
 * Keep every request for a durable pool session on one settled promise tail.
 *
 * The planning assistant is mounted in more than one surface (and a session
 * can be restored while another surface is still mounted), so a queue local
 * to a component is not sufficient.  Keying the tail by session id keeps
 * discovery, paging, and explicit decisions in the same order even when they
 * originate from different views.  A caller without a session should pass a
 * workspace-scoped key until the first response creates one.
 */
const requestTails = new Map<string, Promise<unknown>>()

export function enqueuePlanningPoolRequest<T>(
  sessionKey: string,
  task: () => Promise<T> | T,
): Promise<T> {
  const key = String(sessionKey || "planning-pool:anonymous")
  const previous = requestTails.get(key) || Promise.resolve()
  const run = previous.then(task, task)
  const settled = run.then(() => undefined, () => undefined)
  requestTails.set(key, settled)
  void settled.finally(() => {
    if (requestTails.get(key) === settled) requestTails.delete(key)
  })
  return run
}

export function planningPoolSessionQueueKey(workspaceId: string, planningSessionId?: string): string {
  const session = String(planningSessionId || "").trim()
  return session ? `session:${session}` : `workspace:${String(workspaceId || "general-learning")}`
}

/** Return the revision only when it moves the durable client forward. */
export function monotonicPlanningPoolRevision(
  current: number | undefined,
  incoming: unknown,
): number | undefined {
  const next = Number(incoming)
  if (!Number.isFinite(next)) return current
  if (Number.isFinite(current) && next < Number(current)) return current
  return next
}

const STORAGE_PREFIX = "mentorflow_planning_candidate_pool_v1:"

export function planningPoolStorageKey(workspaceId: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(String(workspaceId || "general-learning"))}`
}

export function readPlanningPoolState(workspaceId: string): PlanningPoolPersistedState | null {
  try {
    const value = localStorage.getItem(planningPoolStorageKey(workspaceId))
    if (!value) return null
    const parsed = JSON.parse(value)
    const session = String(parsed?.planning_session_id || parsed?.planningSessionId || "").trim()
    if (!session) return null
    const revision = Number(parsed?.revision)
    return Number.isFinite(revision)
      ? { planning_session_id: session, revision }
      : { planning_session_id: session }
  } catch {
    return null
  }
}

export function writePlanningPoolState(workspaceId: string, state: PlanningPoolPersistedState | null): void {
  try {
    const key = planningPoolStorageKey(workspaceId)
    if (!state?.planning_session_id) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(state))
  } catch {
    // A private browsing context may deny localStorage; server state remains durable.
  }
}

export function clientRequestId(prefix = "pool"): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  return `${prefix}:${uuid || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
}

export function isPlanningPoolStaleError(error: unknown): boolean {
  const value = error as {
    code?: unknown
    message?: unknown
    detail?: unknown
    response?: { data?: unknown }
  } | null
  const codes = [
    value?.code,
    (value?.detail as { code?: unknown } | null)?.code,
    (value?.response?.data as { code?: unknown } | null)?.code,
  ].map(item => String(item || "").trim().toUpperCase())
  if (codes.includes("STALE_CURSOR") || codes.includes("STALE_REVISION")) return true
  const message = [
    value?.message,
    value?.detail,
    value?.response?.data,
  ].map(item => typeof item === "string" ? item : JSON.stringify(item || "")).join(" ")
  return /planning resource pool (?:cursor|revision) is stale/i.test(message)
}

export function planningDiscoveryMode(
  message: string,
  _hasProtectedCandidates: boolean,
  hasExistingSession: boolean,
): "append" | "replace_unselected" {
  // Selection protects candidates; it must not silently change the learner's
  // discovery intent. “继续找/再补充” appends another page, while an explicit
  // replacement/refinement request swaps only unselected rows.
  if (!hasExistingSession) return "append"
  const keepRequested = /(?:(?:不要|别|无需|不必|勿)\s*(?:替换|换掉|换走|重找|重新找)|(?:保留|继续使用)\s*(?:当前|这些|原有|已选)?)/i.test(message)
  if (keepRequested) return "append"
  // "再找几个配套笔记" is an explicit refinement: retain protected rows,
  // but replace the unselected discovery rows instead of appending them.
  const replaceRequested = /(?:再找(?:几个|一些|一份|点)?|换(?:一批|掉|个|份)?|替换|重新找|重找|不满意|不要这|replace|different|another\s+batch)/i.test(message)
  return replaceRequested ? "replace_unselected" : "append"
}

export function candidateServerId(item: PlanningCandidate | undefined, key = ""): string {
  const explicit = String(item?.candidate_id || item?.stable_id || item?.id || "").trim()
  if (explicit) return explicit
  if (key.startsWith("stable:")) return key.slice("stable:".length)
  if (key.startsWith("id:")) return key.slice("id:".length)
  return key
}

export function selectionKeys(
  values: unknown,
  candidates: PlanningCandidate[],
): string[] {
  if (!Array.isArray(values)) return []
  return values.map((value: any) => {
    if (typeof value === "string" && /^(stable|url|id):/.test(value)) return value
    if (typeof value === "string" && /^https?:/i.test(value)) {
      const item = candidates.find(candidate => String(candidate.url || "") === value)
      return item ? candidateKey(item) : `url:${value.toLowerCase()}`
    }
    const item = typeof value === "object" ? value : candidates.find(candidate => candidateServerId(candidate) === String(value))
    return candidateKey(item || { candidate_id: String(value), url: "" })
  }).filter(Boolean)
}

/**
 * Build the durable context used to refine public-material discovery.
 *
 * The anchor is deliberately a bounded metadata object rather than a copied
 * File/DOM object. This keeps local paths, credentials and browser-only state
 * out of the request while preserving enough provenance for the server to
 * search around the learner's exact source. The precedence is intentional:
 * exact learner reference, uploaded material, selected candidate, objective.
 */
export function buildPlanningSearchAnchor(options: {
  message?: string
  objective?: string
  selectedResources?: Array<Record<string, unknown> | undefined>
  uploadedMaterial?: Record<string, unknown> | File | null
} = {}): Record<string, unknown> {
  const selected = (options.selectedResources || []).filter(Boolean) as Array<Record<string, unknown>>
  const text = String(options.message || "").trim()
  const objective = String(options.objective || text || "").trim()
  const isExact = (item: Record<string, unknown>) => item.direct_user_reference === true
    || Boolean(item.direct_identifier)
    || Boolean(item.reference_resolution)
    || Boolean(item.exact_user_reference)

  // Prefer an explicit selected reference, but also recognize a URL directly
  // pasted in the current user turn before the server has returned its card.
  const exact = selected.find(isExact)
  const messageUrl = text.match(/https?:\/\/[^\s<>"']+/i)?.[0]?.replace(/[),.;!?]+$/, "") || ""
  if (exact || messageUrl) {
    return planningSearchAnchorFromMetadata(exact || { url: messageUrl, direct_user_reference: true }, "exact_reference", objective)
  }

  const uploaded = options.uploadedMaterial
  if (uploaded) {
    const file = uploaded as File
    const metadata: Record<string, unknown> = typeof File !== "undefined" && uploaded instanceof File
      ? { filename: file.name, title: file.name, media_type: file.type, content_label: file.name }
      : { ...uploaded as Record<string, unknown> }
    return planningSearchAnchorFromMetadata(metadata, "uploaded_material", objective)
  }

  const candidate = [...selected].sort((left, right) => candidateConfidence(right) - candidateConfidence(left))[0]
  if (candidate) return planningSearchAnchorFromMetadata(candidate, "selected_candidate", objective)
  return {
    kind: "objective",
    source: "objective",
    objective,
    title: objective,
    topic: objective,
    query: objective,
  }
}

function candidateConfidence(item: Record<string, unknown>): number {
  const values = [item.confidence, item.confidence_score, item.score, item.relevance_score, item.rank_score, item.authority_score]
  for (const value of values) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  const rank = Number(item.rank)
  return Number.isFinite(rank) ? -rank : 0
}

function planningSearchAnchorFromMetadata(
  item: Record<string, unknown>,
  kind: string,
  objective: string,
): Record<string, unknown> {
  // Explicit allow-list: browser File paths, auth material and provider cache
  // internals are intentionally not copied into the discovery contract.
  const allowed = [
    "id", "material_id", "upload_id", "candidate_id", "stable_id", "identity", "url", "canonical_url", "title", "label",
    "filename", "original_filename", "content_label", "content_type", "media_kind", "media_type",
    "source_kind", "source_type", "coverage_role", "collection", "collection_name", "snippet", "reason",
    "rank_reason", "rank_reasons", "authority", "authority_status", "authority_signal", "authority_explanation",
    "popularity", "popularity_status", "popularity_signal", "confidence", "confidence_score", "score",
    "relevance_score", "rank_score", "direct_identifier", "reference_resolution", "direct_user_reference",
  ]
  const anchor: Record<string, unknown> = { kind, source: kind, objective }
  for (const key of allowed) {
    const value = item[key]
    if (value === undefined || value === null || value === "") continue
    if (typeof value === "string" && value.length > 1600) anchor[key] = value.slice(0, 1600)
    else anchor[key] = value
  }
  if (!anchor.filename && anchor.original_filename) anchor.filename = anchor.original_filename
  // Do not forward arbitrary provider/search metadata. A small object can
  // still contain nested request headers, session handles or other secrets
  // whose parent key looks harmless. The explicit allow-list above is the
  // complete public search-anchor contract.
  // Keep common server consumers stable even when an uploaded object supplied
  // only a filename/content label and no explicit title.
  if (!anchor.title) anchor.title = anchor.content_label || anchor.filename || objective
  if (!anchor.content_label && (anchor.title || anchor.filename)) anchor.content_label = anchor.title || anchor.filename
  const genericTitle = /^(?:b站|bilibili)?\s*视频\s*(?:bv|av)[\w.-]*$/i.test(String(anchor.title || "").trim())
  // ``topic`` is what discovery should search around. Keep the exact URL and
  // identifier intact, but do not let a provider's placeholder title (for
  // example a bare BV label) become the only refinement topic.
  anchor.topic = kind === "exact_reference" && genericTitle && objective
    ? objective
    : String(anchor.title || anchor.content_label || objective || "").trim()
  if (kind === "exact_reference" && genericTitle && objective) anchor.search_title = objective
  return anchor
}

export { candidateKey } from "./PlanningCandidatePool"
