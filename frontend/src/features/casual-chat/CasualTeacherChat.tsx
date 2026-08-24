import { useEffect, useMemo, useRef, useState } from "react"
import { ArrowRight, BookOpen, Check, FileText, MessageCircle, Send, Settings2, Sparkles, Volume2, X } from "lucide-react"
import { activeRequest, activeTeacherPersonas, DOCUMENT_MATERIAL_ACCEPT, isSupportedDocumentFile, type ActiveTeacherPersona } from "../active-teaching/api"
import { providerRequestContext, request, syncCurrentProviderSession } from "../../providerConnection"
import { useMentorStore } from "../../stores/useMentorStore"
import {
  speakLocalTeacherVoice,
  stopLocalTeacherVoice,
  type LocalTeacherVoiceResult,
} from "../voice/localTeacherVoice"
import { Teacher2DStage } from "../teachers/presentation/Teacher2DStage"
import { VoicePlaybackRateControl } from "../voice/VoicePlaybackRateControl"
import PlanningCandidatePool, { boundedCandidateSummaries, candidatePageFillTarget, isCandidateBlocked, looksLikeDisallowedMediaCandidate, readCandidatePage, type CandidatePage } from "../planning/PlanningCandidatePool"
import { buildPlanningSearchAnchor, candidateServerId, clientRequestId, enqueuePlanningPoolRequest, isPlanningPoolStaleError, monotonicPlanningPoolRevision, planningDiscoveryMode, planningPoolSessionQueueKey, readPlanningPoolState, selectionKeys, writePlanningPoolState } from "../planning/planningCandidatePoolClient"
import "./casual-chat.css"

type CasualMessage = { id: string; role: "user" | "teacher"; content: string }
type CasualTeacher = ActiveTeacherPersona & { portrait: string; color: string }
type CasualSession = {
  id: string;
  workspace_id?: string;
  teacher_id: string;
  teacher_name: string;
  teacher_prompt?: string;
  extra_setting?: string;
  messages?: Array<{ role: "user" | "teacher"; content: string }>;
  created_at?: string;
  updated_at?: string;
}

export type PlannerResource = {
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
  engine?: string
  video_only_acknowledged?: boolean
  selected?: boolean
  retained?: boolean
  direct_user_reference?: boolean
  direct_identifier?: { kind?: string; value?: string; match?: string }
  reference_resolution?: { status?: string; network_verified?: boolean; requires_user_confirmation?: boolean }
  unsafe?: boolean
  blocked?: boolean
  off_topic?: boolean
  topic_mismatch?: boolean
  safety_status?: string
  safety_flags?: string[]
  moderation_status?: string
  eligibility_status?: string
}

export type PlannerHandoff = {
  goal: string
  resource?: PlannerResource
  /** Selected public resources, kept alongside resource for old consumers. */
  resources?: PlannerResource[]
  /** Explicitly selected anchor and supporting materials for new consumers. */
  anchor_resource?: PlannerResource
  supporting_resources?: PlannerResource[]
  /** A local file stays in memory until the final task editor uploads it. */
  materialFile?: File
  created_at: string
}

type PlanningPoolRequestScope = {
  workspaceId: string
  sessionId: string
  generation: number
}

export function plannerResourceKey(item: PlannerResource): string {
  const stableId = String(item.stable_id || item.candidate_id || "").trim()
  if (stableId) return `stable:${stableId}`
  const canonical = String(item.canonical_url || item.url || "").trim().toLowerCase()
  if (canonical) return `url:${canonical}`
  const id = String(item.id || "").trim()
  return id ? `id:${id}` : ""
}

export function isPlannerVideo(item: PlannerResource): boolean {
  // ``media_kind`` is the canonical classification.  Provenance (source_type
  // / source_kind) and MIME (media_type) must not turn an article into a
  // video-only bundle merely because an upstream adapter used a video label.
  const url = String(item.url || "")
  if (/\.(mp4|webm|mov|m4v|avi|mkv|m3u8)(?:$|[?#])/i.test(url)
    || /(?:youtube\.com\/(?:watch|playlist)|youtu\.be\/|bilibili\.com\/video\/|b23\.tv\/|vimeo\.com\/)/i.test(url)
    || /(?:space\.bilibili\.com\/\d+\/lists\/|youtube\.com\/playlist)/i.test(url)) return true
  const mediaKind = String(item.media_kind || "").trim().toLowerCase()
  if (mediaKind) return mediaKind === "video" || mediaKind === "video_collection"
  return false
}

/**
 * Planning handoff is document-only. This defensive check protects the final
 * contract when an older/stale payload contains a video candidate or unsafe
 * non-HTTPS link.
 */
export function isPlannerHandoffSafe(item: PlannerResource): boolean {
  const url = String(item.url || item.canonical_url || "").trim()
  return !isCandidateBlocked(item)
    && !isPlannerVideo(item)
    && !looksLikeDisallowedMediaCandidate(item)
    && (!url || /^https:\/\//i.test(url))
}

function plannerSignal(value: unknown, fallback = "未知") {
  const text = String(value ?? "").trim()
  return text || fallback
}

function plannerResourceLabel(item: PlannerResource) {
  return plannerSignal(item.media_kind, "来源类型未知")
}

function plannerRankReason(item: PlannerResource) {
  return item.rank_reasons?.filter(Boolean).join("；") || item.rank_reason || item.reason || "排序理由未知"
}

function mergePlannerResources(...groups: PlannerResource[][]): PlannerResource[] {
  const result: PlannerResource[] = []
  const seen = new Map<string, number>()
  const genericTitle = (value: unknown) => /^(?:b站|bilibili)?\s*视频\s*(?:bv|av)[\w.-]*$/i.test(String(value || "").trim())
  groups.flat().forEach((item) => {
    if (!item?.url) return
    const key = plannerResourceKey(item)
    if (!key) return
    const existingIndex = seen.get(key)
    if (existingIndex === undefined) {
      seen.set(key, result.length)
      result.push(item)
      return
    }
    const existing = result[existingIndex]
    const merged: PlannerResource = { ...existing, ...item }
    for (const [field, value] of Object.entries(existing)) {
      const mergedRecord = merged as Record<string, unknown>
      if (mergedRecord[field] === undefined || mergedRecord[field] === null || mergedRecord[field] === "") mergedRecord[field] = value
    }
    if (genericTitle(item.title) && !genericTitle(existing.title)) merged.title = existing.title
    result[existingIndex] = merged
  })
  return result
}

const FALLBACK_TEACHERS: CasualTeacher[] = [
  {
    id: "warm",
    name: "艾莉丝",
    prompt: "温柔、耐心、偶尔俏皮，用生活化的小例子陪伴学生。",
    teaching_style: "活泼陪伴",
    visual_profile_id: "warm",
    visual_profile_name: "小熊软糖教学态",
    visual_manifest_url: "",
    usage_count: 0,
    is_default: true,
    created_at: "",
    updated_at: "",
    portrait: "/teacher2d/original-ai-teacher-blonde-fang/teacher-blonde-fang-v9-teaching-heart-key.png",
    color: "#e39d63",
  },
  {
    id: "researcher",
    name: "林老师",
    prompt: "清晰、理性，但不会冷冰冰；喜欢把复杂问题拆成几步。",
    teaching_style: "清晰拆解",
    visual_profile_id: "researcher",
    visual_profile_name: "研究员",
    visual_manifest_url: "",
    usage_count: 0,
    is_default: false,
    created_at: "",
    updated_at: "",
    portrait: "/teacher2d/original-ai-teacher-blonde-fang/teacher-blonde-fang-v9-teaching-heart-key.png",
    color: "#73b9a2",
  },
  {
    id: "coach",
    name: "小夏老师",
    prompt: "元气、直接、会用短句鼓励你先迈出下一步。",
    teaching_style: "元气鼓励",
    visual_profile_id: "coach",
    visual_profile_name: "元气",
    visual_manifest_url: "",
    usage_count: 0,
    is_default: false,
    created_at: "",
    updated_at: "",
    portrait: "/teacher2d/original-ai-teacher-blonde-fang/teacher-blonde-fang-v9-teaching-heart-key.png",
    color: "#d77b8a",
  },
]

const SELECTED_KEY = "mentorflow_casual_teacher_v1"
const SETTING_KEY = "mentorflow_casual_setting_v1"
const SETTINGS_KEY = "mentorflow_casual_settings_v2"

function loadSavedSettings(): Record<string, string> {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === "object") return parsed as Record<string, string>
    }
    const legacy = localStorage.getItem(SETTING_KEY) || ""
    return legacy ? { warm: legacy } : {}
  } catch {
    return {}
  }
}

function sessionMessages(session: CasualSession | undefined, teacher: CasualTeacher): CasualMessage[] {
  const saved = (session?.messages || []).filter(item => item && item.content).map((item, index) => ({
    id: `${session?.id || teacher.id}-${index}`,
    role: item.role,
    content: String(item.content),
  }))
  return saved.length ? saved : [makeGreeting(teacher)]
}

function sessionPreview(session: CasualSession | undefined, fallback: string): string {
  const last = session?.messages?.at(-1)?.content?.trim()
  return last ? last.slice(0, 30) : fallback
}

function sessionTime(value?: string): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

function portraitForPersona(persona: ActiveTeacherPersona, index: number): string {
  const id = `${persona.id} ${persona.visual_profile_id}`.toLowerCase()
  if (id.includes("coach") || id.includes("encour")) return "/assets/teachers/mentor-iris-encouraging.png"
  if (id.includes("research") || id.includes("explain")) return "/assets/teachers/mentor-iris-explaining.png"
  return [
    "/assets/teachers/mentor-iris.png",
    "/assets/teachers/mentor-iris-explaining.png",
    "/assets/teachers/mentor-iris-encouraging.png",
  ][index % 3]
}

