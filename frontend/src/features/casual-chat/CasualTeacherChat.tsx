import { useEffect, useMemo, useRef, useState } from "react"
import { MessageCircle, Send, Settings2, Sparkles, Volume2 } from "lucide-react"
import { activeTeacherPersonas, type ActiveTeacherPersona } from "../active-teaching/api"
import { providerRequestContext, request } from "../../providerConnection"
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

export function CasualTeacherChat({ workspaceId = "" }: { workspaceId?: string }) {
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
