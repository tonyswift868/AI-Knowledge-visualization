import * as React from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { BookOpen, Check, Copy, FilePlus2, LoaderCircle, MessageSquarePlus, SendHorizontal, Square, Trash2, X } from "lucide-react"
import { call, on } from "../bridge"
import { useApp } from "../store"
import { useRoundScroll } from "../useRoundScroll"
import { Button, ModelFaultPanel, useModelRetry } from "../ui"
import { Loading } from "../loading/Loading"
import "./rag.css"

type Plan = { id: string; title: string; document_ids: string[] }
type Document = { id: string; title: string; source: string; plan_ids: string[]; status: "not_uploaded" | "uploading" | "ready" | "failed"; total_chunks: number; uploaded_chunks: number; error?: string }
type Passage = { number?: number; document_id: string; seq: number; text: string; page?: number | null; section?: string | null; title?: string }
type Message = { id: string; role: "user" | "assistant"; content: string; passages?: Passage[]; supplement?: boolean }
type SessionSummary = { id: string; title: string; document_ids: string[]; updated_at: string; count: number }
type Session = SessionSummary & { messages: Message[] }
type SourcesResponse = { plans: Plan[]; documents: Document[]; sessions: SessionSummary[]; server_status?: { state: "ready" | "unavailable"; message?: string } }

const sameSet = (a: string[], b: string[]) => a.length === b.length && [...a].sort().every((x, i) => x === [...b].sort()[i])