function makeGreeting(teacher: CasualTeacher): CasualMessage {
  return {
    id: `greeting-${teacher.id}`,
    role: "teacher",
    content: `嗨，我是${teacher.name}。这里是下课后的闲聊角，不会影响你的学习计划。今天想聊点什么？`,
  }
}

function LearningPlanner({
  workspaceId,
  onStartPlan,
}: {
  workspaceId: string
  onStartPlan?: (handoff: PlannerHandoff) => void
}) {
  const [input, setInput] = useState("")
  const [goal, setGoal] = useState("")
  const [messages, setMessages] = useState<Array<{ role: "user" | "assistant"; content: string }>>([])
  const [resources, setResources] = useState<PlannerResource[]>([])
  const [selectedResourceKeys, setSelectedResourceKeys] = useState<string[]>([])
  const [anchorResourceKey, setAnchorResourceKey] = useState("")
  const [candidatePage, setCandidatePage] = useState<CandidatePage>({ page: 0, pageSize: 3, hasMore: false, mode: "replace" })
  const [planningSessionId, setPlanningSessionId] = useState("")
  const [planningRevision, setPlanningRevision] = useState<number | undefined>(undefined)
  const [rejectedResourceKeys, setRejectedResourceKeys] = useState<string[]>([])
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [sourceChoice, setSourceChoice] = useState<"none" | "upload" | "search">("none")
  const [lastSearch, setLastSearch] = useState("")
  const [lastProviderCacheRef, setLastProviderCacheRef] = useState("")
  const [busy, setBusy] = useState(false)
  const [poolSyncing, setPoolSyncing] = useState(false)
  const [notice, setNotice] = useState("")
  const [handoffDone, setHandoffDone] = useState(false)
  const goalRef = useRef("")
  // Every request for a durable session flows through the shared settled tail
  // in the pool client, including requests issued by another planning view.
  const poolPendingRef = useRef(0)
  const poolGenerationRef = useRef(0)
  const poolWorkspaceRef = useRef(workspaceId)
  const poolRevisionRef = useRef<number | undefined>(undefined)
  const poolSessionRef = useRef("")
  const poolPageRef = useRef<CandidatePage>({ page: 0, pageSize: 3, hasMore: false, mode: "replace" })
  const candidateSearchRoundRef = useRef(0)
  const selectedKeysRef = useRef<string[]>([])
  const rejectedKeysRef = useRef<string[]>([])
  const resourcesRef = useRef<PlannerResource[]>([])
  const anchorKeyRef = useRef("")
  // Desired checkbox state is recorded before the durable request starts. A
  // delayed/stale response must never roll back that optimistic intent.
  const pendingSelectionRef = useRef<Map<string, boolean>>(new Map())
  // Keep an explicit role decision optimistic until the matching durable
  // response arrives. Refresh/retry snapshots may still carry the old anchor.
  // ``undefined`` means no role intent is pending; an empty string is a
  // deliberate clear while deselecting/rejecting the current anchor.
  const pendingAnchorRef = useRef<string | undefined>(undefined)
  const suppressDefaultAnchorActionRef = useRef("")

  // Keep the latest workspace visible to callbacks created by an older
  // render. The generation below is bumped whenever the planner scope
  // changes, so a late response can never project into the new page.
  poolWorkspaceRef.current = workspaceId

  useEffect(() => { selectedKeysRef.current = selectedResourceKeys }, [selectedResourceKeys])
  useEffect(() => { rejectedKeysRef.current = rejectedResourceKeys }, [rejectedResourceKeys])
  useEffect(() => { resourcesRef.current = resources }, [resources])
  useEffect(() => { anchorKeyRef.current = anchorResourceKey }, [anchorResourceKey])
  useEffect(() => { goalRef.current = goal }, [goal])
  useEffect(() => { poolRevisionRef.current = planningRevision }, [planningRevision])
  useEffect(() => { poolSessionRef.current = planningSessionId }, [planningSessionId])
  useEffect(() => { poolPageRef.current = candidatePage }, [candidatePage])

  const runPoolSerialized = <T,>(task: () => Promise<T>): Promise<T> => (
    enqueuePlanningPoolRequest(
      planningPoolSessionQueueKey(workspaceId, poolSessionRef.current),
      task,
    )
  )
  const isPoolStale = (error: unknown) => isPlanningPoolStaleError(error)
  const capturePoolScope = (sessionId = poolSessionRef.current): PlanningPoolRequestScope => ({
    workspaceId,
    sessionId,
    generation: poolGenerationRef.current,
  })
  const isCurrentPoolScope = (scope: PlanningPoolRequestScope) => (
    scope.generation === poolGenerationRef.current
    && scope.workspaceId === poolWorkspaceRef.current
    && scope.workspaceId === workspaceId
    && (!scope.sessionId || scope.sessionId === poolSessionRef.current)
  )

  const applyPoolPayload = (
    payload: any,
    incomingCandidates?: PlannerResource[],
    options: { preservePage?: boolean; replaceUnselected?: boolean; settledSelection?: { key: string; selected: boolean }; scope?: PlanningPoolRequestScope } = {},
  ) => {
    const parsed = readCandidatePage(payload, [], 3)
    if (options.scope && !isCurrentPoolScope(options.scope)) {
      return { parsed, merged: resourcesRef.current, ignoredScope: true, ignoredStaleResponse: true }
    }
    const nextCandidates = parsed.candidates as PlannerResource[]
    const durableSession = String(parsed.page.planningSessionId || payload?.planning_session_id || "").trim()
    const incomingRevision = parsed.page.revision
    const currentRevision = poolRevisionRef.current
    const sameSession = !durableSession || !poolSessionRef.current || durableSession === poolSessionRef.current
    if (
      sameSession
      && Number.isFinite(incomingRevision)
      && Number.isFinite(currentRevision)
      && Number(incomingRevision) < Number(currentRevision)
    ) {
      return { parsed, merged: resourcesRef.current, ignoredStaleResponse: true }
    }
    const currentResources = resourcesRef.current
    const currentByKey = new Map(currentResources.map(item => [plannerResourceKey(item), item]))
    const protectedKeys = new Set(selectedKeysRef.current.filter(key => {
      const item = currentByKey.get(key)
      return Boolean(item && !rejectedKeysRef.current.includes(key) && !isCandidateBlocked(item))
    }))
    const base = options.replaceUnselected
      ? currentResources.filter(item => (
        protectedKeys.has(plannerResourceKey(item))
        || Boolean(item.direct_user_reference || item.retained || item.selected)
      ))
      : durableSession || parsed.page.mode === "append"
        ? currentResources
        : currentResources.filter(item => protectedKeys.has(plannerResourceKey(item)))
    const merged = mergePlannerResources(base, incomingCandidates || [], nextCandidates)
    resourcesRef.current = merged
    setResources(merged)
    const acceptedRevision = sameSession
      ? monotonicPlanningPoolRevision(currentRevision, incomingRevision)
      : monotonicPlanningPoolRevision(undefined, incomingRevision)
    const nextPage = {
      ...(options.preservePage ? poolPageRef.current : {}),
      ...parsed.page,
      ...(options.preservePage ? {
        page: poolPageRef.current.page,
        pageIndex: poolPageRef.current.pageIndex ?? poolPageRef.current.page,
        page_index: poolPageRef.current.page_index ?? poolPageRef.current.page,
        pageSize: poolPageRef.current.pageSize,
        page_size: poolPageRef.current.page_size ?? poolPageRef.current.pageSize,
        mode: poolPageRef.current.mode,
        serverPaged: poolPageRef.current.serverPaged,
        server_paged: poolPageRef.current.server_paged,
        currentItemIdentities: poolPageRef.current.currentItemIdentities,
        current_item_identities: poolPageRef.current.current_item_identities,
        nextCursor: poolPageRef.current.nextCursor,
        next_cursor: poolPageRef.current.next_cursor,
        previousCursor: poolPageRef.current.previousCursor,
        previous_cursor: poolPageRef.current.previous_cursor,
        currentCursor: poolPageRef.current.currentCursor,
        current_cursor: poolPageRef.current.current_cursor,
      } : {}),
      ...(Number.isFinite(acceptedRevision) ? { revision: acceptedRevision } : {}),
      mode: options.preservePage ? poolPageRef.current.mode : (parsed.page.mode || "append"),
    }
    poolPageRef.current = nextPage
    setCandidatePage(nextPage)
    const session = durableSession
    if (session) {
      setPlanningSessionId(session)
      poolSessionRef.current = session
      writePlanningPoolState(workspaceId, {
        planning_session_id: session,
        ...(Number.isFinite(acceptedRevision) ? { revision: acceptedRevision } : {}),
      })
    }
    if (Number.isFinite(acceptedRevision)) {
      setPlanningRevision(acceptedRevision)
      poolRevisionRef.current = acceptedRevision
    }
    const backendSelection = parsed.selection || parsed.session?.selection || payload?.selection
    const backendRejected = backendSelection?.rejected_keys || backendSelection?.rejectedKeys || backendSelection?.rejected
    const rejectedFromPayload = Array.isArray(backendRejected) ? selectionKeys(backendRejected, merged) : []
    const rejectedSet = new Set([...rejectedKeysRef.current, ...rejectedFromPayload])
    const backendSelected = backendSelection?.selected_keys || backendSelection?.selectedKeys || backendSelection?.selected_candidates || backendSelection?.selected
    if (Array.isArray(backendSelected)) {
      const mergedByKey = new Map(merged.map(item => [plannerResourceKey(item), item]))
      const isVisibleKey = (key: string) => {
        const item = mergedByKey.get(key)
        return Boolean(item && !rejectedSet.has(key) && !isCandidateBlocked(item))
      }
      const optimistic = new Set(selectionKeys(backendSelected, merged).filter(isVisibleKey))
      // Keep local choices while a durable intent is in flight, and retain
      // existing selected rows when a refinement returns only replacements.
      for (const key of selectedKeysRef.current) {
        if ((!options.replaceUnselected || protectedKeys.has(key)) && isVisibleKey(key)) optimistic.add(key)
      }
      for (const [key, selected] of pendingSelectionRef.current) {
        if (selected && isVisibleKey(key)) optimistic.add(key)
        else optimistic.delete(key)
      }
      const keys = [...optimistic]
      selectedKeysRef.current = keys
      setSelectedResourceKeys(keys)
      const settled = options.settledSelection
      if (settled && pendingSelectionRef.current.get(settled.key) === settled.selected) {
        if (optimistic.has(settled.key) === settled.selected) pendingSelectionRef.current.delete(settled.key)
      }
    } else {
      const mergedByKey = new Map(merged.map(item => [plannerResourceKey(item), item]))
      const safeKeys = selectedKeysRef.current.filter(key => {
        const item = mergedByKey.get(key)
        return Boolean(item && !rejectedSet.has(key) && !isCandidateBlocked(item))
      })
      selectedKeysRef.current = safeKeys
      setSelectedResourceKeys(safeKeys)
    }
    if (Array.isArray(backendRejected)) {
      const keys = rejectedFromPayload
      rejectedKeysRef.current = keys
      setRejectedResourceKeys(keys)
    }
    const backendAnchor = String(backendSelection?.anchor_key || backendSelection?.anchorKey || backendSelection?.anchor || payload?.anchor_candidate_id || "")
    const backendAnchorKeyRaw = backendAnchor
      ? (selectionKeys([backendAnchor], merged)[0] || backendAnchor)
      : ""
    const backendAnchorItem = merged.find(item => plannerResourceKey(item) === backendAnchorKeyRaw)
    const backendAnchorKey = backendAnchorItem && !isCandidateBlocked(backendAnchorItem) ? backendAnchorKeyRaw : ""
    const pendingAnchor = pendingAnchorRef.current
    const pendingAnchorItem = pendingAnchor ? merged.find(item => plannerResourceKey(item) === pendingAnchor) : undefined
    const safePendingAnchor = pendingAnchor === "" || (pendingAnchorItem && !isCandidateBlocked(pendingAnchorItem))
      ? pendingAnchor
      : undefined
    const resolvedAnchor = safePendingAnchor !== undefined
      ? safePendingAnchor
      : backendAnchorKey || (options.preservePage ? anchorKeyRef.current : "")
    if (pendingAnchor !== undefined && backendAnchorKey === pendingAnchor) pendingAnchorRef.current = undefined
    anchorKeyRef.current = resolvedAnchor
    setAnchorResourceKey(resolvedAnchor)
    return { parsed, merged }
  }

  const refreshPoolSnapshot = async (workspaceAtRequest: string, sessionId: string, requestPrefix: string, scope = capturePoolScope(sessionId)) => {
    if (!isCurrentPoolScope(scope)) return null
    const result = await activeRequest(`/workspaces/${encodeURIComponent(workspaceAtRequest || "general-learning")}/planning-assistant`, {
      method: "POST",
      body: JSON.stringify({
        message: "",
        pool_action: "refresh",
        planning_session_id: sessionId,
        page_size: poolPageRef.current.pageSize,
        client_request_id: clientRequestId(requestPrefix),
      }),
    }, 30_000)
    if (!isCurrentPoolScope(scope)) return null
    applyPoolPayload(result, undefined, { scope })
    return result
  }

  const enqueuePoolAction = (action: "select" | "deselect" | "reject" | "undo" | "set_anchor" | "clear_anchor", key: string) => {
    const sessionAtEnqueue = poolSessionRef.current
    const workspaceAtEnqueue = workspaceId
    const scopeAtEnqueue = capturePoolScope(sessionAtEnqueue)
    if (!sessionAtEnqueue || !key) return
    if (action === "select" || action === "set_anchor") pendingSelectionRef.current.set(key, true)
    if (action === "deselect" || action === "reject") pendingSelectionRef.current.set(key, false)
    if (action === "set_anchor") pendingAnchorRef.current = key
    if (action === "deselect" || action === "reject") {
      if (anchorKeyRef.current === key) pendingAnchorRef.current = ""
    }
    poolPendingRef.current += 1
    setPoolSyncing(true)
    const queued = runPoolSerialized(async () => {
      if (!isCurrentPoolScope(scopeAtEnqueue)) return
      const item = resourcesRef.current.find(candidate => plannerResourceKey(candidate) === key)
      const id = candidateServerId(item, key)
      const requestId = clientRequestId(`casual-${action}`)
      const send = () => {
        const body: Record<string, unknown> = {
          message: "",
          pool_action: action,
          planning_session_id: sessionAtEnqueue,
          candidate_ids: id ? [id] : [],
          client_request_id: requestId,
        }
        const revision = poolRevisionRef.current
        if (revision !== undefined) { body.revision = revision; body.expected_revision = revision }
        if (action === "set_anchor") body.anchor_candidate_id = id
        if (action === "clear_anchor") body.anchor_candidate_id = id
        return activeRequest(`/workspaces/${encodeURIComponent(workspaceAtEnqueue || "general-learning")}/planning-assistant`, { method: "POST", body: JSON.stringify(body) }, 30_000)
      }
      let result
      try {
        result = await send()
      } catch (error) {
        if (!isPoolStale(error)) throw error
        await refreshPoolSnapshot(workspaceAtEnqueue, sessionAtEnqueue, "casual-action-refresh", scopeAtEnqueue)
        if (!isCurrentPoolScope(scopeAtEnqueue)) return
        result = await send()
      }
      if (!isCurrentPoolScope(scopeAtEnqueue)) return
      applyPoolPayload(result, undefined, {
        preservePage: true,
        scope: scopeAtEnqueue,
        settledSelection: ["select", "set_anchor"].includes(action)
          ? { key, selected: true }
          : ["deselect", "reject"].includes(action)
            ? { key, selected: false }
            : undefined,
      })
    }).catch(async error => {
      if (!isCurrentPoolScope(scopeAtEnqueue)) return
      if (pendingAnchorRef.current === key || (pendingAnchorRef.current === "" && anchorKeyRef.current === key)) {
        pendingAnchorRef.current = undefined
      }
      pendingSelectionRef.current.delete(key)
      try {
        await refreshPoolSnapshot(workspaceAtEnqueue, sessionAtEnqueue, "casual-action-failed-refresh", scopeAtEnqueue)
      } catch {
        // Preserve the original actionable error when even recovery fails.
      }
      if (isCurrentPoolScope(scopeAtEnqueue)) setNotice(error?.message || "候选池操作失败，请刷新后重试。")
    })
    void queued.finally(() => {
      if (!isCurrentPoolScope(scopeAtEnqueue)) return
      poolPendingRef.current = Math.max(0, poolPendingRef.current - 1)
      if (poolPendingRef.current === 0) setPoolSyncing(false)
    })
  }

  useEffect(() => {
    const generation = ++poolGenerationRef.current
    poolWorkspaceRef.current = workspaceId
    const saved = readPlanningPoolState(workspaceId)
    // Scope pool state by workspace; never carry a prior workspace's
    // candidates or decisions while a new durable snapshot is loading.
    setResources([])
    setSelectedResourceKeys([])
    selectedKeysRef.current = []
    setRejectedResourceKeys([])
    rejectedKeysRef.current = []
    setAnchorResourceKey("")
    anchorKeyRef.current = ""
    pendingSelectionRef.current.clear()
    pendingAnchorRef.current = undefined
    const emptyPage: CandidatePage = { page: 0, pageSize: 3, hasMore: false, mode: "replace" }
    poolPageRef.current = emptyPage
    setCandidatePage(emptyPage)
    poolRevisionRef.current = undefined
    setPlanningRevision(undefined)
    poolPendingRef.current = 0
    setPoolSyncing(false)
    setBusy(false)
    setNotice("")
    if (!saved?.planning_session_id) {
      poolSessionRef.current = ""
      setPlanningSessionId("")
      return
    }
    setPlanningSessionId(saved.planning_session_id)
    poolSessionRef.current = saved.planning_session_id
    if (saved.revision !== undefined) {
      setPlanningRevision(saved.revision)
      poolRevisionRef.current = saved.revision
    }
    const scope = { workspaceId, sessionId: saved.planning_session_id, generation }
    let cancelled = false
    void runPoolSerialized(async () => {
      try {
        const result = await activeRequest(`/workspaces/${encodeURIComponent(workspaceId || "general-learning")}/planning-assistant`, {
          method: "POST",
          body: JSON.stringify({ pool_action: "refresh", planning_session_id: saved.planning_session_id, ...(saved.revision !== undefined ? { revision: saved.revision, expected_revision: saved.revision } : {}), client_request_id: clientRequestId("casual-refresh") }),
        }, 30_000)
        if (!cancelled && isCurrentPoolScope(scope)) applyPoolPayload(result, undefined, { scope })
      } catch (error) {
        if (!isPoolStale(error)) throw error
        await refreshPoolSnapshot(workspaceId || "general-learning", saved.planning_session_id, "casual-refresh-recovery", scope)
      }
    }).catch(error => { if (!cancelled && isCurrentPoolScope(scope)) setNotice(error?.message || "无法恢复候选池；你可以重新开始搜索。") })
    return () => { cancelled = true }
  }, [workspaceId])

  type ExplicitCandidatePoolAction = {
    mode: "fill_page" | "add_page"
    target: number
    selectedMaterialCount: number
    searchRound: number
    /** A complete fill is a provider-free read-only pool projection. */
    readOnly: boolean
  }

  async function ask(messageOverride?: string, pageRequest?: number, pageDirection: "next" | "previous" = "next", alreadySerialized = false, requestScope?: PlanningPoolRequestScope, forceSearch = false, explicitAction?: ExplicitCandidatePoolAction): Promise<void> {
    const scope = requestScope || capturePoolScope()
    if (!isCurrentPoolScope(scope)) return
    const text = (messageOverride ?? input).trim()
    const isSearchRetry = Boolean(messageOverride && lastSearch && text === lastSearch)
    const isPoolPage = pageRequest !== undefined && Boolean(poolSessionRef.current)
    if ((!text && !isPoolPage) || busy) return
    // Serialize the first discovery too; before a session id exists the
    // workspace-scoped queue prevents two submits from racing to create it.
    if (!alreadySerialized) {
      // A local-file handoff has no backend round trip. Capture the learner's
      // exact goal before entering the async queue so an immediate click on
      // “start plan” cannot fall back to the generic placeholder.
      if (sourceChoice === "upload" && text) {
        goalRef.current = text
        setGoal(text)
      }
      return runPoolSerialized(() => ask(messageOverride, pageRequest, pageDirection, true, scope, forceSearch, explicitAction))
    }
    if (sourceChoice === "upload" && !forceSearch) {
      setMessages(current => [...current, { role: "user", content: text }, { role: "assistant", content: selectedFile ? `已记下你的目标。确认“${selectedFile.name}”后，我会把它交给学习计划处理。` : "请先选择一个文件，我会在你确认后交给学习计划处理。" }])
      goalRef.current = text
      setGoal(text)
      setInput("")
      if (!selectedFile) setNotice("请先上传一份资料，再确认继续。")
      return
    }
    if (!isPoolPage) {
      setSourceChoice("search")
      setLastSearch(text)
    }
    setBusy(true)
    setNotice("")
    if (!isPoolPage) {
      setMessages(current => [...current, { role: "user", content: text }])
      setInput("")
    }
    try {
      // A complete explicit fill only refreshes the durable pool projection.
      // The backend can answer this path without a Provider, so do not let a
      // missing/expired connection reject the request before it is sent.
      const readOnlyFill = explicitAction?.mode === "fill_page" && explicitAction.readOnly
      let providerContext = readOnlyFill ? {} : providerRequestContext()
      let providerSyncTransportError = false
      if (!isPoolPage && !readOnlyFill && !providerContext.provider_session_id) {
        try {
          await syncCurrentProviderSession()
        } catch (error: any) {
          // A standalone browser test or an unavailable loopback backend can
          // reject before returning a Provider response. Let the normal
          // planner request surface that transport failure; known Provider
          // verification/time-out responses are handled below without making
          // a request that would only produce a misleading 428.
          providerSyncTransportError = error?.name === "TypeError"
        }
        providerContext = providerRequestContext()
      }
      if (!isPoolPage && !readOnlyFill && !providerContext.provider_session_id && !providerSyncTransportError) {
        const providerState = useMentorStore.getState()
        throw new Error(
          providerState.provider === "fake"
            ? "规划助手需要已验证的真实 Provider；Fake 模式不会发起网络请求。"
            : "模型连接仍在验证，请稍候重试。",
        )
      }
      const latestResources = resourcesRef.current
      const latestSelectedKeys = selectedKeysRef.current
      const selectedForRefinement = latestResources.filter(item => (
        latestSelectedKeys.includes(plannerResourceKey(item))
        && !rejectedKeysRef.current.includes(plannerResourceKey(item))
        && !isCandidateBlocked(item)
      ))
      const retainedForRefinement = mergePlannerResources(
        selectedForRefinement,
        latestResources.filter(item => (
          !rejectedKeysRef.current.includes(plannerResourceKey(item))
          && !isCandidateBlocked(item)
          && Boolean(item.direct_user_reference || item.retained || item.selected)
        )),
      )
      const pageSnapshot = poolPageRef.current
      const pageCursor = pageDirection === "previous" ? pageSnapshot.previousCursor : pageSnapshot.nextCursor
      const requestBody: Record<string, unknown> = isPoolPage
        ? {
            message: "",
            pool_action: "page",
            planning_session_id: poolSessionRef.current,
            page: pageRequest,
            page_index: pageRequest,
            cursor: pageCursor,
            ...(pageDirection === "previous" ? { previous_cursor: pageCursor } : { next_cursor: pageCursor }),
            page_size: pageSnapshot.pageSize,
            revision: poolRevisionRef.current,
            expected_revision: poolRevisionRef.current,
            client_request_id: clientRequestId(`casual-page-${pageDirection}`),
          }
        : {
            message: text,
            goal,
            conversation: messages,
            resource_search_authorized: forceSearch || sourceChoice === "search" || sourceChoice === "none",
            auto_companion_resources: true,
             retained_candidates: retainedForRefinement.length
               ? retainedForRefinement
               : latestResources.filter(item => !isCandidateBlocked(item) && !rejectedKeysRef.current.includes(plannerResourceKey(item))),
             selected_resources: selectedForRefinement,
             search_anchor: buildPlanningSearchAnchor({
               message: text,
               objective: goalRef.current || goal || text,
               selectedResources: selectedForRefinement,
               uploadedMaterial: selectedFile
                 ? { filename: selectedFile.name, title: selectedFile.name, content_label: selectedFile.name, media_type: selectedFile.type }
                 : null,
             }),
            planning_session_id: poolSessionRef.current || undefined,
            revision: poolRevisionRef.current,
            expected_revision: poolRevisionRef.current,
            pool_mode: explicitAction?.mode === "add_page" || explicitAction?.mode === "fill_page"
              ? "append"
              : planningDiscoveryMode(
              text,
              selectedForRefinement.length > 0 || latestResources.some(item => Boolean(item.direct_user_reference || item.retained)),
              Boolean(poolSessionRef.current),
            ),
            client_request_id: clientRequestId("casual-discovery"),
            ...(explicitAction ? {
              candidate_discovery_mode: explicitAction.mode,
              candidate_target_count: explicitAction.target,
              selected_material_count: explicitAction.selectedMaterialCount,
              candidate_search_round: explicitAction.searchRound,
              existing_candidate_summaries: boundedCandidateSummaries(latestResources, latestSelectedKeys),
            } : {}),
            ...(!explicitAction && isSearchRetry && lastProviderCacheRef ? { provider_cache_ref: lastProviderCacheRef } : {}),
            ...providerContext,
          }
      let result
      try {
        result = await activeRequest(
          `/workspaces/${encodeURIComponent(workspaceId || "general-learning")}/planning-assistant`,
          {
            method: "POST",
            body: JSON.stringify(requestBody),
          },
          120_000,
        )
      } catch (error) {
        if (!isPoolStale(error)) throw error
        if (!isPoolPage) {
          if (!poolSessionRef.current) throw error
          await refreshPoolSnapshot(workspaceId || "general-learning", poolSessionRef.current, "casual-discovery-refresh", scope)
          if (!isCurrentPoolScope(scope)) return
          result = await activeRequest(
            `/workspaces/${encodeURIComponent(workspaceId || "general-learning")}/planning-assistant`,
            {
              method: "POST",
              body: JSON.stringify({
                ...requestBody,
                revision: poolRevisionRef.current,
                expected_revision: poolRevisionRef.current,
              }),
            },
            120_000,
          )
        } else {
          result = await refreshPoolSnapshot(
            workspaceId || "general-learning",
            poolSessionRef.current,
            "casual-page-refresh",
            scope,
          )
          if (!result || !isCurrentPoolScope(scope)) return
          const targetPage = Math.max(0, Math.min(100, Number(pageRequest) || 0))
          // Cursor state is revision-bound. Replay the original intent once
          // against the fresh snapshot (using no cursor for page zero), then
          // walk forward only if a farther page was requested.
          const refreshedPage = poolPageRef.current
          const retryCursor = pageDirection === "previous"
            ? refreshedPage.previousCursor
            : refreshedPage.nextCursor
          const retryBody: Record<string, unknown> = {
            ...requestBody,
            page: targetPage,
            page_index: targetPage,
            cursor: retryCursor,
            revision: poolRevisionRef.current,
            expected_revision: poolRevisionRef.current,
          }
          if (pageDirection === "previous") {
            delete retryBody.next_cursor
            retryBody.previous_cursor = retryCursor
          } else {
            delete retryBody.previous_cursor
            retryBody.next_cursor = retryCursor
          }
          result = await activeRequest(
            `/workspaces/${encodeURIComponent(workspaceId || "general-learning")}/planning-assistant`,
            { method: "POST", body: JSON.stringify(retryBody) },
            30_000,
          )
          if (!isCurrentPoolScope(scope)) return
          applyPoolPayload(result, undefined, { scope })
          while (poolPageRef.current.page < targetPage) {
            if (!isCurrentPoolScope(scope)) return
            const refreshedPage = poolPageRef.current
            if (!refreshedPage.nextCursor) break
            result = await activeRequest(
              `/workspaces/${encodeURIComponent(workspaceId || "general-learning")}/planning-assistant`,
              {
                method: "POST",
                body: JSON.stringify({
                  message: "",
                  pool_action: "page",
                  planning_session_id: poolSessionRef.current,
                  page: refreshedPage.page + 1,
                  page_index: refreshedPage.page + 1,
                  cursor: refreshedPage.nextCursor,
                  next_cursor: refreshedPage.nextCursor,
                  page_size: refreshedPage.pageSize,
                  revision: poolRevisionRef.current,
                  expected_revision: poolRevisionRef.current,
                  client_request_id: clientRequestId("casual-page-retry"),
                }),
              },
              30_000,
            )
            if (!isCurrentPoolScope(scope)) return
            applyPoolPayload(result, undefined, { scope })
          }
        }
      }
      if (!isCurrentPoolScope(scope)) return
      const parsedPage = readCandidatePage(result, [], 3)
      const nextResources = parsedPage.candidates as PlannerResource[]
      const applied = applyPoolPayload(result, nextResources, {
        replaceUnselected: !isPoolPage && requestBody.pool_mode === "replace_unselected",
        scope,
      })
      if (applied.ignoredStaleResponse || applied.ignoredScope) return
      if (!isPoolPage) setMessages(current => [...current, { role: "assistant", content: String(result.reply || "我已整理出下一步建议。") }])
      // applyPoolPayload owns the durable projection.  A second filter based
      // on this render's stale `resources` value made a confirmed checkbox
      // flash and then disappear.
      setLastProviderCacheRef(String(result?.cache?.provider_ref || ""))
      const nextGoal = String(result.goal_summary || text).trim()
      if (nextGoal) {
        goalRef.current = nextGoal
        setGoal(nextGoal)
      }
      if (!nextResources.length) {
        setNotice((result.warnings || []).join("；") || "暂时没有可核对的公开资料候选，请补充范围或换一种描述。")
      }
    } catch (error: any) {
      if (isCurrentPoolScope(scope)) setNotice(error?.message || "规划助手暂时不可用，请先验证模型连接后重试。")
    } finally {
      if (isCurrentPoolScope(scope)) setBusy(false)
    }
  }

  function enqueuePoolPage(page: number, direction: "next" | "previous") {
    const sessionAtEnqueue = poolSessionRef.current
    const scopeAtEnqueue = capturePoolScope(sessionAtEnqueue)
    if (!sessionAtEnqueue || busy || poolPendingRef.current > 0) return
    poolPendingRef.current += 1
    setPoolSyncing(true)
    const queued = runPoolSerialized(async () => {
      if (!isCurrentPoolScope(scopeAtEnqueue)) return
      await ask("", page, direction, true, scopeAtEnqueue)
    }).catch(error => {
      if (isCurrentPoolScope(scopeAtEnqueue)) setNotice(error?.message || "候选池翻页失败，请重试。")
    })
    void queued.finally(() => {
      if (!isCurrentPoolScope(scopeAtEnqueue)) return
      poolPendingRef.current = Math.max(0, poolPendingRef.current - 1)
      if (poolPendingRef.current === 0) setPoolSyncing(false)
    })
  }

  function requestCandidatePoolAction(mode: "fill_page" | "add_page") {
    if (busy || poolSyncing) return
    const size = Math.max(1, Number(poolPageRef.current.pageSize || 3))
    const currentCount = Math.max(0, Number.isFinite(Number(poolPageRef.current.total))
      ? Number(poolPageRef.current.total)
      : resourcesRef.current.length)
    const selectedMaterialCount = selectedKeysRef.current.length + (selectedFile ? 1 : 0)
    if (mode === "add_page" && selectedMaterialCount < size) {
      setNotice(`请先至少选择 ${size} 份材料，再尝试扩充候选`)
      return
    }
    const target = mode === "fill_page"
      ? candidatePageFillTarget(poolPageRef.current, currentCount, size)
      : Math.ceil((selectedMaterialCount + size) / size) * size
    const qualifiedCount = Number(poolPageRef.current.qualifiedCount)
    const heldIncompleteCount = Number(poolPageRef.current.heldIncompleteCount)
    const publishedTotal = Number(poolPageRef.current.total)
    const eligibleCount = Math.max(
      Number.isFinite(publishedTotal) && publishedTotal >= 0 ? publishedTotal : currentCount,
      Number.isFinite(qualifiedCount) && qualifiedCount >= 0 ? qualifiedCount : 0,
      (Number.isFinite(publishedTotal) && publishedTotal >= 0 ? publishedTotal : currentCount)
        + (Number.isFinite(heldIncompleteCount) && heldIncompleteCount >= 0 ? heldIncompleteCount : 0),
    )
    const searchRound = ++candidateSearchRoundRef.current
    const query = lastSearch || goalRef.current || goal || String(
      resourcesRef.current.find(item => item.direct_user_reference)?.title
      || "继续补齐当前资料候选",
    )
    setSourceChoice("search")
    void ask(query, undefined, "next", false, undefined, true, {
      mode,
      target,
      selectedMaterialCount,
      searchRound,
      readOnly: mode === "fill_page" && eligibleCount >= target,
    })
  }

  function toggleResource(item: PlannerResource) {
    const key = plannerResourceKey(item)
    if (!key) return
    setSelectedResourceKeys(current => current.includes(key)
      ? current.filter(value => value !== key)
      : [...current, key])
  }

  function removeSelectedResource(item: PlannerResource) {
    const key = plannerResourceKey(item)
    setSelectedResourceKeys(current => current.filter(value => value !== key))
  }

  function handoffSelectedResources() {
    const selectedResources = resources
      .filter(item => selectedResourceKeys.includes(plannerResourceKey(item)) && isPlannerHandoffSafe(item))
    if (!selectedResources.length && !selectedFile) {
      setNotice(selectedResourceKeys.length
        ? "当前选择只包含视频或不支持的链接；规划资料入口仅接受后端能力支持的非视频资料（视频、字幕和音频不可用）。"
        : "请选择至少一份可用文档候选，或上传一份受支持的文档后继续。")
      return
    }
    const uploadedResource = selectedFile
      ? {
          url: "",
          title: selectedFile.name,
          snippet: "来自你上传的资料",
          kind: "uploaded_file",
          source_kind: "uploaded_file",
          media_kind: "document",
          direct_user_reference: true,
        }
      : null
    const explicitAnchorKey = anchorResourceKey && selectedResources.some(item => plannerResourceKey(item) === anchorResourceKey)
      ? anchorResourceKey
      : ""
    // Keep an existing anchor stable when a later upload is added. The local
    // file becomes the anchor only when the selected pool has no valid anchor.
    const defaultAnchor = selectedResources[0]
    const resolvedAnchorKey = explicitAnchorKey || (defaultAnchor ? plannerResourceKey(defaultAnchor) : "")
    const anchorResource = selectedResources.find(item => plannerResourceKey(item) === resolvedAnchorKey)
      || (uploadedResource && !selectedResources.length ? uploadedResource : null)
      || uploadedResource
      || { url: "", title: "我的学习资料", snippet: "来自你上传的资料" }
    const fallbackResource = anchorResource
    const uploadedIsSupporting = Boolean(uploadedResource && selectedResources.length && resolvedAnchorKey)
    const handoff: PlannerHandoff = {
      goal: goalRef.current.trim() || goal.trim() || "根据已选资料建立可验证的学习计划",
      resource: fallbackResource,
      resources: selectedResources,
      anchor_resource: anchorResource,
      supporting_resources: [
        ...selectedResources.filter(item => plannerResourceKey(item) !== resolvedAnchorKey),
        ...(uploadedIsSupporting && uploadedResource ? [uploadedResource] : []),
      ],
      materialFile: selectedFile || undefined,
      created_at: new Date().toISOString(),
    }
    setHandoffDone(true)
    onStartPlan?.(handoff)
  }

  function startPlan() {
    const selectedResources = resources.filter(item => selectedResourceKeys.includes(plannerResourceKey(item)) && isPlannerHandoffSafe(item))
    handoffSelectedResources()
  }

  function selectUpload() {
    setSourceChoice("upload")
    setNotice("")
  }

  function selectSearch() {
    setSourceChoice("search")
    setNotice("")
  }

  function handleFile(file?: File) {
    if (!file) return
    if (!isSupportedDocumentFile(file)) {
      setSelectedFile(null)
      setSourceChoice("upload")
      setNotice("不支持这类文件。规划资料入口仅接受后端能力声明可读取的非视频资料；视频、字幕和音频文件已被拒绝。")
      return
    }
    setSelectedFile(file)
    setSourceChoice("upload")
    setNotice(`已选择“${file.name}”。确认后会在下一步上传并建立知识地图。`)
  }

  function removeSelectedFile() {
    if (!selectedFile || handoffDone) return
    const filename = selectedFile.name
    setSelectedFile(null)
    setNotice(`已移除“${filename}”，已选候选资料和纲领状态保持不变。`)
  }

  return (
    <section className="planning-companion" data-testid="learning-planner" aria-label="学习规划助手">
      <header className="planning-companion__header">
        <div>
          <div className="eyebrow"><BookOpen size={14} /> LEARNING PLANNER</div>
          <h2>创建计划</h2>
          <p>教师会把你的需求整理成目标；公开检索只补充匹配的笔记或文档。你的上传资料可以作为纲领，候选按页浏览并确认后才会进入资料整理和计划生成。</p>
        </div>
        <div className="planning-companion__badge"><Sparkles size={15} /> 需求 → 资料 → 计划</div>
      </header>
      <div className="planning-companion__layout">
        <aside className="planning-companion__teacher">
          <div className="planning-companion__portrait" data-testid="planner-teacher-portrait">
            <div className="planning-companion__portrait-glow" />
            <img src="/teacher2d/original-ai-teacher-blonde-fang/teacher-blonde-fang-v9-teaching-heart-key.png" alt="黄色长发的学习规划教师" />
          </div>
          <strong>规划教师</strong>
          <span>先确认目标，再让系统工作</span>
          <div className="planning-companion__steps">
            <span className="is-active"><b>1</b>描述需求</span>
            <span className={resources.length ? "is-active" : ""}><b>2</b>核对资料</span>
            <span className={handoffDone ? "is-active" : ""}><b>3</b>进入计划</span>
          </div>
        </aside>
        <div className="planning-companion__main">
          <div className="planning-companion__messages" aria-live="polite">
            {!messages.length && <div className="planning-companion__welcome"><b>先选择资料来源，再描述目标：</b><span>你可以上传自己的笔记，也可以让 AI 搜索候选资料；候选必须由你打开原文并确认。</span><small>不需要先准备文件，先把目标、范围和时间说出来就行。</small></div>}
            {messages.map((item, index) => (
              <div key={`${item.role}-${index}`} className={`planning-companion__message ${item.role}`}>
                <b>{item.role === "user" ? "你" : "规划教师"}</b><span>{item.content}</span>
              </div>
            ))}
            {busy && <div className="planning-companion__typing">{selectedResourceKeys.length ? "正在保留已选资料并检索配套内容…" : "正在整理需求并检索可核对资料…"}</div>}
          </div>
          <div className="planning-companion__source-choice" aria-label="选择资料来源">
            <span className="planning-companion__source-label">你准备使用哪类资料？</span>
            <button type="button" className={sourceChoice === "upload" ? "is-selected" : ""} onClick={selectUpload} data-testid="planning-companion-use-own">
               <BookOpen size={15} /> 使用我的资料（推荐）
            </button>
            <button type="button" className={sourceChoice === "search" ? "is-selected" : ""} onClick={selectSearch} data-testid="planning-companion-search">
              <Sparkles size={15} /> 帮我找资料
            </button>
            <label className="planning-companion__upload">
              <input data-testid="planning-companion-upload" type="file" accept={DOCUMENT_MATERIAL_ACCEPT} onChange={event => { handleFile(event.target.files?.[0]); event.target.value = "" }} />
              <span>{selectedFile ? `已选：${selectedFile.name}` : "上传文件"}</span>
            </label>
          </div>
          <div className="planning-companion__composer">
            <textarea data-testid="planning-companion-input" value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void ask() } }} disabled={busy || poolSyncing} placeholder={sourceChoice === "upload" ? "告诉教师这份资料要解决什么目标、在何时完成…" : "告诉教师你的学习目标、面向的场景、已有资料和希望完成的时间…"} />
            <button type="button" data-testid="planning-companion-send" className="primary" onClick={() => void ask()} disabled={busy || poolSyncing || !input.trim()}><Send size={16} /> {busy ? (selectedResourceKeys.length ? "补充检索中" : "整理中") : poolSyncing ? "保存候选变更中" : "开始规划"}</button>
          </div>
          {sourceChoice === "upload" && !selectedFile && <div className="planning-companion__hint" role="status">请选择一个文件；你的资料只会在你确认后上传。</div>}
          {resources.length > 0 && (
            <section className="planning-companion__resources" aria-label="推荐资料">
              <div className="planning-companion__resources-head"><div><b>{selectedFile ? "待整理资料包（可多选）" : "推荐资料（可多选）"}</b><span>{selectedFile ? "已选候选与上传文件会合并为一个资料包，一次确认后共同进入整理。" : "公开检索结果仅作匹配的笔记或文档；可保留已选资料，再补充一轮搜索。"}</span></div><span className="planning-companion__verified-note">待你确认</span></div>
              <PlanningCandidatePool
                 candidates={resources}
                 selectedKeys={selectedResourceKeys}
                 onSelectionChange={(keys) => {
                   const previous = selectedKeysRef.current
                   setSelectedResourceKeys(keys)
                   selectedKeysRef.current = keys
                   for (const key of keys.filter(item => !previous.includes(item))) {
                     // The first selected row becomes the default anchor. Send
                     // that as one atomic durable intent rather than select +
                     // set_anchor, which used to make one click race itself.
                     const isDefaultAnchor = !previous.length && !anchorKeyRef.current && keys.length === 1 && key === keys[0]
                     if (isDefaultAnchor) {
                       suppressDefaultAnchorActionRef.current = key
                       enqueuePoolAction("set_anchor", key)
                     } else enqueuePoolAction("select", key)
                   }
                   for (const key of previous.filter(item => !keys.includes(item))) enqueuePoolAction("deselect", key)
                   if (!anchorKeyRef.current && keys[0]) {
                     anchorKeyRef.current = keys[0]
                     setAnchorResourceKey(keys[0])
                   }
                   if (anchorKeyRef.current && !keys.includes(anchorKeyRef.current)) {
                     anchorKeyRef.current = ""
                     setAnchorResourceKey("")
                   }
                 }}
                 anchorKey={anchorResourceKey}
                 onAnchorChange={(key) => {
                   const isDefaultAnchor = key && suppressDefaultAnchorActionRef.current === key
                   if (isDefaultAnchor) suppressDefaultAnchorActionRef.current = ""
                   anchorKeyRef.current = key
                   setAnchorResourceKey(key)
                   // A deselect/reject response already clears an anchor on
                   // the server. Do not enqueue a second clear against the
                   // now-stale revision; only explicit anchor assignment is
                   // a durable pool action.
                   if (key && !isDefaultAnchor) enqueuePoolAction("set_anchor", key)
                 }}
                onAnchorChangeRequest={(next, current) => {
                  if (!current || next === current) return true
                  setNotice("已切换纲领资料；其余已选资料仍作为辅助资料保留。")
                  return true
                }}
                 rejectedKeys={rejectedResourceKeys}
                 onRejectedKeysChange={(keys) => {
                   const previous = rejectedKeysRef.current
                   setRejectedResourceKeys(keys)
                   rejectedKeysRef.current = keys
                   for (const key of keys.filter(item => !previous.includes(item))) enqueuePoolAction("reject", key)
                   for (const key of previous.filter(item => !keys.includes(item))) enqueuePoolAction("undo", key)
                 }}
                 candidatePage={candidatePage}
                 onRequestPage={(page, direction) => {
                   if (poolSessionRef.current && !busy) enqueuePoolPage(page, direction)
                   else if (direction === "next" && candidatePage.nextCursor && lastSearch && !busy) void ask(lastSearch, page, direction)
                 }}
                 onRequestReplenish={() => requestCandidatePoolAction("fill_page")}
                 onRequestAddPage={() => requestCandidatePoolAction("add_page")}
                 selectedMaterialCount={selectedResourceKeys.length + (selectedFile ? 1 : 0)}
                 onAddPageBlocked={(_selectedCount, size) => setNotice(`请先至少选择 ${size} 份材料，再尝试扩充候选`)}
                 busy={poolSyncing}
                pendingLabel="正在保存候选池变更，翻页和卡片操作已暂时锁定…"
                disabled={busy || poolSyncing}
                updateMode={candidatePage.mode}
                 testId="planning-companion-candidate-pool"
              />
              {selectedFile && <div className="planning-companion__local-file" data-testid="planning-companion-merged-upload"><FileText size={18} /><strong>{selectedFile.name}</strong><small>{Math.max(1, Math.round(selectedFile.size / 1024))} KB · {selectedFile.type || "文件"}</small><small>{selectedResourceKeys.length ? "辅助资料 · 将与已选资料合并整理" : "纲领资料（默认）· 将与资料包一起整理"}</small><button type="button" className="planning-companion__remove-upload" aria-label={`移除上传文件 ${selectedFile.name}`} title="移除这份文件" onClick={removeSelectedFile} disabled={handoffDone}><X size={15} /></button></div>}
              <button type="button" className="planning-companion__start" data-testid="planning-companion-start" onClick={startPlan} disabled={(!selectedResourceKeys.length && !selectedFile) || handoffDone}>{handoffDone ? <><Check size={16} /> 已交给学习计划</> : selectedFile && selectedResourceKeys.length ? <>采用已选资料与我的文件，合并整理 <ArrowRight size={16} /></> : selectedFile ? <>采用我的文件，开始整理 <ArrowRight size={16} /></> : <>采用已选资料包，开始整理 <ArrowRight size={16} /></>}</button>
            </section>
          )}
          {selectedFile && !resources.length && <section className="planning-companion__resources planning-companion__local-resource" aria-label="我的资料">
            <div className="planning-companion__resources-head"><div><b>我的资料</b><span>文件会在你确认后交给学习计划处理。</span></div><span className="planning-companion__verified-note">待你确认</span></div>
            <div className="planning-companion__local-file"><FileText size={18} /><strong>{selectedFile.name}</strong><small>{Math.max(1, Math.round(selectedFile.size / 1024))} KB · {selectedFile.type || "文件"}</small><small>纲领资料（默认）· 确认后进入学习计划</small><button type="button" className="planning-companion__remove-upload" aria-label={`移除上传文件 ${selectedFile.name}`} title="移除这份文件" onClick={removeSelectedFile} disabled={handoffDone}><X size={15} /></button></div>
             <button type="button" className="planning-companion__start" data-testid="planning-companion-start" onClick={startPlan} disabled={handoffDone}>采用我的资料（推荐），开始整理 <ArrowRight size={16} /></button>
          </section>}
          {!resources.length && lastSearch && !busy && (
            <div className="planning-companion__empty-actions" data-testid="planning-companion-empty-actions">
              <span>没有可核对的候选也没关系，你可以换一种描述或改用自己的资料。</span>
              <button type="button" onClick={() => { setInput(lastSearch); void ask(lastSearch) }} disabled={busy}>重试搜索</button>
              <button type="button" onClick={() => { setInput(""); selectSearch() }}>换一种描述</button>
              <button type="button" onClick={selectUpload}>上传我的资料</button>
            </div>
          )}
          {notice && <div className="planning-companion__notice" role="status">{notice}</div>}
          <p className="planning-companion__footnote">资料确认后，系统会自动进入学习计划：你只需补充目标备注、开始日期和截止日期，知识地图与每日任务由系统计算。</p>
        </div>
      </div>
    </section>
  )
}

