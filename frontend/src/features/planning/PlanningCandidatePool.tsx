import { ArrowLeft, ArrowRight, Check, RotateCcw, X } from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import "./planning-candidate-pool.css"

export type CandidateRole = "anchor" | "support"
export type PlanningCandidate = {
  id?: string
  candidate_id?: string
  stable_id?: string
  url: string
  canonical_url?: string
  title?: string
  snippet?: string
  reason?: string
  rank_reason?: string
  rank_reasons?: string[]
  kind?: string
  media_kind?: string
  coverage_role?: string
  source_kind?: string
  source_type?: string
  media_type?: string
  collection?: string
  collection_name?: string
  authority?: string
  authority_status?: string
  authority_signal?: string
  popularity?: string | number
  popularity_status?: string
  popularity_signal?: string
  authority_explanation?: string
  selected?: boolean
  retained?: boolean
  direct_user_reference?: boolean
  direct_identifier?: { kind?: string; value?: string; match?: string }
  reference_resolution?: { status?: string; network_verified?: boolean; requires_user_confirmation?: boolean }
  [key: string]: unknown
}

/**
 * A provider/backend safety decision is authoritative.  The pool still keeps
 * a small defensive UI gate so an obviously blocked or off-topic row cannot
 * be selected while a stale/partial response is being rendered.  Unknown or
 * missing metadata remains selectable; this is deliberately not a URL/domain
 * allow-list and never replaces the server policy.
 */
export type CandidateSafety = { blocked: boolean; reason?: "unsafe" | "off_topic" }

const normalizeSafetyCode = (value: unknown): string => String(value ?? "")
  .trim()
  .toLowerCase()
  .replace(/[\s-]+/g, "_")

const unsafeBooleanKeys = [
  "unsafe", "disallowed", "ineligible", "adult_content", "explicit_content", "nsfw", "malware", "phishing",
]
const offTopicBooleanKeys = ["off_topic", "offTopic", "topic_mismatch", "topicMismatch", "irrelevant", "not_relevant"]
const safetyStatusKeys = [
  "safety_status", "safety", "moderation_status", "moderation", "policy_status", "eligibility_status", "eligibility",
  "review_status", "content_status", "content_rating", "risk_level", "risk",
]
const safetyFlagKeys = ["safety_flags", "safetyFlags", "policy_flags", "policyFlags", "moderation_flags", "moderationFlags", "flags"]
const offTopicStatusKeys = ["topic_status", "topic_relevance", "relevance_status"]

// These are provider policy codes, not display-text fragments. In particular,
// ``no_strong_unsafe_signal`` is a positive reason and must not match the
// unsafe token merely because it contains that word.
const unsafeCodes = new Set([
  "unsafe", "rejected", "blocked", "disallowed", "ineligible", "adult_content", "explicit_content", "nsfw", "malware",
  "phishing", "scam", "harmful", "illegal", "dangerous", "adult", "explicit", "unsafe_signal", "unsafe_signal_detected",
])
const offTopicCodes = new Set([
  "off_topic", "topic_mismatch", "mismatch_topic", "topic_inconsistent", "irrelevant", "not_relevant", "current_topic_relevance_gate",
])
const qualificationBlockedCodes = new Set(["blocked", "rejected"])
const qualificationRejectedCodes = new Set(["rejected"])