export function Rag() {
  const toast = useApp((s) => s.toast)
  const modelFault = useApp((s) => s.modelFault)
  const [sources, setSources] = React.useState<SourcesResponse | null>(null)
  const [loadError, setLoadError] = React.useState("")
  const [planId, setPlanId] = React.useState("")
  const [selected, setSelected] = React.useState<string[]>([])
  const [session, setSession] = React.useState<Session | null>(null)
  const [question, setQuestion] = React.useState("")
  const [pending, setPending] = React.useState<{ text: string; reply: string } | null>(null)
  const [sendFailure, setSendFailure] = React.useState<{ text: string; message: string } | null>(null)
  const [busy, setBusy] = React.useState<"ingest" | "import" | "session" | "send" | "" >("")
  const [progress, setProgress] = React.useState<Record<string, Partial<Document>>>({})
  const [sourceOpen, setSourceOpen] = React.useState<{ messageId: string; number: number } | null>(null)
  const abort = React.useRef<AbortController | null>(null)
  const mounted = React.useRef(false)
  const sourcesFlight = React.useRef<Promise<SourcesResponse> | null>(null)
  const probeRagStatus = React.useRef<(() => void) | null>(null)
  const scrollRef = React.useRef<HTMLDivElement>(null)
  const beginRound = useRoundScroll(scrollRef, session?.id)
  useModelRetry(() => { void send() }, (fault) => fault.purpose === "rag_qa" || fault.purpose === "rag.send")

  function clearSendFailure() {
    setSendFailure(null)
    const fault = useApp.getState().modelFault
    if (fault && (fault.purpose === "rag_qa" || fault.purpose === "rag.send")) {
      useApp.getState().set({ modelFault: null })
    }
  }

  const refresh = React.useCallback(async () => {
    if (sourcesFlight.current) return sourcesFlight.current
    const flight = call<SourcesResponse>("rag.sources").then((result) => {
      if (mounted.current) {
        setSources(result)
        setLoadError("")
      }
      return result
    })
    sourcesFlight.current = flight
    try {
      return await flight
    } finally {
      if (sourcesFlight.current === flight) sourcesFlight.current = null
    }
  }, [])
  React.useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    let inFlight = false
    let pollRequired = false
    let delay = 1500
    mounted.current = true
    const clearScheduled = () => {
      if (timer !== null) clearTimeout(timer)
      timer = null
    }
    const schedule = () => {
      if (!alive || !pollRequired || timer !== null || inFlight) return
      const jittered = delay * (0.8 + Math.random() * 0.4)
      timer = setTimeout(() => {
        timer = null
        void load(false)
      }, jittered)
      delay = Math.min(delay * 2, 60_000)
    }
    const load = async (initial: boolean) => {
      if (!alive || inFlight) return
      inFlight = true
      try {
        const result = await refresh()
        if (!alive) return
        if (initial && result.sessions[0]) void openSession(result.sessions[0].id)
        pollRequired = result.server_status?.state === "unavailable"
        if (!pollRequired) delay = 1500
      } catch (e) {
        if (!alive) return
        pollRequired = true
        if (initial) setLoadError((e as Error).message)
      } finally {
        inFlight = false
        schedule()
      }
    }
    probeRagStatus.current = () => {
      clearScheduled()
      if (!inFlight) void load(false)
    }
    void load(true)
    const off = on("rag.progress", (event) => {
      const id = String(event?.id ?? event?.document_id ?? "")
      if (!id) return
      setProgress((old) => ({ ...old, [id]: event }))
      setSources((old) => old ? { ...old, documents: old.documents.map((doc) => doc.id === id ? { ...doc, ...event, id } : doc) } : old)
    })
    return () => { alive = false; mounted.current = false; probeRagStatus.current = null; clearScheduled(); off(); abort.current?.abort() }
  }, [refresh])

  async function openSession(id: string) {
    try {
      const result = await call<{ session: Session }>("rag.get", { session_id: id })
      setSession(result.session)
      setSelected(result.session.document_ids)
      clearSendFailure()
    } catch (e) { toast("error", (e as Error).message) }
  }
  function toggleDocument(id: string) {
    clearSendFailure()
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  }
  function selectPlan(id: string) {
    clearSendFailure()
    setPlanId(id)
    if (!id) return
    const plan = sources?.plans.find((item) => item.id === id)
    if (plan) setSelected((current) => [...new Set([...current, ...plan.document_ids])])
  }
  function togglePlan(id: string) {
    clearSendFailure()
    const ids = sources?.plans.find((item) => item.id === id)?.document_ids ?? []
    setSelected((current) => ids.length && ids.every((docId) => current.includes(docId))
      ? current.filter((docId) => !ids.includes(docId)) : [...new Set([...current, ...ids])])
  }
  async function importFiles() {
    setBusy("import")
    try {
      const picked = await call<{ paths: string[] }>("rag.pick_files")
      if (!picked.paths?.length) return
      const result = await call<{ documents: Document[] }>("rag.import_files", { paths: picked.paths })
      const imported = result.documents ?? []
      setSources((current) => current ? { ...current, documents: mergeDocuments(current.documents, imported) } : current)
      setSelected((current) => [...new Set([...current, ...imported.map((doc) => doc.id)])])
      await refresh()
    } catch (e) { toast("error", (e as Error).message) }
    finally { setBusy("") }
  }
  async function ingest() {
    const documentIds = selectedDocuments.filter((doc) => doc.status !== "ready").map((doc) => doc.id)
    if (!documentIds.length || busy) return
    setBusy("ingest")
    try {
      const result = await call<{ documents: Document[] }>("rag.ingest", { document_ids: documentIds })
      const updated = result.documents ?? []
      if (updated.length) setSources((current) => current ? { ...current, documents: mergeDocuments(current.documents, updated) } : current)
      const fresh = await refresh()
      const byReturnedId = new Map(updated.map((doc) => [doc.id, doc]))
      const finalDocs = documentIds.map((id) => byReturnedId.get(id) ?? fresh.documents.find((doc) => doc.id === id)).filter((doc): doc is Document => !!doc)
      const failed = finalDocs.filter((doc) => doc.status !== "ready")
      if (failed.length) toast("error", `${failed.length} 份资料未完成入库：${failed.map((doc) => `${doc.title}${doc.error ? `（${doc.error}）` : ""}`).join("、")}`)
      else toast("ok", "所选资料已完成入库。")
    } catch (e) { toast("error", (e as Error).message) }
    finally { setBusy("") }
  }
  async function newSession() {
    if (!selected.length || busy || pending) return
    setBusy("session")
    try {
      const result = await call<{ session: Session }>("rag.create", { document_ids: selected })
      setSession(result.session)
      clearSendFailure()
      await refresh()
    } catch (e) { toast("error", (e as Error).message) }
    finally { setBusy("") }
  }
  async function ensureSession(): Promise<Session> {
    if (session && sameSet(session.document_ids, selected)) return session
    const existing = sources?.sessions.find((item) => sameSet(item.document_ids, selected))
    if (existing) {
      const result = await call<{ session: Session }>("rag.get", { session_id: existing.id })
      setSession(result.session)
      return result.session
    }
    const result = await call<{ session: Session }>("rag.create", { document_ids: selected })
    setSession(result.session)
    return result.session
  }
  async function send(retryText?: string) {
    const text = (retryText ?? question).trim()
    if (!text || !selected.length || pending || busy) return
    const notReady = selected.some((id) => docById(id)?.status !== "ready")
    if (notReady) {
      toast("info", "请先完成资料入库，再开始提问。")
      return
    }
    setSourceOpen(null)
    clearSendFailure()
    setBusy("send")
    try {
      const activeSession = await ensureSession()
      beginRound(activeSession.id)
      setQuestion("")
      setPending({ text, reply: "" })
      const controller = new AbortController()
      abort.current = controller
      const result = await call<{ session: Session }>("rag.send", { session_id: activeSession.id, text }, {
        signal: controller.signal,
        onStream: (delta) => setPending((current) => current && { ...current, reply: current.reply + delta }),
      })
      setSession(result.session)
      await refresh()
    } catch (e) {
      const error = e as Error & { code?: string; model_fault?: { category?: string; purpose?: string } }
      setQuestion((current) => current || text)
      if (error.code === "CANCELLED") toast("info", error.message)
      else {
        const fault = error.model_fault
        const retryable = fault?.category === "network" || error.code === "RAG_UNAVAILABLE"
          || (error.code === "RAG_HTTP" && /HTTP (408|429|5\d\d)/.test(error.message))
        if (retryable) {
          setSendFailure({ text, message: error.message })
          if (fault && (fault.purpose === "rag_qa" || fault.purpose === "rag.send")) useApp.getState().set({ modelFault: null })
          probeRagStatus.current?.()
        } else {
          setSendFailure(null)
        }
        toast("error", error.message)
      }
    } finally { abort.current = null; setPending(null); setBusy("") }
  }
  async function deleteSession(id: string) {
    try {
      await call("rag.delete", { session_id: id })
      if (session?.id === id) { setSession(null); clearSendFailure() }
      await refresh()
    } catch (e) { toast("error", (e as Error).message) }
  }
  function docById(id: string) { return sources?.documents.find((doc) => doc.id === id) }

  if (!sources) return <div className="page rag-page" data-testid="rag-page">
    {loadError ? <div className="card" role="alert">读取资料失败：{loadError}<Button onClick={() => refresh().catch((e) => setLoadError(e.message))}>重试</Button></div>
      : <Loading shape="page" label="正在读取资料与问答记录…" onRetry={() => refresh().catch((e) => setLoadError(e.message))} />}
  </div>

  const selectedDocuments = selected.map((id) => docById(id)).filter((doc): doc is Document => !!doc)
  const currentPlan = sources.plans.find((plan) => plan.id === planId)
  const sessions = sources.sessions
  const ragFault = modelFault && (modelFault.purpose === "rag_qa" || modelFault.purpose === "rag.send") ? modelFault : null
  const sessionGroups = new Map<string, { title: string; sessions: SessionSummary[] }>()
  for (const item of sessions) {
    const key = [...item.document_ids].sort().join("\u001f")
    const title = item.document_ids.map((id) => docById(id)?.title).filter(Boolean).join("、") || "未关联资料"
    const group = sessionGroups.get(key) ?? { title, sessions: [] }
    group.sessions.push(item)
    sessionGroups.set(key, group)
  }

  return <div className="page rag-page" data-testid="rag-page">
    <header className="rag-heading">
      <div><span className="rag-eyebrow">REFERENCE DESK</span><h1>资料问答</h1><p>选好资料后，直接围绕原文提问。回答会标出出处。</p></div>
      <div className="rag-heading-mark" aria-hidden="true"><BookOpen /></div>
    </header>
    {sources.server_status?.state === "unavailable" && <div className="rag-offline" role="status" data-testid="rag-offline-notice">
      <span>{sources.server_status.message || "资料检索暂时不可用，已保存记录仍可查看。"}</span>
    </div>}
    <div className="rag-layout">
      <aside className="card rag-sources" aria-label="选择问答资料">
        <div className="rag-section-title"><span className="rag-step">01</span><div><h2>选择资料</h2><p>同一份资料可以用于多个问答会话。</p></div></div>
        <label className="rag-label" htmlFor="rag-plan-select">按计划选择</label>
        <select id="rag-plan-select" className="input" data-testid="rag-plan-select" aria-label="按计划选择资料" value={planId} disabled={!!pending || !!busy} onChange={(event) => selectPlan(event.target.value)}>
          <option value="">选择一个计划…</option>{sources.plans.map((plan) => <option key={plan.id} value={plan.id}>{plan.title}</option>)}
        </select>
        {currentPlan && <Button small kind="ghost" className="rag-all-plan" data-testid="rag-plan-select-all" aria-label={`选择计划全部资料：${currentPlan.title}`} disabled={!!pending || !!busy} onClick={() => togglePlan(currentPlan.id)}>
          {currentPlan.document_ids.every((id) => selected.includes(id)) ? "取消本计划资料" : "选择这个计划的全部资料"} <span>· {currentPlan.document_ids.length} 份</span>
        </Button>}
        <div className="rag-document-list" aria-label="可选资料">
          {sources.documents.length === 0 ? <p className="muted small">还没有可选资料。可以先上传文件。</p> : sources.documents.map((doc) => {
            const planNames = doc.plan_ids.map((id) => sources.plans.find((plan) => plan.id === id)?.title).filter(Boolean)
            const status = progress[doc.id] ?? doc
            return <label className={`rag-document ${selected.includes(doc.id) ? "selected" : ""}`} key={doc.id}>
              <input type="checkbox" data-testid={`rag-document-${doc.id}`} aria-label={`选择资料：${doc.title}`} checked={selected.includes(doc.id)} disabled={!!pending || !!busy} onChange={() => toggleDocument(doc.id)} />
              <span className="rag-document-info"><b title={doc.title}>{doc.title}</b><small>{planNames.join(" · ") || "单独上传"}</small>
                <small className={`rag-doc-status ${status.status === "failed" ? "failed" : ""}`} role="status">{documentStatus(status)}</small></span>
            </label>
          })}
        </div>
        <div className="rag-upload-row">
          <Button kind="ghost" small data-testid="rag-pick-files" aria-label="上传新资料文件" disabled={!!busy || !!pending} onClick={importFiles}><FilePlus2 />{busy === "import" ? "正在读取文件…" : "上传新文件"}</Button>
          <span className="muted small">文件只在本机抽取文字</span>
        </div>
        <div className="rag-selected" aria-label="已选资料">
          {!selectedDocuments.length ? <span className="muted small">尚未选择资料</span> : selectedDocuments.map((doc) => <span className="rag-chip" key={doc.id}>
            <span title={doc.title}>{doc.title}</span><button type="button" data-testid={`rag-remove-document-${doc.id}`} aria-label={`移除资料：${doc.title}`} title="移除资料" disabled={!!pending || !!busy} onClick={() => toggleDocument(doc.id)}><X size={13} /></button>
          </span>)}
        </div>
        <Button kind="primary" data-testid="rag-ingest" disabled={!selected.length || !!busy || selectedDocuments.every((doc) => doc.status === "ready")} onClick={ingest}>
          {busy === "ingest" ? <><LoaderCircle className="spin" />正在入库…</> : "将所选资料入库"}
        </Button>
        <p className="rag-privacy">仅上传抽取后的文字分块，用于资料检索；原文件不会上传。</p>
      </aside>

      <section className="card rag-chat" aria-label="资料问答对话">
        {ragFault && !sendFailure && <div className="rag-model-fault" data-testid="rag-model-fault"><ModelFaultPanel fault={ragFault} onRetry={() => { void send() }} /></div>}
        <header className="rag-chat-head">
          <div><span className="rag-step">02</span><div><h2>{session?.title || "开始提问"}</h2><p>{selectedDocuments.length ? `当前使用 ${selectedDocuments.length} 份资料` : "请先选择资料"}</p></div></div>
          <Button small kind="ghost" data-testid="rag-new-session" disabled={!selected.length || !!busy || !!pending} onClick={newSession}><MessageSquarePlus />新建会话</Button>
        </header>
        <div className="rag-session-strip" aria-label="资料组会话">
          {[...sessionGroups.entries()].map(([groupId, group]) => <section className="rag-session-group" key={groupId} aria-label={`资料组：${group.title}`}>
            <span className="rag-session-group-title" title={group.title}>{group.title}</span>
            <div>{group.sessions.map((item) => <div className={`rag-session ${session?.id === item.id ? "active" : ""}`} key={item.id}>
              <button type="button" title={item.title} disabled={!!pending} onClick={() => void openSession(item.id)}>{item.title}</button>
              <button type="button" aria-label={`删除会话：${item.title}`} title="删除会话" disabled={!!pending} onClick={() => void deleteSession(item.id)}><Trash2 size={13} /></button>
            </div>)}</div>
          </section>)}
        </div>
        <div className="rag-messages" ref={scrollRef} data-testid="rag-messages">
          {!session?.messages.length && !pending ? <div className="rag-empty"><div className="rag-empty-icon"><BookOpen /></div><b>从资料开始，一起把问题说清楚</b><span>选好并完成资料入库后，在下面输入你的问题。</span></div> : <>
            {session?.messages.map((message) => <div key={message.id} data-turn-key={`rag-${session.id}-${message.id}`} data-turn-role={message.role}><RagMessage message={message} onCitation={(number) => setSourceOpen({ messageId: message.id, number })}
              active={sourceOpen?.messageId === message.id ? sourceOpen.number : null} /></div>)}
            {pending && <><div data-turn-key="rag-pending-user" data-turn-role="user"><div className="rag-message user"><div className="rag-bubble">{pending.text}</div></div></div><div data-turn-key="rag-pending-assistant" data-turn-role="assistant"><div className="rag-message assistant"><div className="rag-bubble" data-testid="rag-streaming-reply">{pending.reply || <span className="rag-thinking">正在查阅资料并整理回答…<i /><i /><i /></span>}</div></div></div></>}
          </>}
        </div>
        <div className="rag-composer">
          {!selectedDocuments.length && <p className="rag-composer-hint">选择一份或多份资料后即可提问。</p>}
          {sendFailure && <div className="rag-send-failure" role="status" data-testid="rag-send-failure">
            <span>{sendFailure.message} 这条问题没有自动重发。</span>
            <Button small data-testid="rag-retry-send" disabled={!!busy || !!pending} onClick={() => void send(sendFailure.text)}>重试这条问题</Button>
          </div>}
          <textarea className="input" rows={2} data-testid="rag-question" aria-label="输入资料问题" value={question} disabled={!!pending || !!busy}
            placeholder="例如：这份资料是怎样解释这个概念的？" onChange={(event) => { const value = event.target.value; setQuestion(value); if (value.trim() !== question.trim() && (sendFailure || ragFault)) clearSendFailure() }}
            onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} />
          {pending ? <Button data-testid="rag-cancel" onClick={() => abort.current?.abort()}><Square />取消回答</Button>
            : <Button kind="primary" data-testid="rag-send" disabled={!question.trim() || !selected.length || !!busy} onClick={() => void send()}>{busy === "send" ? <><LoaderCircle className="spin" />准备会话…</> : busy === "session" ? <><LoaderCircle className="spin" />建立会话…</> : <><SendHorizontal />提问</>}</Button>}
        </div>
      </section>
    </div>
  </div>
}

