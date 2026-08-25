import { useEffect, useRef, useState } from "react"
import { BookOpen, Check, Copy, Pause, Play, Send, Square } from "lucide-react"

import { teacherLiveApi } from "./api"
import { TEACHER_LIVE_DEMO_TURNS } from "./demoFixture"
import type {
  TeacherLiveApiClient,
  TeacherLiveCitation,
  TeacherLiveEvent,
  TeacherLiveMetrics,
  TeacherLiveSession,
  TeacherResponseMode,
} from "./types"
import "./teacher-live.css"


type LiveMessage = {
  id: string
  role: "learner" | "teacher"
  content: string
  streaming?: boolean
  responseMode?: TeacherResponseMode
  citations: TeacherLiveCitation[]
  metrics?: TeacherLiveMetrics
  error?: string
  retryable?: boolean
  turnId?: string
  idempotencyKey?: string
  originalMessage?: string
}


function identity(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
  return `${prefix}-${random}`
}


function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}


function stringValue(value: unknown): string {
  return typeof value === "string" ? value : ""
}


function modeValue(value: unknown): TeacherResponseMode | undefined {
  return value === "fast" || value === "deep" ? value : undefined
}


function initialMessages(): LiveMessage[] {
  return [{
    id: "teacher-live-welcome",
    role: "teacher",
    content: "欢迎来到实时课堂。选一个示例问题，或者直接输入你卡住的地方。",
    citations: [],
  }]
}