export const looksLikeVideoCandidate = (item: PlanningCandidate | any): boolean => {
  const url = String(item?.url || "")
  if (/\.(mp4|webm|mov|m4v|avi|mkv|m3u8)(?:$|[?#])/i.test(url)
    || /(?:youtube\.com\/(?:watch|playlist)|youtu\.be\/|bilibili\.com\/video\/|b23\.tv\/|vimeo\.com\/)/i.test(url)
    || /(?:space\.bilibili\.com\/\d+\/lists\/|youtube\.com\/playlist)/i.test(url)) return true
  const mediaKind = String(item?.media_kind || "").trim().toLowerCase()
  return mediaKind === "video" || mediaKind === "video_collection"
}

/** Media families that cannot become planning materials, even when a stale
 * handoff mislabels the item as a document. */
export const looksLikeDisallowedMediaCandidate = (item: PlanningCandidate | any): boolean => {
  if (looksLikeVideoCandidate(item)) return true
  const mediaFields = [item?.media_kind, item?.kind, item?.source_kind, item?.source_type, item?.media_type, item?.mime_type]
    .map(value => String(value || "").trim().toLowerCase())
    .filter(Boolean)
  if (mediaFields.some(value => /(?:audio|subtitle|transcript|caption|asr|sensevoice)/i.test(value))) return true
  const url = String(item?.url || item?.canonical_url || "")
  return /\.(?:mp3|wav|m4a|ogg|oga|aac|flac|srt|vtt|ass|ssa|lrc)(?:$|[?#])/i.test(url)
}

const codeValues = (value: unknown): string[] => {
  if (Array.isArray(value)) return value.flatMap(codeValues)
  if (value && typeof value === "object") return Object.values(value as Record<string, unknown>).flatMap(codeValues)
  const code = normalizeSafetyCode(value)
  return code ? [code] : []
}

const hasCode = (value: unknown, codes: Set<string>): boolean => codeValues(value).some(code => codes.has(code))

const reasonKinds = (value: unknown): { unsafe: boolean; offTopic: boolean } => codeValues(value).reduce<{ unsafe: boolean; offTopic: boolean }>(
  (result, code) => {
    // A dynamic provider reason such as ``unsafe:adult_content`` is still an
    // explicit policy code. Do not broaden this to arbitrary prose containing
    // the word "unsafe".
    const prefix = code.split(/[:=]/, 1)[0]
    if (unsafeCodes.has(code) || prefix === "unsafe") result.unsafe = true
    if (offTopicCodes.has(code) || prefix === "off_topic" || prefix === "topic_mismatch") result.offTopic = true
    return result
  },
  { unsafe: false, offTopic: false },
)

export const candidateSafety = (item: PlanningCandidate | any): CandidateSafety => {
  const directReference = item?.direct_user_reference === true
  // ``blocked`` is a qualification/relevance projection in the planning
  // contract, not a hard-safety verdict.  Keep it fail-closed, but classify
  // it as off-topic unless an explicit hard-safety signal is present below.
  const boolBlocked = item?.blocked === true
  const boolUnsafe = unsafeBooleanKeys.some(key => item?.[key] === true)
  const boolOffTopic = offTopicBooleanKeys.some(key => item?.[key] === true)
  const safetyStatusUnsafe = safetyStatusKeys.some(key => hasCode(item?.[key], unsafeCodes))
  const safetyFlagsUnsafe = safetyFlagKeys.some(key => hasCode(item?.[key], unsafeCodes))
  const statusOffTopic = offTopicStatusKeys.some(key => hasCode(item?.[key], offTopicCodes))
  const safetyReason = reasonKinds(item?.safety_reason)
  const blockReason = reasonKinds(item?.block_reason)

  // Explicit hard-safety policy always wins, including for a direct user
  // reference. Direct references only bypass the relevance gate below.
  if (boolUnsafe || safetyStatusUnsafe || safetyFlagsUnsafe || safetyReason.unsafe || blockReason.unsafe) {
    return { blocked: true, reason: "unsafe" }
  }

  // The planning pool is a document-only discovery surface. Videos, playlists
  // and collections are never selectable, including when a stale handoff or a
  // direct learner reference marks them as user-provided. This keeps the
  // planner from entering the subtitle/transcript flow at all.
  if (looksLikeDisallowedMediaCandidate(item)) {
    return { blocked: true, reason: "off_topic" }
  }

  const candidateUrl = String(item?.url || item?.canonical_url || "").trim()
  if (candidateUrl && !/^https:\/\//i.test(candidateUrl)) {
    return { blocked: true, reason: "off_topic" }
  }

  const qualificationStatusBlocked = hasCode(item?.qualification_status, qualificationBlockedCodes)
  const qualificationStatusRejected = hasCode(item?.qualification_status, qualificationRejectedCodes)
  const relevanceBlocked = boolBlocked || boolOffTopic || statusOffTopic || safetyReason.offTopic || blockReason.offTopic
  // Exact learner-supplied references are preserved for explicit review. The
  // backend never treats them as discovered recommendations, so stale query
  // relevance metadata must not silently erase the learner's chosen anchor.
  // A direct reference with an explicit hard ``blocked``/``rejected`` status
  // and no relevance marker remains blocked fail-closed.
  if (directReference && !qualificationStatusBlocked) return { blocked: false }
  if (directReference && !qualificationStatusRejected && qualificationStatusBlocked && relevanceBlocked) return { blocked: false }
  if (relevanceBlocked || qualificationStatusBlocked) return { blocked: true, reason: "off_topic" }
  return { blocked: false }
}

export const isCandidateBlocked = (item: PlanningCandidate | any): boolean => candidateSafety(item).blocked
export type CandidatePage = {
  page: number
  pageIndex?: number
  page_index?: number
  pageSize: number
  page_size?: number
  total?: number
  qualifiedCount?: number
  heldIncompleteCount?: number
  /** Number of qualified rows held back because the server could not fill a complete page. */
  shortfallCount?: number
  shortfall_count?: number
  /** Explicit server truth; omitted for legacy responses. */
  pageComplete?: boolean
  page_complete?: boolean
  newCandidateCount?: number
  directReferenceCount?: number
  hasMore: boolean
  has_more?: boolean
  nextCursor?: string
  next_cursor?: string
  previousCursor?: string
  previous_cursor?: string
  currentCursor?: string
  current_cursor?: string
  offset?: number
  planningSessionId?: string
  planning_session_id?: string
  revision?: number
  /** True when the server already returned one page and the UI must not slice it again. */
  serverPaged?: boolean
  server_paged?: boolean
  /** Stable identities in the page returned by the server. */
  currentItemIdentities?: string[]
  current_item_identities?: string[]
  mode?: "append" | "replace" | "server_paged"
}
export const candidateKey = (item: PlanningCandidate | any): string => {
  const stableId = String(item?.stable_id || item?.candidate_id || "").trim()
  if (stableId) return `stable:${stableId}`
  const canonical = String(item?.canonical_url || item?.url || "").trim().toLowerCase()
  if (canonical) return `url:${canonical}`
  const id = String(item?.id || "").trim()
  return id ? `id:${id}` : ""
}
export const pageCandidates = <T,>(items: T[], page: number, pageSize = 3): T[] => items.slice(Math.max(0, page) * Math.max(1, pageSize), (Math.max(0, page) + 1) * Math.max(1, pageSize))
export const pageCount = (total: number, pageSize = 3): number => Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, pageSize)))
/**
 * Return the next complete page boundary for an explicit refill request.
 *
 * ``total`` is the number of rows currently published by the server, while
 * ``qualifiedCount`` includes policy-qualified rows that may not be published
 * yet.  A held incomplete row is also part of the qualified pool, so include
 * it alongside the published total when the server exposes that projection.
 */
export const candidatePageFillTarget = (
  candidatePage: Partial<CandidatePage> | undefined,
  fallbackTotal = 0,
  fallbackPageSize = 3,
): number => {
  const parsedPageSize = Number(candidatePage?.pageSize ?? candidatePage?.page_size ?? fallbackPageSize)
  const pageSize = Number.isFinite(parsedPageSize) && parsedPageSize > 0
    ? parsedPageSize
    : Math.max(1, Number(fallbackPageSize) || 1)
  const parsedTotal = Number(candidatePage?.total)
  const publishedTotal = Number.isFinite(parsedTotal) && parsedTotal >= 0
    ? parsedTotal
    : Math.max(0, Number(fallbackTotal) || 0)
  const parsedQualified = Number(candidatePage?.qualifiedCount)
  const qualifiedCount = Number.isFinite(parsedQualified) && parsedQualified >= 0 ? parsedQualified : 0
  const parsedHeld = Number(candidatePage?.heldIncompleteCount)
  const heldIncompleteCount = Number.isFinite(parsedHeld) && parsedHeld >= 0 ? parsedHeld : 0
  const baseline = Math.max(1, publishedTotal, qualifiedCount, publishedTotal + heldIncompleteCount)
  return Math.ceil(baseline / pageSize) * pageSize
}
export function readCandidatePage(payload: any, fallbackOrPageSize: PlanningCandidate[] | number = 3, pageSize = 3): { candidates: PlanningCandidate[]; page: CandidatePage; session?: any; selection?: any } {
  const bundle = payload?.resource_bundle || payload?.resourceBundle || payload || {}
  const rawPage = bundle?.candidate_page || payload?.candidate_page || bundle?.candidatePage || payload?.candidatePage || {}
  const rawItems = rawPage?.candidates || rawPage?.items || rawPage?.results || bundle?.candidates || payload?.resources || []
  const incoming = Array.isArray(rawItems) ? rawItems : []
  const rawProtected = rawPage?.protected_items || rawPage?.protectedItems || []
  const protectedItems = Array.isArray(rawProtected) ? rawProtected : []
  // Some legacy/server responses expose the exact learner URL in a separate
  // lane. It is still a real card and must participate in the three-card page.
  const rawProvided = rawPage?.provided_items || rawPage?.providedItems || bundle?.provided_items || payload?.provided_items || []
  const providedItems = Array.isArray(rawProvided) ? rawProvided : []
  const pageIndexRaw = rawPage?.page_index ?? rawPage?.pageIndex ?? payload?.page_index ?? payload?.pageIndex
  const pageRaw = rawPage?.page ?? rawPage?.page_number ?? rawPage?.index ?? payload?.page ?? payload?.page_number
  const parsedPageIndex = pageIndexRaw === undefined || pageIndexRaw === null || pageIndexRaw === "" ? Number.NaN : Number(pageIndexRaw)
  const parsedPageNumber = pageRaw === undefined || pageRaw === null || pageRaw === "" ? Number.NaN : Number(pageRaw)
  const pageBase = Number(rawPage?.page_base ?? payload?.page_base)
  const requestedSize = typeof fallbackOrPageSize === "number" ? fallbackOrPageSize : pageSize
  const parsedSize = Number(rawPage?.page_size ?? rawPage?.pageSize ?? payload?.page_size ?? payload?.pageSize ?? requestedSize)
  const size = Number.isFinite(parsedSize) && parsedSize > 0 ? parsedSize : pageSize
  const totalValue = Number(rawPage?.total ?? rawPage?.total_count ?? rawPage?.count ?? payload?.total ?? payload?.total_count)
  const qualifiedValue = Number(rawPage?.qualified_candidate_count ?? rawPage?.qualifiedCandidateCount)
  const heldIncompleteValue = Number(rawPage?.held_incomplete_count ?? rawPage?.heldIncompleteCount)
  const shortfallValue = Number(rawPage?.shortfall_count ?? rawPage?.shortfallCount ?? rawPage?.page_shortfall ?? rawPage?.pageShortfall)
  const newCandidateValue = Number(rawPage?.new_candidate_count ?? rawPage?.newCandidateCount)
  const directReferenceValue = Number(rawPage?.direct_reference_count ?? rawPage?.directReferenceCount)
  const pageCompleteRaw = rawPage?.page_complete ?? rawPage?.pageComplete ?? rawPage?.complete ?? rawPage?.is_complete
  const hasMore = rawPage?.has_more !== undefined ? Boolean(rawPage.has_more) : rawPage?.hasMore !== undefined ? Boolean(rawPage.hasMore) : Boolean(rawPage?.next_cursor || rawPage?.nextCursor || rawPage?.next?.cursor) || incoming.length > size
  const sessionId = String(rawPage?.planning_session_id || rawPage?.planningSessionId || payload?.planning_session_id || payload?.planningSessionId || bundle?.planning_session_id || "").trim()
  const revisionValue = Number(rawPage?.revision ?? payload?.revision ?? bundle?.revision)
  const currentCursor = String(rawPage?.current_cursor || rawPage?.currentCursor || rawPage?.cursor_current || rawPage?.current?.cursor || payload?.current_cursor || payload?.currentCursor || payload?.current?.cursor || "").trim() || undefined
  const offsetValue = Number(rawPage?.offset ?? rawPage?.start_offset ?? payload?.offset)
  const explicitServerPaged = rawPage?.server_paged ?? rawPage?.serverPaged ?? payload?.server_paged ?? payload?.serverPaged
  const serverPaged = explicitServerPaged !== undefined
    ? Boolean(explicitServerPaged)
    : Boolean(
      ["server_paged", "server-paged"].includes(String(rawPage?.mode || payload?.candidate_page_mode || payload?.mode || "").toLowerCase())
      || rawPage?.page_index !== undefined
      || sessionId,
    )
  // ``page_index`` is the canonical zero-based value.  The server's
  // human-facing ``page``/``page_number`` aliases are one-based whenever a
  // server-paged response (or an explicit page_base=1) is present.  Keep the
  // legacy client-only ``page`` field zero-based when none of those markers
  // are supplied.
  const page = Number.isFinite(parsedPageIndex) && parsedPageIndex >= 0
    ? parsedPageIndex
    : Number.isFinite(parsedPageNumber)
      ? Math.max(0, parsedPageNumber - (pageBase === 1 || serverPaged || rawPage?.page_number !== undefined || payload?.page_number !== undefined ? 1 : 0))
      : 0
  const currentItemIdentities = (Array.isArray(rawPage?.current_item_identities)
    ? rawPage.current_item_identities
    : Array.isArray(rawPage?.currentItemIdentities)
      ? rawPage.currentItemIdentities
      : Array.isArray(rawPage?.current_item_ids)
        ? rawPage.current_item_ids
      : Array.isArray(rawPage?.item_identities)
          ? rawPage.item_identities
      : incoming.map((item: any) => candidateKey(item)))
    .map((item: any) => String(item || "").trim())
    .filter(Boolean)
  const mergedCandidates = [...protectedItems, ...providedItems, ...incoming]
    .map((item: any) => ({ ...item, url: String(item?.url || "").trim() }))
    .filter((item: PlanningCandidate) => Boolean(item.url && candidateKey(item)))
    .filter((item: PlanningCandidate, index: number, values: PlanningCandidate[]) => values.findIndex(value => candidateKey(value) === candidateKey(item)) === index)
  return {
    candidates: mergedCandidates,
    page: {
      page,
      pageIndex: page,
      page_index: page,
      pageSize: size,
      page_size: size,
      ...(Number.isFinite(totalValue) ? { total: totalValue } : {}),
      ...(Number.isFinite(qualifiedValue) ? { qualifiedCount: qualifiedValue } : {}),
      ...(Number.isFinite(heldIncompleteValue) ? { heldIncompleteCount: heldIncompleteValue } : {}),
      ...(Number.isFinite(shortfallValue)
        ? { shortfallCount: shortfallValue, shortfall_count: shortfallValue }
        : {}),
      ...(pageCompleteRaw !== undefined ? { pageComplete: Boolean(pageCompleteRaw), page_complete: Boolean(pageCompleteRaw) } : {}),
      ...(Number.isFinite(newCandidateValue) ? { newCandidateCount: newCandidateValue } : {}),
      ...(Number.isFinite(directReferenceValue) ? { directReferenceCount: directReferenceValue } : {}),
      hasMore,
      has_more: hasMore,
      nextCursor: String(rawPage?.next_cursor || rawPage?.nextCursor || rawPage?.next?.cursor || payload?.next_cursor || payload?.nextCursor || payload?.next?.cursor || "") || undefined,
      next_cursor: String(rawPage?.next_cursor || rawPage?.nextCursor || rawPage?.next?.cursor || payload?.next_cursor || payload?.nextCursor || payload?.next?.cursor || "") || undefined,
      previousCursor: String(rawPage?.previous_cursor || rawPage?.previousCursor || rawPage?.previous?.cursor || payload?.previous_cursor || payload?.previousCursor || payload?.previous?.cursor || "") || undefined,
      previous_cursor: String(rawPage?.previous_cursor || rawPage?.previousCursor || rawPage?.previous?.cursor || payload?.previous_cursor || payload?.previousCursor || payload?.previous?.cursor || "") || undefined,
      ...(currentCursor ? { currentCursor, current_cursor: currentCursor } : {}),
      ...(Number.isFinite(offsetValue) ? { offset: offsetValue } : {}),
      ...(sessionId ? { planningSessionId: sessionId, planning_session_id: sessionId } : {}),
      ...(Number.isFinite(revisionValue) ? { revision: revisionValue } : {}),
      serverPaged,
      server_paged: serverPaged,
      currentItemIdentities,
      current_item_identities: currentItemIdentities,
      mode: (() => {
        const value = String(rawPage?.mode || payload?.candidate_page_mode || "").toLowerCase()
        if (value === "append") return "append" as const
        if (value === "server_paged" || value === "server-paged" || serverPaged) return "server_paged" as const
        return "replace" as const
      })(),
    },
    session: payload?.session || bundle?.session || (sessionId ? { planning_session_id: sessionId, revision: Number.isFinite(revisionValue) ? revisionValue : undefined } : undefined),
    selection: payload?.selection || bundle?.selection || rawPage?.selection,
  }
}

export type PlanningCandidatePoolProps = {
  candidates: PlanningCandidate[]
  selectedKeys: string[]
  onSelectionChange: (keys: string[]) => void
  anchorKey?: string
  onAnchorChange?: (key: string) => void
  /** Return false to require an explicit confirmation before changing anchor. */
  onAnchorChangeRequest?: (nextKey: string, currentKey: string) => boolean | void
  rejectedKeys?: string[]
  onRejectedKeysChange?: (keys: string[]) => void
  pageSize?: number
  candidatePage?: Partial<CandidatePage>
  onRequestPage?: (page: number, direction: "next" | "previous", cursor?: string) => void
  /** Explicit learner-authorized discovery turn used to refill a short page. */
  onRequestReplenish?: () => void
  /** Explicit discovery turn that expands the durable pool by one page. */
  onRequestAddPage?: () => void
  /** Count of materials already chosen for the eventual handoff bundle. */
  selectedMaterialCount?: number
  /** Optional parent notice hook for a blocked add-page action. */
  onAddPageBlocked?: (selectedCount: number, pageSize: number) => void
  /** Optional durable-pool event hook. Legacy callers can continue to use the state callbacks. */
  onPoolAction?: (action: "select" | "deselect" | "reject" | "undo" | "set_anchor" | "clear_anchor", key: string) => void
  busy?: boolean
  /** Visible copy for a durable request; controls remain disabled while busy. */
  pendingLabel?: string
  updateMode?: "append" | "replace" | "server_paged"
  disabled?: boolean
  className?: string
  testId?: string
  emptyLabel?: string
}

/**
 * Keep diversification context bounded and metadata-only. URLs are not sent
 * in this summary lane: the durable pool already owns candidate identities and
 * the next search round should not manufacture or replay links from the UI.
 */
export function boundedCandidateSummaries(
  candidates: PlanningCandidate[],
  selectedKeys: string[] = [],
  maxItems = 12,
): Array<Record<string, unknown>> {
  const selected = new Set(selectedKeys)
  return candidates
    .map(item => {
      const key = candidateKey(item)
      return {
        ...(item.candidate_id ? { candidate_id: String(item.candidate_id) } : {}),
        ...(item.stable_id ? { stable_id: String(item.stable_id) } : {}),
        ...(item.id ? { id: String(item.id) } : {}),
        ...(item.title ? { title: String(item.title).slice(0, 240) } : {}),
        ...(item.snippet ? { snippet: String(item.snippet).slice(0, 500) } : {}),
        ...(item.media_kind ? { media_kind: String(item.media_kind) } : {}),
        ...(item.coverage_role ? { coverage_role: String(item.coverage_role) } : {}),
        ...(item.source_kind ? { source_kind: String(item.source_kind) } : {}),
        ...(item.authority_status ? { authority_status: String(item.authority_status) } : {}),
        ...(item.popularity_status ? { popularity_status: String(item.popularity_status) } : {}),
        selected: selected.has(key) || item.selected === true,
        ...(item.retained === true ? { retained: true } : {}),
        ...(item.direct_user_reference === true ? { direct_user_reference: true } : {}),
      }
    })
    .filter(item => Object.keys(item).length > 1)
    .slice(0, Math.max(1, Math.floor(maxItems)))
}

const labelFor = (item: PlanningCandidate) => String(item.title || item.url || "候选资料")
const signal = (value: unknown, fallback = "未知") => String(value ?? "").trim() || fallback
const rankReason = (item: PlanningCandidate) => (
  (Array.isArray(item.rank_reasons) ? item.rank_reasons.filter(Boolean).join("；") : "")
  || item.rank_reason
  || item.reason
  || "排序理由未知"
)

export function PlanningCandidatePool({
  candidates,
  selectedKeys,
  onSelectionChange,
  anchorKey = "",
  onAnchorChange,
  onAnchorChangeRequest,
  rejectedKeys: rejectedKeysProp,
  onRejectedKeysChange,
  pageSize = 3,
  candidatePage,
  onRequestPage,
  onRequestReplenish,
  onRequestAddPage,
  selectedMaterialCount,
  onAddPageBlocked,
  busy = false,
  pendingLabel,
  updateMode = candidatePage?.mode || "replace",
  disabled = false,
  className = "",
  testId = "planning-candidate-pool",
  emptyLabel = "暂时没有可核对的候选资料。",
  onPoolAction,
}: PlanningCandidatePoolProps) {
  const [page, setPage] = useState(Math.max(0, Number(candidatePage?.page || 0)))
  const [localRejectedKeys, setLocalRejectedKeys] = useState<string[]>([])
  const [actionNotice, setActionNotice] = useState("")
  // ``undefined`` means no local override; an empty string is an optimistic
  // explicit clear while the parent/durable projection catches up.
  const [localAnchorKey, setLocalAnchorKey] = useState<string | undefined>(undefined)
  // The parent owns the durable selection, but it can be a render behind while
  // a checkbox event is being handled. Keep the last emitted value locally so
  // two rapid clicks cannot both observe the same stale `selectedKeys` prop.
  const selectedKeysRef = useRef(selectedKeys)
  selectedKeysRef.current = selectedKeys
  // A role click is optimistic. Prefer that local intent for the current
  // render while the parent/durable projection catches up; otherwise one
  // delayed render can make a successful first click look like a no-op.
  const rawEffectiveAnchorKey = localAnchorKey !== undefined ? localAnchorKey : anchorKey
  const rejectedKeys = rejectedKeysProp || localRejectedKeys
  const setRejectedKeys = (next: string[]) => {
    if (onRejectedKeysChange) onRejectedKeysChange(next)
    else setLocalRejectedKeys(next)
  }
  const rejected = new Set(rejectedKeys)
  const blockedCandidates = useMemo(() => candidates.filter(item => isCandidateBlocked(item)), [candidates])
  const blockedKeys = useMemo(() => new Set(blockedCandidates.map(candidateKey)), [blockedCandidates])
  const effectiveAnchorKey = blockedKeys.has(rawEffectiveAnchorKey) ? "" : rawEffectiveAnchorKey
  const visibleCandidates = useMemo(() => candidates.filter(item => !rejected.has(candidateKey(item)) && !blockedKeys.has(candidateKey(item))), [candidates, rejectedKeys, blockedKeys])
  const selected = useMemo(
    () => candidates.filter(item => selectedKeys.includes(candidateKey(item)) && !rejected.has(candidateKey(item)) && !blockedKeys.has(candidateKey(item))),
    [candidates, selectedKeys, rejectedKeys, blockedKeys],
  )
  useEffect(() => {
    const firstVisibleSelected = selected[0] ? candidateKey(selected[0]) : ""
    if (!effectiveAnchorKey && localAnchorKey === undefined && firstVisibleSelected) setLocalAnchorKey(firstVisibleSelected)
  }, [effectiveAnchorKey, localAnchorKey, selected])
  useEffect(() => {
    // Once the durable/parent projection reflects the optimistic role, drop
    // the local override so future external snapshots can update normally.
    if (localAnchorKey !== undefined && anchorKey === localAnchorKey) setLocalAnchorKey(undefined)
  }, [anchorKey, localAnchorKey])
  const selectedSet = new Set(selected.map(candidateKey))
  const currentPage = Number(candidatePage?.page ?? page)
  const effectiveSize = Math.max(1, Number(candidatePage?.pageSize || pageSize))
  const total = Number(candidatePage?.total ?? visibleCandidates.length)
  const pages = pageCount(total, effectiveSize)
  const hasPrevious = currentPage > 0
  const hasNext = candidatePage?.hasMore === true || currentPage < pages - 1
  const shortfallCount = Math.max(0, Number(candidatePage?.shortfallCount ?? 0))
  const heldIncompleteCount = Math.max(0, Number(candidatePage?.heldIncompleteCount ?? 0))
  const pageComplete = candidatePage?.pageComplete === false || shortfallCount > 0 || heldIncompleteCount > 0
    ? false
    : candidatePage?.pageComplete

  useEffect(() => {
    if (candidatePage?.page !== undefined) setPage(Math.max(0, Number(candidatePage.page) || 0))
  }, [candidatePage?.page])

  const changePage = (nextPage: number, direction: "next" | "previous") => {
    const bounded = Math.max(0, nextPage)
    setPage(bounded)
    const cursor = direction === "previous" ? candidatePage?.previousCursor : candidatePage?.nextCursor
    onRequestPage?.(bounded, direction, cursor)
  }
  const toggle = (key: string) => {
    if (disabled || !key) return
    const current = selectedKeysRef.current
    const removing = current.includes(key)
    const next = removing ? current.filter(item => item !== key) : [...current, key]
    selectedKeysRef.current = next
    onSelectionChange(next)
    onPoolAction?.(removing ? "deselect" : "select", key)
    if (!removing && !effectiveAnchorKey) {
      setLocalAnchorKey(key)
      onAnchorChange?.(key)
    }
    if (removing && key === effectiveAnchorKey) {
      // Removing an anchor never silently promotes a different candidate. The
      // learner must explicitly choose a replacement or clear the role.
      setLocalAnchorKey("")
      onAnchorChange?.("")
    }
  }
  const changeRole = (key: string, role: CandidateRole) => {
    if (disabled || !selectedSet.has(key)) return
    if (role === "anchor" && key !== effectiveAnchorKey) {
      const allowed = onAnchorChangeRequest?.(key, effectiveAnchorKey)
      if (allowed === false) return
      setLocalAnchorKey(key)
      onAnchorChange?.(key)
      onPoolAction?.("set_anchor", key)
    } else if (role === "support" && key === effectiveAnchorKey) {
      const replacement = selectedKeys.find(item => item !== key) || ""
      if (!replacement) return
      const allowed = onAnchorChangeRequest?.(replacement, key)
      if (allowed === false) return
      setLocalAnchorKey(replacement)
      onAnchorChange?.(replacement)
      onPoolAction?.("set_anchor", replacement)
    }
  }
  const reject = (item: PlanningCandidate) => {
    if (disabled) return
    const key = candidateKey(item)
    if (!key) return
    if (selectedSet.has(key)) {
      const next = selectedKeys.filter(itemKey => itemKey !== key)
      onSelectionChange(next)
      // The durable pool rejects only unselected candidates. Keep the
      // component-level event stream valid for callers using onPoolAction;
      // parent selection callbacks may additionally enqueue the same
      // transition, but never put reject ahead of deselect.
      onPoolAction?.("deselect", key)
      if (key === effectiveAnchorKey) {
        setLocalAnchorKey("")
        onAnchorChange?.("")
      }
    }
    setRejectedKeys([...new Set([...rejectedKeys, key])])
    onPoolAction?.("reject", key)
  }
  const undo = (key: string) => {
    setRejectedKeys(rejectedKeys.filter(item => item !== key))
    onPoolAction?.("undo", key)
  }
  const serverPageKeys = new Set((candidatePage?.currentItemIdentities || []).map(String))
  const pageVisibleCandidates = candidatePage?.serverPaged
    ? serverPageKeys.size
      ? visibleCandidates.filter(item => serverPageKeys.has(candidateKey(item)) || serverPageKeys.has(String((item as any).candidate_id || (item as any).stable_id || (item as any).id)))
      : []
    : visibleCandidates
  const pageItems = candidatePage?.serverPaged ? pageVisibleCandidates : pageCandidates(pageVisibleCandidates, currentPage, effectiveSize)
  const pinnedItems = selected
  const selectedCount = Math.max(0, Number(selectedMaterialCount ?? selected.length) || 0)
  const hasPool = total > 0 || visibleCandidates.length > 0
  const addPage = () => {
    if (disabled || busy || !onRequestAddPage) return
    if (selectedCount < effectiveSize) {
      const message = `请先至少选择 ${effectiveSize} 份材料，再尝试扩充候选`
      setActionNotice(message)
      onAddPageBlocked?.(selectedCount, effectiveSize)
      return
    }
    setActionNotice("")
    onRequestAddPage()
  }

  return (
    <section className={`planning-candidate-pool ${className}`} data-testid={testId} aria-busy={busy || undefined}>
      <div className="planning-candidate-pool__toolbar">
        <div>
          <b>资料候选池</b>
          <span>{selected.length ? `已选 ${selected.length} 份 · 选中项会固定保留` : "公开检索仅补充匹配的笔记/文档；可逐份打开原文后选择"}</span>
        </div>
        <span className="planning-candidate-pool__mode" data-testid={`${testId}-mode`}>
          {updateMode === "append" ? "追加候选" : updateMode === "server_paged" ? "服务端分页" : "替换未选候选"}
        </span>
      </div>
      {pinnedItems.length > 0 && (
        <div className="planning-candidate-pool__pinned" data-testid={`${testId}-pinned`} aria-label="已固定选择">
          <div className="planning-candidate-pool__lane-title"><Check size={14} /> 已固定选择 · {pinnedItems.length} 份</div>
          <div className="planning-candidate-pool__pinned-items">
            {pinnedItems.map(item => {
              const key = candidateKey(item)
              const isAnchor = key === effectiveAnchorKey
              return <div className={`planning-candidate-pool__pin ${isAnchor ? "is-anchor" : "is-support"}`} key={key} data-testid={`${testId}-pinned-${key.replace(/[^a-z0-9_-]/gi, "-")}`}>
                <span title={labelFor(item)}>{labelFor(item)}</span>
                <small>{isAnchor ? "纲领" : "辅助"}</small>
                {item.direct_user_reference ? <small>你提供的资料</small> : null}
                <small>已保留选择 · 本轮不会被旧候选替换</small>
                <button type="button" onClick={() => toggle(key)} disabled={disabled} aria-label={`移除 ${labelFor(item)}`}>×</button>
              </div>
            })}
          </div>
        </div>
      )}
      {busy && <div className="planning-candidate-pool__status" data-testid={`${testId}-busy`} role="status">{pendingLabel || "正在同步候选池，已选资料会继续保留…"}</div>}
      {!busy && actionNotice && <div className="planning-candidate-pool__status" data-testid={`${testId}-action-notice`} role="status">{actionNotice}</div>}
      {!busy && blockedCandidates.length > 0 && <div className="planning-candidate-pool__status" data-testid={`${testId}-safety-note`} role="status">
         <span>{blockedCandidates.every(looksLikeDisallowedMediaCandidate)
           ? "当前候选只有视频、字幕或音频资料；规划资料入口仅接受非视频资料，请上传或搜索可读取的资料。"
          : `已排除 ${blockedCandidates.length} 份不安全、低相关或缺少独立内容的候选；它们不会占用推荐名额。`}</span>
        {onRequestReplenish && pageComplete === false && <button type="button" onClick={onRequestReplenish} disabled={disabled || busy} data-testid={`${testId}-replenish`}>补齐当前页</button>}
      </div>}
      {!busy && !pageItems.length && <div className="planning-candidate-pool__empty" role="status">
         {blockedCandidates.length > 0 && blockedCandidates.every(looksLikeDisallowedMediaCandidate)
           ? "当前候选只有视频、字幕或音频资料；它们不会进入规划资料包。请上传或搜索可读取的非视频资料。"
          : candidatePage?.heldIncompleteCount
          ? `当前合计还有 ${candidatePage.heldIncompleteCount} 份候选未组成完整的 ${effectiveSize} 份一页，系统会继续补齐后展示。`
          : emptyLabel}
      </div>}
      {pageItems.length > 0 && (
        <div className="planning-candidate-pool__grid" role="list" aria-label="候选资料">
          {pageItems.map((item, index) => {
            const key = candidateKey(item)
            const checked = selectedSet.has(key)
            const isAnchor = checked && key === effectiveAnchorKey
            return <article className={`planning-candidate-pool__card ${checked ? "is-selected" : ""} ${isAnchor ? "is-anchor" : ""}`} key={key} role="listitem" data-testid={`${testId}-card-${key.replace(/[^a-z0-9_-]/gi, "-")}`}>
              <label className="planning-candidate-pool__select">
                <input type="checkbox" name={`${testId}-resource`} checked={checked} onChange={() => toggle(key)} disabled={disabled} aria-label={`选择 ${labelFor(item)}`} />
                <span className="planning-candidate-pool__index">{String(currentPage * effectiveSize + index + 1).padStart(2, "0")}</span>
                <span className="planning-candidate-pool__copy">
                  <strong title={labelFor(item)}>{labelFor(item)}</strong>
                  {checked && (item.selected || item.retained) ? <small>已保留选择 · 后续检索不会替换</small> : null}
                  {item.direct_user_reference ? <small>精确引用 · 由你提供，尚未联网核验</small> : null}
                  <small>{signal(item.media_kind, "来源类型未知")} · 角色：{signal(item.coverage_role || item.collection || item.collection_name)}</small>
                  <small>权威：{signal(item.authority_status || item.authority || item.authority_signal || (item.authority_explanation ? "已提供说明" : ""))} · 热度：{signal(item.popularity_status || item.popularity || item.popularity_signal)}</small>
                  <em>{rankReason(item)}</em>
                  {item.url && <a href={item.url} target="_blank" rel="noreferrer" aria-label={`打开“${labelFor(item)}”原文`} onClick={event => event.stopPropagation()}>打开原文 <ArrowRight size={13} /></a>}
                </span>
              </label>
              {checked && <div className="planning-candidate-pool__roles" aria-label={`${labelFor(item)}角色`}>
                <button type="button" className={isAnchor ? "is-active" : ""} onClick={() => changeRole(key, "anchor")} disabled={disabled} aria-pressed={isAnchor}>{isAnchor ? "纲领（默认）" : "设为纲领"}</button>
                <button type="button" className={!isAnchor ? "is-active" : ""} onClick={() => changeRole(key, "support")} disabled={disabled || isAnchor && selected.length < 2} aria-pressed={!isAnchor}>辅助</button>
              </div>}
              <button type="button" className="planning-candidate-pool__reject" onClick={() => reject(item)} disabled={disabled} aria-label={`暂不使用 ${labelFor(item)}`}><X size={13} /> 暂不使用</button>
            </article>
          })}
        </div>
      )}
      {rejectedKeys.length > 0 && <div className="planning-candidate-pool__rejected" data-testid={`${testId}-rejected`}>
        <span>已暂不使用 {rejectedKeys.length} 份</span>
        {rejectedKeys.map(key => <button type="button" key={key} onClick={() => undo(key)} disabled={disabled}><RotateCcw size={13} /> 撤销</button>)}
      </div>}
      <div className="planning-candidate-pool__pagination" aria-label="候选资料翻页">
        <button type="button" onClick={() => changePage(currentPage - 1, "previous")} disabled={disabled || busy || !hasPrevious} aria-label="上一页" data-testid={`${testId}-previous`}><ArrowLeft size={14} /> 上一页</button>
        <span data-testid={`${testId}-page-status`} aria-live="polite">
          第 {Math.min(currentPage + 1, pages)} / {pages} 页 · 共 {total} 份候选
          {Number.isFinite(candidatePage?.qualifiedCount) && candidatePage?.qualifiedCount !== total ? ` · 合格 ${candidatePage?.qualifiedCount} 份` : ""}
          {Number.isFinite(candidatePage?.directReferenceCount) ? ` · ${candidatePage?.directReferenceCount} 份由你提供` : ""}
          {Number.isFinite(candidatePage?.newCandidateCount) ? ` · ${candidatePage?.newCandidateCount} 份新资料` : ""}
          {pageComplete === false
            ? ` · 当前页未补齐${shortfallCount ? `（还缺 ${shortfallCount} 份）` : heldIncompleteCount ? `（暂存 ${heldIncompleteCount} 份合格候选）` : ""}`
            : ""}
          {candidatePage?.hasMore ? " · 还有更多" : ""}
        </span>
        <button type="button" onClick={() => changePage(currentPage + 1, "next")} disabled={disabled || busy || !hasNext} aria-label="下一页" data-testid={`${testId}-next`}><ArrowRight size={14} /> 下一页</button>
      </div>
      <div className="planning-candidate-pool__capacity" data-testid={`${testId}-capacity`}>
        {pageComplete === false
          ? `后端只发布了当前可核对的 ${total} 份；${shortfallCount
            ? `还缺 ${shortfallCount} 份（${shortfallCount} 个名额）。`
            : heldIncompleteCount
              ? `仍有 ${heldIncompleteCount} 份合格候选暂未组成完整一页。`
              : "当前页不是完整三份，原因已由后端标记。"}`
          : selected.length ? `已固定 ${selected.length} 份，当前页可替换未选候选` : "还可选择资料；选中的资料会固定在上方"}
        <span className="planning-candidate-pool__capacity-actions">
          {!busy && pageComplete === false && onRequestReplenish && blockedCandidates.length === 0
            ? <button type="button" onClick={onRequestReplenish} disabled={disabled || busy} data-testid={`${testId}-replenish-shortfall`}>补齐当前页</button>
            : null}
          {!busy && hasPool && onRequestAddPage
            ? <button type="button" onClick={addPage} disabled={disabled || busy} data-testid={`${testId}-add-page`}>再加 {effectiveSize} 份资料</button>
            : null}
        </span>
      </div>
    </section>
  )
}

export default PlanningCandidatePool