function mergeDocuments(before: Document[], after: Document[]) {
  const byId = new Map(before.map((doc) => [doc.id, doc]))
  after.forEach((doc) => byId.set(doc.id, { ...byId.get(doc.id), ...doc }))
  return [...byId.values()]
}
function documentStatus(doc: Partial<Document>) {
  if (doc.status === "ready") return `已就绪${doc.total_chunks ? ` · ${doc.total_chunks} 段` : ""}`
  if (doc.status === "failed") return `入库失败${doc.error ? `：${doc.error}` : "，可重试"}`
  if (doc.status === "uploading") return `入库中 ${doc.uploaded_chunks ?? 0} / ${doc.total_chunks ?? 0} 段`
  return "尚未入库"
}

function RagMessage({ message, onCitation, active }: { message: Message; onCitation: (n: number) => void; active: number | null }) {
  const toast = useApp((s) => s.toast)
  const [copied, setCopied] = React.useState(false)
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  React.useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const passages = message.passages ?? []
  const passage = active ? passages[active - 1] : null
  async function copy() {
    try {
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable")
        if (window.qt?.webChannelTransport) {
          const permission = await navigator.permissions.query({ name: "clipboard-write" as PermissionName })
          if (permission.state !== "granted") throw new Error("Clipboard permission unavailable")
        }
        await navigator.clipboard.writeText(message.content)
      } catch { await call("app.copy_text", { text: message.content }) }
      setCopied(true)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 2000)
    } catch (error) { toast("error", `复制失败：${(error as Error).message}`) }
  }
  return <article className={`rag-message ${message.role}`} data-testid={`rag-message-${message.id}`}>
    <div className="rag-message-label">{message.role === "user" ? "你" : "资料回答"}{message.role === "assistant" && message.supplement && <span className="rag-supplement-badge">含资料外补充</span>}</div>
    <div className="rag-bubble" data-selectable>
      {message.role === "assistant" ? <ReactMarkdown remarkPlugins={[remarkGfm, remarkCitations]} components={{
        a: ({ href, children, ...props }) => href?.startsWith("#rag-citation-")
          ? <button type="button" className="rag-citation" data-testid={`rag-citation-${href.slice("#rag-citation-".length)}`} aria-label={`查看引用 ${href.slice("#rag-citation-".length)}`}
              onClick={() => onCitation(Number(href.slice("#rag-citation-".length)))}>{children}</button>
          : <a href={href} {...props}>{children}</a>,
      }}>{message.content}</ReactMarkdown> : message.content}
    </div>
    {message.role === "assistant" && <div className="rag-message-actions"><button type="button" className="chat-copy" data-testid={`rag-copy-${message.id}`} aria-label={copied ? "已复制" : "复制"} title="复制" onClick={copy}>
      {copied ? <Check size={14} /> : <Copy size={14} />}<span aria-live="polite">{copied ? "已复制" : "复制"}</span>
    </button></div>}
    {passage && <aside className="rag-passage" data-testid={`rag-source-${active}`} aria-label={`原文引用 ${active}`}>
      <div className="rag-passage-head"><div><b>[{active}] {passage.title || "原文出处"}</b><span>{[passage.page ? `第 ${passage.page} 页` : "", passage.section || "", `原文块 ${passage.seq}`].filter(Boolean).join(" · ")}</span></div>
        <button type="button" aria-label="关闭原文" onClick={() => onCitation(0)}><X size={15} /></button></div>
      <div className="rag-passage-text" data-selectable>{passage.text}</div>
    </aside>}
  </article>
}

function remarkCitations() {
  return (tree: any) => {
    const walk = (parent: any) => {
      if (!Array.isArray(parent?.children)) return
      const next: any[] = []
      for (const child of parent.children) {
        if (child.type === "code" || child.type === "inlineCode" || child.type === "link" || child.type === "linkReference") {
          next.push(child)
          continue
        }
        if (child.type === "text") {
          const pattern = /\[(\d+)\]/g
          let last = 0
          let match: RegExpExecArray | null
          while ((match = pattern.exec(child.value))) {
            if (match.index > last) next.push({ type: "text", value: child.value.slice(last, match.index) })
            next.push({ type: "link", url: `#rag-citation-${match[1]}`, children: [{ type: "text", value: match[0] }] })
            last = pattern.lastIndex
          }
          if (last < child.value.length) next.push({ type: "text", value: child.value.slice(last) })
        } else { walk(child); next.push(child) }
      }
      parent.children = next
    }
    walk(tree)
  }
}