export function TeacherLiveClassroom({
  client = teacherLiveApi,
  currentConcept = "HashMap threshold",
}: {
  client?: TeacherLiveApiClient
  currentConcept?: string
}) {
  const [session, setSession] = useState<TeacherLiveSession | null>(null)
  const [messages, setMessages] = useState<LiveMessage[]>(initialMessages)
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const [sessionBusy, setSessionBusy] = useState(true)
  const [notice, setNotice] = useState("正在准备课堂…")
  const [evidenceVisible, setEvidenceVisible] = useState(false)
  const [copiedId, setCopiedId] = useState("")
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const createStartedRef = useRef(false)

  useEffect(() => {
    if (createStartedRef.current) return
    createStartedRef.current = true
    let active = true
    client.createSession({ current_concept: currentConcept })
      .then(created => {
        if (!active) return
        setSession(created)
        setNotice(
          created.provider_mode === "DEMO_FAKE"
            ? "离线演示已就绪"
            : "真实 Provider 课堂已就绪",
        )
      })
      .catch(error => {
        if (!active) return
        setNotice(error instanceof Error ? error.message : "课堂创建失败，请刷新后重试。")
      })
      .finally(() => {
        if (active) setSessionBusy(false)
      })
    return () => {
      active = false
      abortRef.current?.abort()
    }
  }, [client, currentConcept])

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" })
  }, [messages])

  const updateMessage = (id: string, update: (message: LiveMessage) => LiveMessage) => {
    setMessages(items => items.map(item => item.id === id ? update(item) : item))
  }

  const handleEvent = (teacherMessageId: string, event: TeacherLiveEvent) => {
    if (event.event === "turn_started") {
      updateMessage(teacherMessageId, message => ({
        ...message,
        responseMode: modeValue(event.data.response_mode) || message.responseMode,
      }))
      return
    }
    if (event.event === "response_chunk") {
      const delta = stringValue(event.data.text_delta)
      updateMessage(teacherMessageId, message => ({
        ...message,
        content: message.content + delta,
        streaming: true,
        responseMode: modeValue(event.data.response_mode) || message.responseMode,
      }))
      return
    }
    if (event.event === "citation") {
      const citation: TeacherLiveCitation = {
        evidence_id: stringValue(event.data.evidence_id),
        source_reference: stringValue(event.data.source_reference) || null,
        excerpt: stringValue(event.data.excerpt) || null,
      }
      if (citation.evidence_id) {
        updateMessage(teacherMessageId, message => ({
          ...message,
          citations: message.citations.some(item => item.evidence_id === citation.evidence_id)
            ? message.citations
            : [...message.citations, citation],
        }))
      }
      return
    }
    if (event.event === "metrics") {
      const metrics: TeacherLiveMetrics = {
        response_mode: modeValue(event.data.response_mode) || "fast",
        ttft_ms: numberValue(event.data.ttft_ms),
        total_response_ms: numberValue(event.data.total_response_ms) || 0,
        provider_call_count: numberValue(event.data.provider_call_count) || 0,
        native_provider_streaming: event.data.native_provider_streaming === true,
        input_tokens: numberValue(event.data.input_tokens),
        output_tokens: numberValue(event.data.output_tokens),
        cached_input_tokens: numberValue(event.data.cached_input_tokens),
        cache_status: ["hit", "miss", "not_supported"].includes(stringValue(event.data.cache_status))
          ? stringValue(event.data.cache_status) as TeacherLiveMetrics["cache_status"]
          : "unknown",
      }
      updateMessage(teacherMessageId, message => ({
        ...message,
        metrics,
        responseMode: metrics.response_mode,
      }))
      return
    }
    if (event.event === "completed") {
      updateMessage(teacherMessageId, message => ({
        ...message,
        streaming: false,
        responseMode: modeValue(event.data.response_mode) || message.responseMode,
      }))
      return
    }
    if (event.event === "error") {
      updateMessage(teacherMessageId, message => ({
        ...message,
        streaming: false,
        error: stringValue(event.data.message) || "这轮课堂没有完成。",
        retryable: event.data.retryable === true,
      }))
    }
  }

  const runTurn = async (
    learnerText: string,
    retry?: { teacherMessageId: string; turnId: string; idempotencyKey: string },
  ) => {
    const text = learnerText.trim()
    if (!text || !session || session.state !== "active" || busy) return
    const turnId = retry?.turnId || identity("turn")
    const idempotencyKey = retry?.idempotencyKey || identity("idem")
    const teacherMessageId = retry?.teacherMessageId || identity("teacher")
    if (retry) {
      updateMessage(teacherMessageId, message => ({
        ...message,
        content: "",
        error: undefined,
        retryable: false,
        streaming: true,
        citations: [],
        metrics: undefined,
      }))
    } else {
      setMessages(items => [
        ...items,
        { id: identity("learner"), role: "learner", content: text, citations: [] },
        {
          id: teacherMessageId,
          role: "teacher",
          content: "",
          streaming: true,
          citations: [],
          turnId,
          idempotencyKey,
          originalMessage: text,
        },
      ])
    }
    setInput("")
    setBusy(true)
    setNotice("老师正在组织回答…")
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const turn = await client.submitTurn(session.session_id, {
        message: text,
        turn_id: turnId,
        idempotency_key: idempotencyKey,
      })
      if (turn.duplicate) setNotice("已恢复上次回复，没有重复调用 Provider")
      await client.streamTurn(
        turn.stream_path,
        event => handleEvent(teacherMessageId, event),
        { signal: controller.signal },
      )
      setNotice(turn.duplicate ? "已恢复本轮记录" : "回复完成")
    } catch (error) {
      if ((error as Error)?.name !== "AbortError") {
        updateMessage(teacherMessageId, message => ({
          ...message,
          streaming: false,
          error: error instanceof Error ? error.message : "课堂连接中断，请稍后重试。",
          retryable: true,
        }))
        setNotice("这轮没有完成")
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setBusy(false)
    }
  }

  const togglePause = async () => {
    if (!session || busy) return
    setSessionBusy(true)
    try {
      const updated = session.state === "paused"
        ? await client.resumeSession(session.session_id)
        : await client.pauseSession(session.session_id)
      setSession(updated)
      setNotice(updated.state === "paused" ? "课堂已暂停" : "课堂已恢复，可以继续提问")
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "课堂状态更新失败。")
    } finally {
      setSessionBusy(false)
    }
  }

  const abortClassroom = async () => {
    if (!session || session.state === "aborted") return
    abortRef.current?.abort()
    setSessionBusy(true)
    try {
      const updated = await client.abortSession(session.session_id)
      setSession(updated)
      setMessages(items => items.map(item => item.streaming ? {
        ...item,
        streaming: false,
        error: "本次回复已取消，课堂已经结束。",
      } : item))
      setNotice("课堂已结束")
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "课堂暂时无法结束。")
    } finally {
      setBusy(false)
      setSessionBusy(false)
    }
  }

  const copyResponse = async (message: LiveMessage) => {
    if (!message.content || !navigator.clipboard?.writeText) return
    await navigator.clipboard.writeText(message.content)
    setCopiedId(message.id)
    window.setTimeout(() => setCopiedId(""), 1200)
  }

  const providerMode = session?.provider_mode || "DEMO_FAKE"
  const canSend = Boolean(
    session
    && session.state === "active"
    && input.trim()
    && !busy
    && !sessionBusy,
  )

  return (
    <main className="teacher-live-shell" data-testid="teacher-live-classroom">
      <header className="teacher-live-header">
        <div className="teacher-live-title-group">
          <span className="teacher-live-kicker">LIVE CLASSROOM</span>
          <h1>Teacher 实时课堂</h1>
          <p>围绕一个概念，边生成边交流。</p>
        </div>
        <div className="teacher-live-status-cluster">
          <span
            className={`teacher-live-provider teacher-live-provider--${providerMode.toLowerCase().replace("_", "-")}`}
            data-testid="teacher-live-provider-mode"
          >
            {providerMode}
          </span>
          <span className="teacher-live-session-state" data-testid="teacher-live-session-state">
            {session?.state === "paused" ? "已暂停" : session?.state === "aborted" ? "已结束" : "进行中"}
          </span>
        </div>
      </header>

      {providerMode === "DEMO_FAKE" && session && (
        <div className="teacher-live-demo-banner" role="note">
          <strong>离线 DEMO</strong>
          <span>当前没有使用真实 AI，也不会消耗 Provider 配额。</span>
        </div>
      )}

      <section className="teacher-live-context" aria-label="当前课堂上下文">
        <div>
          <BookOpen aria-hidden="true" size={18} />
          <span>当前概念</span>
          <strong data-testid="teacher-live-current-concept">{session?.current_concept || currentConcept}</strong>
        </div>
        <button
          type="button"
          className={evidenceVisible ? "is-active" : ""}
          onClick={() => setEvidenceVisible(value => !value)}
          aria-pressed={evidenceVisible}
          data-testid="teacher-live-evidence-toggle"
        >
          {evidenceVisible ? "收起引用" : "查看引用"}
        </button>
      </section>

      <section className="teacher-live-dialogue" aria-label="课堂对话" aria-live="polite">
        {messages.map(message => (
          <article
            key={message.id}
            className={`teacher-live-message teacher-live-message--${message.role}`}
            data-testid={`teacher-live-message-${message.role}`}
          >
            <div className="teacher-live-message-meta">
              <span>{message.role === "teacher" ? "Teacher" : "你"}</span>
              {message.responseMode && (
                <span
                  className={`teacher-live-mode teacher-live-mode--${message.responseMode}`}
                  data-testid="teacher-live-response-mode"
                >
                  {message.responseMode.toUpperCase()}
                </span>
              )}
            </div>
            <div className="teacher-live-bubble">
              {message.content || (message.streaming ? "正在生成" : "")}
              {message.streaming && <span className="teacher-live-caret" aria-label="正在流式生成">▍</span>}
            </div>
            {message.error && (
              <div className="teacher-live-error" role="alert">
                <span>{message.error}</span>
                {message.retryable && message.originalMessage && message.turnId && message.idempotencyKey && session?.state === "active" && (
                  <button
                    type="button"
                    onClick={() => runTurn(message.originalMessage || "", {
                      teacherMessageId: message.id,
                      turnId: message.turnId || "",
                      idempotencyKey: message.idempotencyKey || "",
                    })}
                    disabled={busy}
                    data-testid="teacher-live-retry"
                  >
                    安全重试
                  </button>
                )}
              </div>
            )}
            {message.role === "teacher" && message.content && !message.streaming && (
              <div className="teacher-live-message-actions">
                <button type="button" onClick={() => copyResponse(message)} aria-label="复制教师回复">
                  {copiedId === message.id ? <Check size={14} /> : <Copy size={14} />}
                  {copiedId === message.id ? "已复制" : "复制"}
                </button>
              </div>
            )}
            {evidenceVisible && message.citations.length > 0 && (
              <div className="teacher-live-citations" data-testid="teacher-live-citations">
                {message.citations.map(citation => (
                  <a
                    key={citation.evidence_id}
                    id={`citation-${citation.evidence_id}`}
                    href={`#citation-${citation.evidence_id}`}
                  >
                    <strong>{citation.evidence_id}</strong>
                    {citation.excerpt && <span>{citation.excerpt}</span>}
                    {citation.source_reference && <small>{citation.source_reference}</small>}
                  </a>
                ))}
              </div>
            )}
            {message.metrics && (
              <details className="teacher-live-diagnostics" data-testid="teacher-live-diagnostics">
                <summary>响应诊断</summary>
                <dl>
                  <div><dt>TTFT</dt><dd>{message.metrics.ttft_ms?.toFixed(1) ?? "未知"} ms</dd></div>
                  <div><dt>总耗时</dt><dd>{message.metrics.total_response_ms.toFixed(1)} ms</dd></div>
                  <div><dt>Provider 调用</dt><dd>{message.metrics.provider_call_count}</dd></div>
                </dl>
                {!message.metrics.native_provider_streaming && (
                  <p>当前真实适配器返回整段文本后再传输；此 TTFT 不能当作原生 Provider 首 token。</p>
                )}
              </details>
            )}
          </article>
        ))}
        <div ref={bottomRef} />
      </section>

      {session?.demo_fixture && (
        <section className="teacher-live-prompts" aria-label="离线演示问题">
          {TEACHER_LIVE_DEMO_TURNS.map(turn => (
            <button
              key={turn}
              type="button"
              onClick={() => runTurn(turn)}
              disabled={busy || session.state !== "active"}
            >
              {turn}
            </button>
          ))}
        </section>
      )}

      <section className="teacher-live-composer" aria-label="输入问题">
        <textarea
          value={input}
          onChange={event => setInput(event.target.value)}
          onKeyDown={event => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault()
              if (canSend) runTurn(input)
            }
          }}
          placeholder={session?.state === "paused" ? "课堂已暂停" : "输入你想问的问题…"}
          disabled={!session || session.state !== "active" || busy || sessionBusy}
          aria-label="课堂问题"
          data-testid="teacher-live-input"
        />
        <button
          type="button"
          className="teacher-live-send"
          onClick={() => runTurn(input)}
          disabled={!canSend}
          aria-label="发送问题"
          data-testid="teacher-live-send"
        >
          <Send size={18} />
          发送
        </button>
      </section>

      <footer className="teacher-live-controls">
        <span role="status">{notice}</span>
        <div>
          <button
            type="button"
            onClick={togglePause}
            disabled={!session || busy || sessionBusy || session.state === "aborted"}
            data-testid="teacher-live-pause-resume"
          >
            {session?.state === "paused" ? <Play size={16} /> : <Pause size={16} />}
            {session?.state === "paused" ? "恢复" : "暂停"}
          </button>
          <button
            type="button"
            className="teacher-live-abort"
            onClick={abortClassroom}
            disabled={!session || sessionBusy || session.state === "aborted"}
            data-testid="teacher-live-abort"
          >
            <Square size={15} />
            {busy ? "取消并结束" : "结束课堂"}
          </button>
        </div>
      </footer>
    </main>
  )
}
