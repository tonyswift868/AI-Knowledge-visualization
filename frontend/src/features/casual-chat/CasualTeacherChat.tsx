import { useEffect, useMemo, useRef, useState } from "react"
import { ArrowRight, BookOpen, Check, FileText, MessageCircle, Send, Settings2, Sparkles, Volume2 } from "lucide-react"
import { activeRequest, activeTeacherPersonas, type ActiveTeacherPersona } from "../active-teaching/api"
import { providerRequestContext, request, syncCurrentProviderSession } from "../../providerConnection"
import { useMentorStore } from "../../stores/useMentorStore"
import {
  speakLocalTeacherVoice,
  stopLocalTeacherVoice,
  type LocalTeacherVoiceResult,
} from "../voice/localTeacherVoice"
import { Teacher2DStage } from "../teachers/presentation/Teacher2DStage"
import { VoicePlaybackRateControl } from "../voice/VoicePlaybackRateControl"
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
  direct_user_reference?: boolean
  direct_identifier?: { kind?: string; value?: string; match?: string }
  reference_resolution?: { status?: string; network_verified?: boolean; requires_user_confirmation?: boolean }
}

export type PlannerHandoff = {
  goal: string
  resource?: PlannerResource
  /** Selected public resources, kept alongside resource for old consumers. */
  resources?: PlannerResource[]
  /** A local file stays in memory until the final task editor uploads it. */
  materialFile?: File
  created_at: string
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
  const kind = String(item.media_kind || item.kind || item.source_kind || item.source_type || item.media_type || "").toLowerCase()
  if (kind.includes("video") || kind.includes("youtube") || kind.includes("bilibili")) return true
  return /\.(mp4|webm|mov|m4v|avi|mkv)(?:$|[?#])/i.test(String(item.url || ""))
}

function plannerSignal(value: unknown, fallback = "未知") {
  const text = String(value ?? "").trim()
  return text || fallback
}

function plannerResourceLabel(item: PlannerResource) {
  return plannerSignal(item.media_kind || item.kind || item.source_kind || item.source_type || item.media_type, "来源类型未知")
}

function plannerRankReason(item: PlannerResource) {
  return item.rank_reasons?.filter(Boolean).join("；") || item.rank_reason || item.reason || "排序理由未知"
}

function mergePlannerResources(...groups: PlannerResource[][]): PlannerResource[] {
  const result: PlannerResource[] = []
  const seen = new Set<string>()
  groups.flat().forEach((item) => {
    if (!item?.url) return
    const key = plannerResourceKey(item)
    if (!key || seen.has(key)) return
    seen.add(key)
    result.push(item)
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
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [sourceChoice, setSourceChoice] = useState<"none" | "upload" | "search">("none")
  const [lastSearch, setLastSearch] = useState("")
  const [lastProviderCacheRef, setLastProviderCacheRef] = useState("")
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [handoffDone, setHandoffDone] = useState(false)
  const [videoOnlyConfirmation, setVideoOnlyConfirmation] = useState(false)

  async function ask(messageOverride?: string) {
    const text = (messageOverride ?? input).trim()
    const isSearchRetry = Boolean(messageOverride && lastSearch && text === lastSearch)
    if (!text || busy) return
    if (sourceChoice === "upload") {
      setMessages(current => [...current, { role: "user", content: text }, { role: "assistant", content: selectedFile ? `已记下你的目标。确认“${selectedFile.name}”后，我会把它交给学习计划处理。` : "请先选择一个文件，我会在你确认后交给学习计划处理。" }])
      setGoal(text)
      setInput("")
      if (!selectedFile) setNotice("请先上传一份资料，再确认继续。")
      return
    }
    setSourceChoice("search")
    setLastSearch(text)
    setBusy(true)
    setNotice("")
    setMessages(current => [...current, { role: "user", content: text }])
    setInput("")
    try {
      let providerContext = providerRequestContext()
      let providerSyncTransportError = false
      if (!providerContext.provider_session_id) {
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
      if (!providerContext.provider_session_id && !providerSyncTransportError) {
        const providerState = useMentorStore.getState()
        throw new Error(
          providerState.provider === "fake"
            ? "规划助手需要已验证的真实 Provider；Fake 模式不会发起网络请求。"
            : "模型连接仍在验证，请稍候重试。",
        )
      }
      const result = await activeRequest(
        `/workspaces/${encodeURIComponent(workspaceId || "general-learning")}/planning-assistant`,
        {
          method: "POST",
          body: JSON.stringify({
            message: text,
            goal,
            conversation: messages,
            // Reaching this branch means the learner chose public discovery
            // (the upload branch returned above).  Send explicit consent;
            // don't make the backend infer it from wording or React state
            // timing after setSourceChoice("search").
            resource_search_authorized: true,
            retained_candidates: resources,
            selected_resources: resources.filter(item => selectedResourceKeys.includes(plannerResourceKey(item))),
            ...(isSearchRetry && lastProviderCacheRef
              ? { provider_cache_ref: lastProviderCacheRef }
              : {}),
            ...providerContext,
          }),
        },
        120_000,
      )
      const bundledCandidates = Array.isArray(result?.resource_bundle?.candidates)
        ? result.resource_bundle.candidates
        : result.resources
      const nextResources = (Array.isArray(bundledCandidates) ? bundledCandidates : [])
        .map((item: any) => ({ ...item, url: String(item?.url || "").trim() }))
        .filter((item: PlannerResource) => Boolean(item.url && plannerResourceKey(item)))
        .slice(0, 3) as PlannerResource[]
      setMessages(current => [...current, { role: "assistant", content: String(result.reply || "我已整理出下一步建议。") }])
      setSelectedResourceKeys(current => current.filter(key => nextResources.some(item => plannerResourceKey(item) === key) || resources.some(item => plannerResourceKey(item) === key)))
      setResources(current => mergePlannerResources(
        nextResources,
        current.filter(item => selectedResourceKeys.includes(plannerResourceKey(item))),
      ))
      setLastProviderCacheRef(String(result?.cache?.provider_ref || ""))
      const nextGoal = String(result.goal_summary || text).trim()
      if (nextGoal) {
        setGoal(nextGoal)
      }
      if (!nextResources.length) {
        setNotice((result.warnings || []).join("；") || "暂时没有可核对的公开资料候选，请补充范围或换一种描述。")
      }
    } catch (error: any) {
      setNotice(error?.message || "规划助手暂时不可用，请先验证模型连接后重试。")
    } finally {
      setBusy(false)
    }
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

  function handoffSelectedResources(videoOnlyAcknowledged = false) {
    const selectedResources = resources
      .filter(item => selectedResourceKeys.includes(plannerResourceKey(item)))
      .map(item => videoOnlyAcknowledged && isPlannerVideo(item)
        ? { ...item, video_only_acknowledged: true }
        : item)
    if (!selectedResources.length && !selectedFile) {
      setNotice("请选择至少一份候选资料，或选择“使用我的资料”并上传文件后继续。")
      return
    }
    const fallbackResource = selectedResources[0] || { url: "", title: selectedFile?.name || "我的学习资料", snippet: "来自你上传的资料" }
    const handoff: PlannerHandoff = {
      goal: goal.trim() || "根据已选资料建立可验证的学习计划",
      resource: fallbackResource,
      resources: selectedResources,
      materialFile: selectedFile || undefined,
      created_at: new Date().toISOString(),
    }
    setHandoffDone(true)
    onStartPlan?.(handoff)
  }

  function startPlan() {
    const selectedResources = resources.filter(item => selectedResourceKeys.includes(plannerResourceKey(item)))
    if (selectedResources.length && selectedResources.every(isPlannerVideo) && !selectedFile && !videoOnlyConfirmation) {
      setVideoOnlyConfirmation(true)
      return
    }
    handoffSelectedResources()
  }

  function selectUpload() {
    setSourceChoice("upload")
    setNotice("")
    setVideoOnlyConfirmation(false)
  }

  function selectSearch() {
    setSourceChoice("search")
    setSelectedFile(null)
    setNotice("")
  }

  function handleFile(file?: File) {
    if (!file) return
    setSelectedFile(file)
    setSourceChoice("upload")
    setResources([])
    setSelectedResourceKeys([])
    setLastSearch("")
    setNotice(`已选择“${file.name}”。确认后会在下一步上传并建立知识地图。`)
  }

  return (
    <section className="planning-companion" data-testid="learning-planner" aria-label="学习规划助手">
      <header className="planning-companion__header">
        <div>
          <div className="eyebrow"><BookOpen size={14} /> LEARNING PLANNER</div>
          <h2>创建计划</h2>
          <p>教师会把你的需求整理成目标，再从公开检索中挑出最多三份候选资料。你可以一起确认视频、笔记或讲义，才会进入资料整理和计划生成。</p>
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
            {busy && <div className="planning-companion__typing">正在整理需求并检索可核对资料…</div>}
          </div>
          <div className="planning-companion__source-choice" aria-label="选择资料来源">
            <span className="planning-companion__source-label">你准备使用哪类资料？</span>
            <button type="button" className={sourceChoice === "upload" ? "is-selected" : ""} onClick={selectUpload} data-testid="planning-companion-use-own">
              <BookOpen size={15} /> 使用我的资料
            </button>
            <button type="button" className={sourceChoice === "search" ? "is-selected" : ""} onClick={selectSearch} data-testid="planning-companion-search">
              <Sparkles size={15} /> 帮我找资料
            </button>
            <label className="planning-companion__upload">
              <input data-testid="planning-companion-upload" type="file" onChange={event => { handleFile(event.target.files?.[0]); event.target.value = "" }} />
              <span>{selectedFile ? `已选：${selectedFile.name}` : "上传文件"}</span>
            </label>
          </div>
          <div className="planning-companion__composer">
            <textarea data-testid="planning-companion-input" value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void ask() } }} placeholder={sourceChoice === "upload" ? "告诉教师这份资料要解决什么目标、在何时完成…" : "告诉教师你的学习目标、面向的场景、已有资料和希望完成的时间…"} />
            <button type="button" data-testid="planning-companion-send" className="primary" onClick={() => void ask()} disabled={busy || !input.trim()}><Send size={16} /> {busy ? "整理中" : "开始规划"}</button>
          </div>
          {sourceChoice === "upload" && !selectedFile && <div className="planning-companion__hint" role="status">请选择一个文件；你的资料只会在你确认后上传。</div>}
          {resources.length > 0 && (
            <section className="planning-companion__resources" aria-label="推荐资料">
              <div className="planning-companion__resources-head"><div><b>推荐资料（可多选）</b><span>候选来自实时检索；可保留已选资料，再补充一轮搜索。</span></div><span className="planning-companion__verified-note">待你确认</span></div>
              <fieldset className="planning-companion__resource-grid" aria-label="候选资料">
                <legend className="sr-only">候选资料，可选择多份</legend>
                {resources.map((item, index) => (
                  <label key={`${plannerResourceKey(item)}-${index}`} className={`planning-companion__resource ${selectedResourceKeys.includes(plannerResourceKey(item)) ? "is-selected" : ""}`}>
                    <input type="checkbox" name="planner-resource" checked={selectedResourceKeys.includes(plannerResourceKey(item))} onChange={() => toggleResource(item)} aria-label={`选择 ${item.title || item.url}`} />
                    <span className="planning-companion__resource-index">0{index + 1}</span>
                    <span className="planning-companion__resource-copy"><strong title={item.title || item.url}>{item.title || item.url}</strong>{item.direct_user_reference && <small>精确引用 · 由你提供，尚未联网核验</small>}<small>{plannerResourceLabel(item)} · 角色：{plannerSignal(item.coverage_role || item.collection || item.collection_name)}</small><small>权威：{plannerSignal(item.authority_status || item.authority || item.authority_signal || (item.authority_explanation ? "已提供说明" : ""))} · 热度：{plannerSignal(item.popularity_status || item.popularity || item.popularity_signal)}</small><em>{plannerRankReason(item)}</em><a href={item.url} target="_blank" rel="noreferrer" aria-label={`打开“${item.title || item.url}”原文`} onClick={event => event.stopPropagation()}>打开原文 <ArrowRight size={13} /></a></span>
                  </label>
                ))}
              </fieldset>
              {selectedResourceKeys.length > 0 && <div className="planning-companion__bundle" aria-live="polite"><b>已选资料包 · {selectedResourceKeys.length} 份</b><div>{resources.filter(item => selectedResourceKeys.includes(plannerResourceKey(item))).map(item => <button type="button" key={plannerResourceKey(item)} onClick={() => removeSelectedResource(item)} aria-label={`移除 ${item.title || item.url}`}>{item.title || item.url} ×</button>)}</div></div>}
              {videoOnlyConfirmation && <div className="planning-companion__video-confirmation" data-testid="planning-video-only-confirmation" role="alert"><b>当前资料包只有视频</b><span>视频转写可能遗漏公式、图表或上下文。建议先添加笔记或讲义。</span><div><button type="button" onClick={() => { setVideoOnlyConfirmation(false); selectSearch(); setNotice("请继续搜索或上传笔记，再回到资料包确认。") }}>添加笔记</button><button type="button" className="secondary" onClick={() => { setVideoOnlyConfirmation(false); handoffSelectedResources(true) }}>继续仅使用视频</button></div></div>}
              <button type="button" className="planning-companion__start" data-testid="planning-companion-start" onClick={startPlan} disabled={!selectedResourceKeys.length || handoffDone}>{handoffDone ? <><Check size={16} /> 已交给学习计划</> : <>采用已选资料包，开始整理 <ArrowRight size={16} /></>}</button>
            </section>
          )}
          {selectedFile && <section className="planning-companion__resources planning-companion__local-resource" aria-label="我的资料">
            <div className="planning-companion__resources-head"><div><b>我的资料</b><span>文件会在你确认后交给学习计划处理。</span></div><span className="planning-companion__verified-note">待你确认</span></div>
            <div className="planning-companion__local-file"><FileText size={18} /><strong>{selectedFile.name}</strong><small>{Math.max(1, Math.round(selectedFile.size / 1024))} KB · {selectedFile.type || "文件"}</small></div>
            <button type="button" className="planning-companion__start" data-testid="planning-companion-start" onClick={startPlan} disabled={handoffDone}>采用我的资料，开始整理 <ArrowRight size={16} /></button>
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

export function CasualTeacherChat({ workspaceId = "", mode = "casual", onStartPlan }: { workspaceId?: string; mode?: "planner" | "casual"; onStartPlan?: (handoff: PlannerHandoff) => void }) {
  if (mode === "planner") return <LearningPlanner workspaceId={workspaceId} onStartPlan={onStartPlan} />
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
  const teacher = useMemo(() => teachers.find(item => item.id === teacherId) || teachers[0], [teacherId, teachers])

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
    setSessionsLoaded(false)
    setHydratedTeacherId("")
    const loadSessions = async () => {
      try {
        const query = `?workspace_id=${encodeURIComponent(workspaceId)}`
        const result = await request<{ items?: CasualSession[] }>(`/casual-chat/sessions${query}`)
        if (cancelled) return
        const next: Record<string, CasualSession> = {}
        for (const session of result.items || []) {
          if (session?.teacher_id) next[session.teacher_id] = session
        }
        sessionsRef.current = next
        setSessionsByTeacher(next)
      } catch {
        // A fresh local companion remains usable even if the history endpoint
        // is temporarily unavailable.
      } finally {
        if (!cancelled) setSessionsLoaded(true)
      }
    }
    void loadSessions()
    return () => { cancelled = true }
  }, [workspaceId])

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
  }, [sessionsLoaded, teacher?.id, teacherId])

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
    if (!teacher || !sessionsLoaded || hydratedTeacherId !== teacher.id || !session?.id) return
    const timer = window.setTimeout(() => {
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
  }, [extraSetting, teacher?.id, sessionsLoaded, hydratedTeacherId])

  function chooseTeacher(id: string) {
    if (id === teacherId) return
    voiceScopeRef.current += 1
    stopLocalTeacherVoice()
    explicitTeacherSelectionRef.current = true
    settingDirtyRef.current = false
    settingDraftRef.current = undefined
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

  async function ensureSession() {
    const existing = sessionsRef.current[teacher.id]
    if (existing?.id) {
      await request(`/casual-chat/sessions/${encodeURIComponent(existing.id)}`, {
        method: "PATCH",
        body: JSON.stringify({
          teacher_id: teacher.id,
          teacher_name: teacher.name,
          teacher_prompt: teacher.prompt,
          extra_setting: extraSetting.trim(),
        }),
      })
      sessionsRef.current = {
        ...sessionsRef.current,
        [teacher.id]: { ...existing, extra_setting: extraSetting.trim() },
      }
      return existing.id
    }
    const created = await request<any>("/casual-chat/sessions", {
      method: "POST",
      body: JSON.stringify({
        workspace_id: workspaceId,
        teacher_id: teacher.id,
        teacher_name: teacher.name,
        teacher_prompt: teacher.prompt,
        extra_setting: extraSetting.trim(),
      }),
    })
    const createdSession: CasualSession = {
        ...created,
        id: String(created.id),
        teacher_id: teacher.id,
        teacher_name: teacher.name,
        teacher_prompt: teacher.prompt,
        extra_setting: extraSetting.trim(),
        messages: [],
      }
    sessionsRef.current = { ...sessionsRef.current, [teacher.id]: createdSession }
    setSessionsByTeacher(current => ({ ...current, [teacher.id]: createdSession }))
    return String(created.id)
  }

  async function send() {
    const value = input.trim()
    if (!value || busy || !teacher) return
    const userMessage: CasualMessage = { id: `user-${Date.now()}`, role: "user", content: value }
    setMessages(current => [...current, userMessage])
    setInput("")
    setBusy(true)
    setNotice("")
    try {
      const id = await ensureSession()
      const result = await request<any>(`/casual-chat/sessions/${encodeURIComponent(id)}/messages`, {
        method: "POST",
        body: JSON.stringify({ message: value, ...providerRequestContext() }),
      })
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
      setNotice(error?.message || "闲聊服务暂时不可用，稍后再试")
    } finally {
      setBusy(false)
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