export function CasualTeacherChat({ workspaceId, mode = "casual", onStartPlan }: { workspaceId?: string; mode?: "planner" | "casual"; onStartPlan?: (handoff: PlannerHandoff) => void }) {
  if (mode === "planner") return <LearningPlanner workspaceId={workspaceId || "general-learning"} onStartPlan={onStartPlan} />
  // An omitted scope is the standalone/local companion's deterministic safe
  // scope. An explicit empty scope means App is still resolving workspaces;
  // it must not be turned into a real backend scope until that resolution is
  // complete.
  const casualWorkspaceId = workspaceId === undefined ? "general-learning" : workspaceId.trim()
  const [teachers, setTeachers] = useState<CasualTeacher[]>(FALLBACK_TEACHERS)
  const [teacherId, setTeacherId] = useState(() => {
    try { return localStorage.getItem(SELECTED_KEY) || "warm" } catch { return "warm" }
  })
  const [extraSetting, setExtraSetting] = useState(() => {
    const saved = loadSavedSettings()
    return saved.warm || ""
  })
  const initialSettings = useRef<Record<string, string> | null>(null)
  if (!initialSettings.current) initialSettings.current = loadSavedSettings()
  const [, setSettingsByTeacher] = useState<Record<string, string>>(() => initialSettings.current || {})
  const [messages, setMessages] = useState<CasualMessage[]>(() => [makeGreeting(FALLBACK_TEACHERS[0])])
  const [input, setInput] = useState("")
  const [sessionsByTeacher, setSessionsByTeacher] = useState<Record<string, CasualSession>>({})
  const [sessionsLoaded, setSessionsLoaded] = useState(false)
  const [hydratedTeacherId, setHydratedTeacherId] = useState("")
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [speakingMessageId, setSpeakingMessageId] = useState("")
  const [voiceStatus, setVoiceStatus] = useState("")
  const sessionsRef = useRef<Record<string, CasualSession>>({})
  const settingsRef = useRef<Record<string, string>>(initialSettings.current || {})
  const settingDirtyRef = useRef(false)
  const settingDraftRef = useRef<string | undefined>(undefined)
  const explicitTeacherSelectionRef = useRef(false)
  const voiceScopeRef = useRef(0)
  const workspaceGenerationRef = useRef(0)
  const teacherGenerationRef = useRef(0)
  const teacherIdRef = useRef(teacherId)
  const previousWorkspaceScopeRef = useRef(casualWorkspaceId)
  const teacher = useMemo(() => teachers.find(item => item.id === teacherId) || teachers[0], [teacherId, teachers])
  useEffect(() => { teacherIdRef.current = teacher.id }, [teacher.id])

  useEffect(() => {
    let cancelled = false
    activeTeacherPersonas().then(items => {
      if (cancelled || !items.length) return
      setTeachers(items.map((item, index) => ({
        ...item,
        portrait: portraitForPersona(item, index),
        color: index % 2 ? "#73b9a2" : "#e39d63",
      })))
    }).catch(() => { /* local companions keep the module usable offline */ })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    const scope = casualWorkspaceId
    const generation = ++workspaceGenerationRef.current
    const isCurrentScope = () => !cancelled
      && generation === workspaceGenerationRef.current
      && casualWorkspaceId === scope
    const previousScope = previousWorkspaceScopeRef.current
    previousWorkspaceScopeRef.current = scope
    if (previousScope && previousScope !== scope) {
      // A setting draft belongs to the old workspace as well as its teacher;
      // do not carry it into a different workspace while the new history is
      // hydrating.
      settingDirtyRef.current = false
      settingDraftRef.current = undefined
      setExtraSetting("")
      setInput("")
      setBusy(false)
    }

    // Clear all scope-owned state synchronously when the parent changes
    // workspace. The previous request may still settle later, but it can no
    // longer hydrate or mutate the newly selected workspace.
    setSessionsLoaded(false)
    setHydratedTeacherId("")
    sessionsRef.current = {}
    setSessionsByTeacher({})
    setMessages([makeGreeting(teacher || FALLBACK_TEACHERS[0])])
    if (!scope) {
      return () => { cancelled = true }
    }

    const loadSessions = async () => {
      try {
        const query = `?workspace_id=${encodeURIComponent(scope)}`
        const result = await request<{ items?: CasualSession[] }>(`/casual-chat/sessions${query}`)
        if (!isCurrentScope()) return
        const next: Record<string, CasualSession> = {}
        for (const session of result.items || []) {
          if (!session?.teacher_id) continue
          // The API is scoped by query, but fail closed if a stale/misbehaving
          // response includes a session from another workspace.
          if (session.workspace_id && session.workspace_id !== scope) continue
          next[session.teacher_id] = session
        }
        sessionsRef.current = next
        setSessionsByTeacher(next)
      } catch {
        // A fresh local companion remains usable even if the history endpoint
        // is temporarily unavailable.
      } finally {
        if (isCurrentScope()) setSessionsLoaded(true)
      }
    }
    void loadSessions()
    return () => { cancelled = true }
  }, [casualWorkspaceId])

  useEffect(() => {
    if (!teacher || !sessionsLoaded) return
    const saved = sessionsRef.current[teacher.id]
    setMessages(sessionMessages(saved, teacher))
    // Never overwrite a draft that the learner edited while the async
    // persona/session requests were still in flight. This is the exact race
    // that used to restore the server's default setting after a refresh.
    if (settingDirtyRef.current && settingDraftRef.current !== undefined) {
      const draft = settingDraftRef.current
      settingsRef.current = { ...settingsRef.current, [teacher.id]: draft }
      setSettingsByTeacher(current => ({ ...current, [teacher.id]: draft }))
      setExtraSetting(current => current === draft ? current : draft)
    } else {
      const hasTeacherSetting = Object.prototype.hasOwnProperty.call(settingsRef.current, teacher.id)
      const canUseInitialFallback = !explicitTeacherSelectionRef.current
      const fallbackKey = canUseInitialFallback && Object.prototype.hasOwnProperty.call(settingsRef.current, teacherId)
        ? teacherId
        : canUseInitialFallback && teacherId !== "warm" && Object.prototype.hasOwnProperty.call(settingsRef.current, "warm")
          ? "warm"
          : ""
      const localSetting = hasTeacherSetting
        ? settingsRef.current[teacher.id]
        : fallbackKey ? settingsRef.current[fallbackKey] : undefined
      setExtraSetting(localSetting ?? saved?.extra_setting ?? "")
    }
    setNotice("")
    setVoiceStatus("")
    setSpeakingMessageId("")
    setHydratedTeacherId(teacher.id)
    try { localStorage.setItem(SELECTED_KEY, teacher?.id || "warm") } catch { /* optional persistence */ }
  }, [casualWorkspaceId, sessionsLoaded, teacher?.id, teacherId])

  useEffect(() => () => {
    voiceScopeRef.current += 1
    stopLocalTeacherVoice()
  }, [])

  useEffect(() => {
    if (!teacher || !sessionsLoaded || hydratedTeacherId !== teacher.id) return
    setSettingsByTeacher(current => {
      if (current[teacher.id] === extraSetting) return current
      const next = { ...current, [teacher.id]: extraSetting }
      settingsRef.current = next
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(next))
        localStorage.setItem(SETTING_KEY, extraSetting)
      } catch { /* optional persistence */ }
      return next
    })
  }, [extraSetting, teacher?.id])

  useEffect(() => {
    const session = teacher ? sessionsRef.current[teacher.id] : undefined
    const scope = casualWorkspaceId
    const generation = workspaceGenerationRef.current
    const teacherGeneration = teacherGenerationRef.current
    if (!teacher || !scope || !sessionsLoaded || hydratedTeacherId !== teacher.id || !session?.id) return
    if (session.workspace_id && session.workspace_id !== scope) return
    const timer = window.setTimeout(() => {
      if (generation !== workspaceGenerationRef.current || casualWorkspaceId !== scope || teacherGeneration !== teacherGenerationRef.current || teacherIdRef.current !== teacher.id) return
      void request(`/casual-chat/sessions/${encodeURIComponent(session.id)}`, {
        method: "PATCH",
        body: JSON.stringify({
          teacher_id: teacher.id,
          teacher_name: teacher.name,
          teacher_prompt: teacher.prompt,
          extra_setting: extraSetting.trim(),
        }),
      }).catch(() => { /* the next send retries the save */ })
    }, 350)
    return () => window.clearTimeout(timer)
  }, [casualWorkspaceId, extraSetting, teacher?.id, sessionsLoaded, hydratedTeacherId])

  function chooseTeacher(id: string) {
    if (id === teacherId) return
    voiceScopeRef.current += 1
    teacherGenerationRef.current += 1
    stopLocalTeacherVoice()
    explicitTeacherSelectionRef.current = true
    settingDirtyRef.current = false
    settingDraftRef.current = undefined
    setBusy(false)
    setHydratedTeacherId("")
    setTeacherId(id)
  }

  function updateExtraSetting(value: string) {
    const next = value.slice(0, 500)
    settingDirtyRef.current = true
    settingDraftRef.current = next
    if (teacher) {
      const saved = { ...settingsRef.current, [teacher.id]: next }
      settingsRef.current = saved
      setSettingsByTeacher(saved)
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(saved))
        localStorage.setItem(SETTING_KEY, next)
      } catch { /* optional persistence */ }
    }
    setExtraSetting(next)
  }

  async function readAloud(messageId: string, content: string) {
    if (speakingMessageId === messageId) {
      voiceScopeRef.current += 1
      stopLocalTeacherVoice()
      setSpeakingMessageId("")
      setVoiceStatus("已停止朗读")
      return
    }
    const scope = ++voiceScopeRef.current
    stopLocalTeacherVoice()
    setSpeakingMessageId(messageId)
    setVoiceStatus("正在准备教师语音…")
    let result: LocalTeacherVoiceResult
    try {
      result = await speakLocalTeacherVoice(content, {
        replyKind: "after_class.small_talk",
        onStart: () => {
          if (scope !== voiceScopeRef.current) return
          setVoiceStatus("正在播放教师语音")
        },
        onEnd: () => {
          if (scope !== voiceScopeRef.current) return
          setSpeakingMessageId("")
          setVoiceStatus("播放完成")
        },
        onError: reason => {
          if (scope !== voiceScopeRef.current) return
          setSpeakingMessageId("")
          setVoiceStatus(reason || "音频播放失败，请修复或重试声包")
        },
      })
    } catch (reason) {
      if (scope !== voiceScopeRef.current) return
      setSpeakingMessageId("")
      setVoiceStatus(
        reason instanceof Error && reason.message
          ? `教师语音暂不可用：${reason.message}`
          : "教师语音暂不可用，请稍后重试",
      )
      return
    }
    if (scope !== voiceScopeRef.current || result.ok) return
    setSpeakingMessageId("")
    if (result.reason !== "cancelled") {
      setVoiceStatus(result.reason || "教师语音不可用，请稍后重试")
    }
  }

  async function ensureSession(scope: string, generation: number, teacherGeneration: number) {
    const isCurrentScope = () => (
      generation === workspaceGenerationRef.current
      && casualWorkspaceId === scope
      && teacherGeneration === teacherGenerationRef.current
      && teacherIdRef.current === teacher.id
    )
    if (!scope || !isCurrentScope()) return ""
    const existing = sessionsRef.current[teacher.id]
    if (existing?.id) {
      if (existing.workspace_id && existing.workspace_id !== scope) return ""
      await request(`/casual-chat/sessions/${encodeURIComponent(existing.id)}`, {
        method: "PATCH",
        body: JSON.stringify({
          teacher_id: teacher.id,
          teacher_name: teacher.name,
          teacher_prompt: teacher.prompt,
          extra_setting: extraSetting.trim(),
        }),
      })
      if (!isCurrentScope()) return ""
      sessionsRef.current = {
        ...sessionsRef.current,
        [teacher.id]: { ...existing, extra_setting: extraSetting.trim() },
      }
      return existing.id
    }
    const created = await request<any>("/casual-chat/sessions", {
      method: "POST",
      body: JSON.stringify({
        workspace_id: scope,
        teacher_id: teacher.id,
        teacher_name: teacher.name,
        teacher_prompt: teacher.prompt,
        extra_setting: extraSetting.trim(),
      }),
    })
    if (!isCurrentScope()) return ""
    const createdId = String(created?.id || "")
    if (!createdId) throw new Error("闲聊会话创建失败")
    const createdSession: CasualSession = {
        ...created,
        id: createdId,
        workspace_id: scope,
        teacher_id: teacher.id,
        teacher_name: teacher.name,
        teacher_prompt: teacher.prompt,
        extra_setting: extraSetting.trim(),
        messages: [],
      }
    sessionsRef.current = { ...sessionsRef.current, [teacher.id]: createdSession }
    setSessionsByTeacher(current => ({ ...current, [teacher.id]: createdSession }))
    return createdId
  }

  async function send() {
    const value = input.trim()
    if (!value || busy || !teacher) return
    const scope = casualWorkspaceId
    const generation = workspaceGenerationRef.current
    const teacherGeneration = teacherGenerationRef.current
    const teacherAtRequest = teacher.id
    if (!scope) {
      setNotice("正在加载学习空间，请稍候再发送。")
      return
    }
    const userMessage: CasualMessage = { id: `user-${Date.now()}`, role: "user", content: value }
    setMessages(current => [...current, userMessage])
    setInput("")
    setBusy(true)
    setNotice("")
    try {
      const id = await ensureSession(scope, generation, teacherGeneration)
      if (!id || generation !== workspaceGenerationRef.current || casualWorkspaceId !== scope || teacherGeneration !== teacherGenerationRef.current || teacherIdRef.current !== teacherAtRequest) return
      const result = await request<any>(`/casual-chat/sessions/${encodeURIComponent(id)}/messages`, {
        method: "POST",
        body: JSON.stringify({ message: value, ...providerRequestContext() }),
      })
      if (generation !== workspaceGenerationRef.current || casualWorkspaceId !== scope || teacherGeneration !== teacherGenerationRef.current || teacherIdRef.current !== teacherAtRequest) return
      const reply = String(result.message || "我在听，你可以继续说。")
      const replyId = `teacher-${Date.now()}`
      const teacherMessage: CasualMessage = { id: replyId, role: "teacher", content: reply }
      setMessages(current => [...current, teacherMessage])
      setSessionsByTeacher(current => {
        const session = current[teacher.id]
        if (!session) return current
        const sessionMessages: NonNullable<CasualSession["messages"]> = [
          ...(session.messages || []),
          { role: "user" as const, content: value },
          { role: "teacher" as const, content: reply },
        ].slice(-24)
        const updatedSession = {
          ...session,
          updated_at: new Date().toISOString(),
          messages: sessionMessages,
        }
        sessionsRef.current = { ...current, [teacher.id]: updatedSession }
        return { ...current, [teacher.id]: updatedSession }
      })
    } catch (error: any) {
      if (generation !== workspaceGenerationRef.current || casualWorkspaceId !== scope || teacherGeneration !== teacherGenerationRef.current || teacherIdRef.current !== teacherAtRequest) return
      setNotice(error?.message || "闲聊服务暂时不可用，稍后再试")
    } finally {
      if (generation === workspaceGenerationRef.current && casualWorkspaceId === scope && teacherGeneration === teacherGenerationRef.current && teacherIdRef.current === teacherAtRequest) setBusy(false)
    }
  }

  return (
    <section className="casual-teacher-chat" data-testid="casual-teacher-chat" aria-label="和教师闲聊">
      <header className="casual-teacher-chat__header">
        <div>
          <div className="eyebrow"><MessageCircle size={14} /> AFTER CLASS LOUNGE</div>
          <h2>和教师聊两句</h2>
          <p>选择一位教师，补充一点聊天设定。闲聊不会改变任务、进度或掌握度。</p>
        </div>
        <div className="casual-teacher-chat__badge"><Sparkles size={15} /> 文字优先 · 按需朗读</div>
      </header>

      <div className="casual-teacher-chat__body">
        <aside className="casual-teacher-chat__teachers" aria-label="选择教师">
          <div className="casual-teacher-chat__label">选择教师</div>
          {teachers.map(item => (
            <button
              type="button"
              key={item.id}
              data-testid={`casual-teacher-option-${item.id}`}
              className={item.id === teacher.id ? "active" : ""}
              onClick={() => chooseTeacher(item.id)}
            >
              <span className="casual-teacher-chat__mini-avatar" style={{ background: item.color }}>{item.name.slice(0, 1)}</span>
              <span><b>{item.name}</b><small>{sessionPreview(sessionsByTeacher[item.id], item.teaching_style || "陪伴聊天")}</small></span>
              {sessionsByTeacher[item.id]?.updated_at && <time>{sessionTime(sessionsByTeacher[item.id]?.updated_at)}</time>}
            </button>
          ))}
          <label className="casual-teacher-chat__setting">
            <span><Settings2 size={14} /> 补充设定（可选）</span>
            <textarea data-testid="casual-teacher-setting" value={extraSetting} onChange={event => updateExtraSetting(event.target.value)} placeholder="例如：更活泼一点，多用生活化比喻" />
          </label>
          <div className="casual-teacher-chat__voice-toggle" data-testid="casual-teacher-voice-mode">
            <Volume2 size={15} /> 回答先显示，点击喇叭才朗读
          </div>
          <div className="casual-teacher-chat__voice-select" data-testid="casual-teacher-voice">
            <span>朗读音色</span>
            <b>系统配置的教师音色</b>
            <small>点击朗读时按需合成；普通用户无需填写语音 API Key。系统语音兼容回退仍需明确开启。</small>
          </div>
          <VoicePlaybackRateControl compact />
        </aside>

        <div className="casual-teacher-chat__conversation">
          <div className={`casual-teacher-chat__portrait${teacher.visual_manifest_url ? " has-dynamic-teacher" : ""}`} data-testid="casual-teacher-portrait" style={{ borderColor: teacher.color }}>
            <div className="casual-teacher-chat__portrait-glow" />
            {teacher.visual_manifest_url ? <Teacher2DStage teacherName={teacher.name} state={busy ? "talking" : "idle"} manifestUrl={teacher.visual_manifest_url} /> : <img src={teacher.portrait} alt={`${teacher.name}立绘`} />}
            <div className="casual-teacher-chat__portrait-caption"><b>{teacher.name}</b><span>{teacher.teaching_style || "陪伴闲聊"}</span></div>
          </div>
          <div className="casual-teacher-chat__messages" aria-live="polite">
            {messages.map(message => (
              <div key={message.id} className={`casual-teacher-chat__message ${message.role}`}>
                <div className="casual-teacher-chat__bubble">{message.content}</div>
                {message.role === "teacher" && <button type="button" data-testid="casual-teacher-speak" aria-label={speakingMessageId === message.id ? "停止朗读教师回复" : "朗读教师回复"} aria-pressed={speakingMessageId === message.id} title={speakingMessageId === message.id ? "停止朗读" : "听语音"} className={speakingMessageId === message.id ? "is-speaking" : ""} onClick={() => void readAloud(message.id, message.content)}><Volume2 aria-hidden="true" size={14} /><span>{speakingMessageId === message.id ? "停止朗读" : "听语音"}</span></button>}
              </div>
            ))}
            {busy && <div className="casual-teacher-chat__typing"><i /><i /><i /> 正在想一句轻松的回复…</div>}
          </div>
          <div className="casual-teacher-chat__composer">
            <textarea data-testid="casual-teacher-input" value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send() } }} placeholder={`和${teacher.name}聊点什么…`} />
            <button type="button" data-testid="casual-teacher-send" className="primary" onClick={() => void send()} disabled={busy || !input.trim()}><Send size={16} /> {busy ? "回复中" : "发送"}</button>
          </div>
          {voiceStatus && <div className="casual-teacher-chat__voice-status" role="status">{voiceStatus}</div>}
          {notice && <div className="casual-teacher-chat__notice" role="status">{notice}</div>}
        </div>
      </div>
    </section>
  )
}
