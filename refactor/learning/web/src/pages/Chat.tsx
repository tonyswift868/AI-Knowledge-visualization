import * as React from "react"
import { useRoundScroll } from "../useRoundScroll"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import "katex/dist/katex.min.css"
import { Archive, ArchiveRestore, ArrowLeft, Check, Copy, MessageSquarePlus, PanelLeftClose, PanelLeftOpen, SendHorizontal, Square, Trash2, UserRoundPen, X } from "lucide-react"
import { BridgeFailure, call, on } from "../bridge"
import { useApp } from "../store"
import { Button, SakuraArt, useModelRetry } from "../ui"
import { Loading, MODEL_SLOW_MESSAGE } from '../loading/Loading'
import './chat-copy.css'

type Msg = { role: "user" | "assistant"; content: string; seq?: number }
type Summary = { id: string; title: string; updated_at: string; count: number; archived?: boolean }
type Session = Summary & { messages: Msg[] }

function when(iso: string) {
  const d = new Date(iso)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) return d.toTimeString().slice(0, 5)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

export function Chat() {
  const toast = useApp((s) => s.toast)
  const go = useApp((s) => s.go)
  const account = useApp((s) => s.account)
  const [list, setList] = React.useState<Summary[] | null>(null)
  const [loadError,setLoadError]=React.useState('')
  const [listLoading,setListLoading]=React.useState(true)
  const [current, setCurrent] = React.useState<Session | null>(null)
  const [draft, setDraft] = React.useState("")
  const [pending, setPending] = React.useState<{ user: string; reply: string } | null>(null)
  const [personaOpen, setPersonaOpen] = React.useState(false)
  const [showArchived, setShowArchived] = React.useState(false)
  const [listCollapsed, setListCollapsed] = React.useState(false)
  const [drawerOpen, setDrawerOpen] = React.useState(false)
  const [narrow, setNarrow] = React.useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 760px)').matches)
  const abort = React.useRef<AbortController | null>(null)
  const scroller = React.useRef<HTMLDivElement>(null)
  const beginRound = useRoundScroll(scroller, current?.id)
  const input = React.useRef<HTMLTextAreaElement>(null)
  const openListButton = React.useRef<HTMLButtonElement>(null)
  const closeListButton = React.useRef<HTMLButtonElement>(null)
  const currentRef = React.useRef(current)
  const pendingRef = React.useRef(pending)
  const archivedFilterRef = React.useRef(showArchived)
  const requestVersion = React.useRef(0)
  const scopeVersion = React.useRef(0)
  const openVersion = React.useRef(0)
  const preferenceInteractionVersion = React.useRef(0)
  const preferenceQueue = React.useRef<Promise<void>>(Promise.resolve())
  currentRef.current = current
  pendingRef.current = pending
  archivedFilterRef.current = showArchived

  const refresh = React.useCallback(async (archived = archivedFilterRef.current) => {
    const version = ++requestVersion.current
    setListLoading(true)
    try {
      const r = await call<{ sessions: Summary[] }>("chat.list", { archived })
      if (version === requestVersion.current) { setLoadError(''); setList(r.sessions) }
      return r.sessions
    } finally { if (version === requestVersion.current) setListLoading(false) }
  }, [])

  const open = React.useCallback(async (id: string) => {
    const version = scopeVersion.current
    const request = ++openVersion.current
    try {
      const r = await call<{ session: Session }>("chat.get", { session_id: id })
      if (version === scopeVersion.current && request === openVersion.current) setCurrent(r.session)
    } catch (e) { if (version === scopeVersion.current) toast("error", (e as Error).message) }
  }, [toast])

  React.useEffect(() => {
    let alive = true
    const scope = ++scopeVersion.current
    preferenceInteractionVersion.current = 0
    const preferenceReadVersion = preferenceInteractionVersion.current
    ++openVersion.current
    ++requestVersion.current
    setCurrent(null)
    setPending(null)
    setList(null)
    setDraft("")
    setPersonaOpen(false)
    setLoadError("")
    setListCollapsed(false)
    setDrawerOpen(false)
    setShowArchived(false)
    archivedFilterRef.current = false
    call<{ list_collapsed: boolean; available?: boolean }>("chat.ui_get").then((r) => {
      if (alive && scope === scopeVersion.current && preferenceReadVersion === preferenceInteractionVersion.current && r.available !== false) setListCollapsed(!!r.list_collapsed)
    }).catch(() => { if (alive && scope === scopeVersion.current && preferenceReadVersion === preferenceInteractionVersion.current) setListCollapsed(false) })
    refresh(false).then((sessions) => {
      if (alive && scope === scopeVersion.current && sessions[0]) return open(sessions[0].id)
    }).catch((e) => { if (alive && scope === scopeVersion.current) { setLoadError(e.message); toast("error", e.message) } })
    const off = on("chat_changed", () => {
      refresh().catch(() => {})
      const selected = currentRef.current
      if (selected && !pendingRef.current) {
        const request = ++openVersion.current
        call<{ session: Session }>("chat.get", { session_id: selected.id }).then((r) => {
          if (scope === scopeVersion.current && request === openVersion.current && currentRef.current?.id === selected.id && !pendingRef.current) setCurrent(r.session)
        }).catch(() => {})
      }
    })
    return () => { alive = false; ++requestVersion.current; ++openVersion.current; if (scope === scopeVersion.current) ++scopeVersion.current; off(); abort.current?.abort() }
  }, [account, refresh, open, toast])

  React.useEffect(() => {
    const media = window.matchMedia('(max-width: 760px)')
    const update = () => {
      const next = media.matches
      setNarrow(next)
      setDrawerOpen(false)
    }
    media.addEventListener?.('change', update)
    window.addEventListener('resize', update)
    update()
    return () => { media.removeEventListener?.('change', update); window.removeEventListener('resize', update) }
  }, [])

  React.useEffect(() => {
    if (!narrow || !drawerOpen) return
    const frame = requestAnimationFrame(() => closeListButton.current?.focus())
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); setDrawerOpen(false); requestAnimationFrame(() => openListButton.current?.focus()); return }
      if (event.key !== 'Tab') return
      const items = [...(document.querySelector('.chat-list.drawer-open')?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], textarea, [tabindex]:not([tabindex="-1"])') ?? [])]
      if (!items.length) return
      const first = items[0], last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', keydown)
    return () => { cancelAnimationFrame(frame); window.removeEventListener('keydown', keydown) }
  }, [narrow, drawerOpen])

  function toggleCollapsed(next: boolean) {
    const scope = scopeVersion.current
    preferenceInteractionVersion.current++
    setListCollapsed(next)
    preferenceQueue.current = preferenceQueue.current.catch(() => {}).then(async () => {
      if (scope !== scopeVersion.current) return
      try {
        const r = await call<{ saved?: boolean }>("chat.ui_set", { list_collapsed: next })
        if (scope === scopeVersion.current && r.saved === false) toast("info", "收起状态暂时无法保存。")
      } catch { if (scope === scopeVersion.current) toast("info", "收起状态暂时无法保存。") }
    })
  }

  function chooseTab(archived: boolean) {
    if (archived === showArchived) return
    setShowArchived(archived)
    setList(null)
    setLoadError("")
    refresh(archived).catch((e) => setLoadError(e.message))
  }

  function toggleList() {
    if (narrow) {
      if (drawerOpen) { setDrawerOpen(false); requestAnimationFrame(() => openListButton.current?.focus()) }
      else setDrawerOpen(true)
    } else toggleCollapsed(!listCollapsed)
  }

  async function fresh() {
    if (pending) return
    const scope = scopeVersion.current
    if (current && !current.archived && current.count === 0) { input.current?.focus(); return }
    try {
      const r = await call<{ session: Session }>("chat.create")
      if (scope !== scopeVersion.current) return
      setCurrent(r.session)
      setShowArchived(false)
      archivedFilterRef.current = false
      await refresh(false)
      input.current?.focus()
      if (narrow) { setDrawerOpen(false); requestAnimationFrame(() => input.current?.focus()) }
    } catch (e) { if (scope === scopeVersion.current) toast("error", (e as Error).message) }
  }

  async function remove(id: string) {
    const scope = scopeVersion.current
    try {
      await call("chat.delete", { session_id: id })
      if (scope !== scopeVersion.current) return
      const sessions = await refresh()
      if (scope !== scopeVersion.current) return
      if (currentRef.current?.id === id) {
        setCurrent(null)
        if (sessions[0]) open(sessions[0].id)
      }
    } catch (e) { if (scope === scopeVersion.current) toast("error", (e as Error).message) }
  }

  async function setArchived(id: string, archived: boolean) {
    const scope = scopeVersion.current
    try {
      const r = await call<{ session: Session }>("chat.archive", { session_id: id, archived })
      if (scope !== scopeVersion.current) return
      if (currentRef.current?.id === id) setCurrent(r.session)
      await refresh()
    } catch (e) { if (scope === scopeVersion.current) toast("error", (e as Error).message) }
  }

  useModelRetry(()=>{void send()},f=>f.purpose==='free_chat' || f.purpose==='chat.send')
  async function send() {
    const text = draft.trim()
    if (!text || pending) return
    const scope = scopeVersion.current
    let session = current
    try {
      if (!session) {
        session = (await call<{ session: Session }>("chat.create")).session
        if (scope !== scopeVersion.current) return
        setCurrent(session)
        setShowArchived(false)
        archivedFilterRef.current = false
        refresh(false)
      }
    } catch (e) { if (scope === scopeVersion.current) toast("error", (e as Error).message); return }
    beginRound(session.id)
    if (session.archived) {
      setShowArchived(false)
      archivedFilterRef.current = false
      refresh(false)
    }
    setDraft("")
    setPending({ user: text, reply: "" })
    const controller = new AbortController()
    abort.current = controller
    try {
      const r = await call<{ session: Session }>("chat.send", { session_id: session.id, text }, {
        signal: controller.signal,
        onStream: (d) => { if (scope === scopeVersion.current) setPending((p) => p && { ...p, reply: p.reply + d }) },
      })
      if (scope !== scopeVersion.current) return
      setCurrent(r.session)
      refresh()
    } catch (e) {
      if (scope !== scopeVersion.current) return
      const err = e as BridgeFailure
      setDraft((d) => d || text)
      // The backend may have restored an archived session before a failed or
      // cancelled generation. Refresh its metadata without touching the draft.
      void call<{ session: Session }>("chat.get", { session_id: session.id }).then((r) => {
        if (scope === scopeVersion.current && currentRef.current?.id === session.id) setCurrent(r.session)
      }).catch(() => {})
      refresh().catch(() => {})
      if (err.code === "CANCELLED") toast("info", err.message)
      else toast("error", err.message)
    } finally {
      if (scope === scopeVersion.current) {
        abort.current = null
        setPending(null)
      }
    }
  }

  const messages = current?.messages ?? []
  const listVisible = narrow ? drawerOpen : !listCollapsed

  return (
    <div className={`page chat-page ${!narrow && listCollapsed ? "list-collapsed" : ""} ${narrow && drawerOpen ? "drawer-visible" : ""}`}>
      {narrow && drawerOpen && <button className="chat-drawer-backdrop" aria-label="关闭对话列表" onClick={() => { setDrawerOpen(false); openListButton.current?.focus() }} />}
      <aside className={`card chat-list ${narrow && drawerOpen ? "drawer-open" : ""}`} aria-label={showArchived ? "已归档对话列表" : "对话列表"} aria-hidden={narrow && !drawerOpen}>
        <div className="row between chat-list-tools">
          <Button small kind="ghost" onClick={() => go("tools")}><ArrowLeft />更多工具</Button>
          {narrow && <button ref={closeListButton} type="button" className="btn small ghost" aria-label="关闭对话列表" title="关闭对话列表" onClick={() => { setDrawerOpen(false); openListButton.current?.focus() }}><X /></button>}
        </div>
        <Button kind="primary" onClick={fresh} disabled={!!pending}><MessageSquarePlus />新对话</Button>
        <div className="chat-list-tabs" role="tablist" aria-label="对话分类">
          <button type="button" role="tab" aria-selected={!showArchived} className={!showArchived ? "on" : ""} onClick={() => chooseTab(false)}>对话</button>
          <button type="button" role="tab" aria-selected={showArchived} className={showArchived ? "on" : ""} onClick={() => chooseTab(true)}>已归档</button>
        </div>
        <div className="chat-sessions">
          {list!==null && <Loading active={listLoading} label="正在更新对话列表…"/>}
          {list === null ? loadError ? <div role="alert">{loadError}<Button small onClick={()=>void refresh().catch(e=>setLoadError(e.message))}>重试</Button></div> : <Loading shape="cards" label="正在读取对话…" onRetry={()=>void refresh().catch(e=>setLoadError(e.message))}/>
            : list.length === 0 ? <p className="muted small" style={{ padding: "4px 6px" }}>{showArchived ? "没有已归档的对话。" : "还没有对话。"}</p>
            : list.map((s) => (
              <SessionRow key={s.id} s={s} on={current?.id === s.id} disabled={!!pending} archived={showArchived}
                          onOpen={() => { void open(s.id); if (narrow) { setDrawerOpen(false); requestAnimationFrame(() => input.current?.focus()) } }}
                          onArchive={() => void setArchived(s.id, true)} onRestore={() => void setArchived(s.id, false)} onDelete={() => remove(s.id)} />
            ))}
        </div>
      </aside>

      <section className="card chat-main">
        <header className="chat-head">
          <div style={{ minWidth: 0 }}>
            <b className="chat-title">{current?.title ?? "教师闲聊"}</b>
            <span className="muted small">想聊什么都行，不会进入课程</span>
          </div>
          <div className="row chat-head-actions">
            <button ref={openListButton} type="button" className="btn small ghost" aria-label={narrow ? (drawerOpen ? "关闭对话列表" : "打开对话列表") : listVisible ? "收起对话列表" : "展开对话列表"} title={narrow ? (drawerOpen ? "关闭对话列表" : "打开对话列表") : listVisible ? "收起对话列表" : "展开对话列表"}
              onClick={toggleList}>
              {listVisible && !narrow || narrow && drawerOpen ? <PanelLeftClose /> : <PanelLeftOpen />}{narrow ? "对话列表" : listVisible ? "收起列表" : "展开列表"}
            </button>
            <Button small kind="primary" onClick={fresh} disabled={!!pending}><MessageSquarePlus />新对话</Button>
            <Button small kind={personaOpen ? "primary" : undefined} onClick={() => setPersonaOpen((v) => !v)}><UserRoundPen />人设</Button>
          </div>
        </header>
        {personaOpen && <Persona onClose={() => setPersonaOpen(false)} />}

        {current?.archived && <div className="chat-archived-note" role="status">
          <span>这段对话已归档。发送消息会自动恢复。</span>
          <Button small kind="ghost" onClick={() => void setArchived(current.id, false)}><ArchiveRestore />恢复</Button>
        </div>}

        <div className="chat-scroll" ref={scroller}>
          {messages.length === 0 && !pending ? (
            <div className="chat-empty fade-in">
              <SakuraArt />
              <p>随便聊点什么吧。<br /><span className="muted small">点右上角“人设”可以写下你希望老师是什么样子。</span></p>
            </div>
          ) : (
            <>
              {messages.map((m) => <div key={m.seq} data-turn-key={`msg-${m.seq}`} data-turn-role={m.role}><Bubble m={m} /></div>)}
              {pending && <>
                <div data-turn-key="pending-user" data-turn-role="user"><Bubble m={{ role: "user", content: pending.user }} /></div>
                <div data-turn-key="pending-reply" data-turn-role="assistant"><Bubble m={{ role: "assistant", content: pending.reply }} streaming cancel={()=>abort.current?.abort()} /></div>
              </>}
            </>
          )}
        </div>

        <div className="composer">
          <textarea ref={input} className="input" rows={1} value={draft} placeholder="输入消息，Enter 发送，Shift + Enter 换行"
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
          {pending
            ? <Button onClick={() => abort.current?.abort()}><Square />停止</Button>
            : <Button kind="primary" onClick={send} disabled={!draft.trim()}><SendHorizontal />发送</Button>}
        </div>
      </section>
    </div>
  )
}

function SessionRow({ s, on, disabled, archived, onOpen, onArchive, onRestore, onDelete }:
  { s: Summary; on: boolean; disabled: boolean; archived: boolean; onOpen: () => void; onArchive: () => void; onRestore: () => void; onDelete: () => void }) {
  const [confirm, setConfirm] = React.useState(false)
  React.useEffect(() => {
    if (!confirm) return
    const t = setTimeout(() => setConfirm(false), 3000)
    return () => clearTimeout(t)
  }, [confirm])
  return (
    <div className={`chat-session ${on ? "on" : ""}`}>
      <button type="button" className="chat-session-open" disabled={disabled} aria-label={`打开对话：${s.title}`} aria-pressed={on} onClick={onOpen}>
        <b>{s.title}</b>
        <span>{when(s.updated_at)} · {s.count} 条</span>
      </button>
      {archived
        ? <button type="button" className="chat-row-action" disabled={disabled} title="恢复对话" aria-label={`恢复对话：${s.title}`} onClick={onRestore}><ArchiveRestore size={15} /></button>
        : <button type="button" className="chat-row-action" disabled={disabled} title="归档对话" aria-label={`归档对话：${s.title}`} onClick={onArchive}><Archive size={15} /></button>}
      <button type="button" className={`chat-del ${confirm ? "confirm" : ""}`} disabled={disabled} title="删除这段对话" aria-label={confirm ? `确认删除对话：${s.title}` : `删除对话：${s.title}`}
              onClick={(e) => { e.stopPropagation(); confirm ? onDelete() : setConfirm(true) }}>
        {confirm ? "确认删除" : <Trash2 size={15} />}
      </button>
    </div>
  )
}

function Bubble({ m, streaming, cancel }: { m: Msg; streaming?: boolean; cancel?:()=>void }) {
  if (m.role === "user") return <div className="bubble user" data-selectable>{m.content}</div>
  return (
    <div className={`bubble bot ${streaming ? "streaming wait-content" : ""}`} data-selectable>
      {m.content
        ? <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>{m.content}</ReactMarkdown>
        : <Loading shape="conversation" slowMessage={MODEL_SLOW_MESSAGE} label="老师正在想…" onCancel={cancel}/>}
      {!streaming && m.content && <CopyReply text={m.content} />}
    </div>
  )
}

function CopyReply({ text }: { text: string }) {
  const toast = useApp((s) => s.toast)
  const [copied, setCopied] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  React.useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  async function copy() {
    setBusy(true)
    try {
      try {
        if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable')
        if (window.qt?.webChannelTransport) {
          // Qt has no browser permission prompt UI. A "prompt" state leaves
          // writeText pending indefinitely, so use the native write-only path.
          const permission = await navigator.permissions.query({ name: 'clipboard-write' as PermissionName })
          if (permission.state !== 'granted') throw new Error('Clipboard permission unavailable')
        }
        await navigator.clipboard.writeText(text)
      } catch {
        // Qt file:// pages may deny clipboard permission; keep this write-only.
        await call('app.copy_text', { text })
      }
      setCopied(true)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 2000)
    } catch (e) {
      toast('error', `复制失败：${(e as Error).message}`)
    } finally { setBusy(false) }
  }
  return <div className="chat-reply-actions">
    <button type="button" className="chat-copy" title="复制" aria-label={copied ? '已复制' : '复制'} disabled={busy} onClick={copy}>
      {copied ? <Check size={14} /> : <Copy size={14} />}<span aria-live="polite">{copied ? '已复制' : '复制'}</span>
    </button>
  </div>
}

function Persona({ onClose }: { onClose: () => void }) {
  const settings = useApp((s) => s.settings)
  const set = useApp((s) => s.set)
  const toast = useApp((s) => s.toast)
  const [text, setText] = React.useState(settings?.chat_persona ?? "")
  const [busy, setBusy] = React.useState(false)
  async function save() {
    setBusy(true)
    try {
      const r = await call<{ settings: any }>("settings.save", { changes: { chat_persona: text } })
      set({ settings: r.settings })
      toast("ok", text.trim() ? "人设已保存，下一句开始生效。" : "已清空人设。")
      onClose()
    } catch (e) { toast("error", (e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="persona fade-in">
      <textarea className="input" rows={5} maxLength={6000} value={text} onChange={(e) => setText(e.target.value)}
                placeholder="例如：你叫小樱，说话轻松，喜欢用游戏打比方……" />
      <div className="row between">
        <span className="muted small">这段话会原样发给模型，除此之外没有任何提示。留空就是完全不设定。</span>
        <div className="row">
          <Button small kind="ghost" onClick={onClose}>取消</Button>
          <Button small kind="primary" busy={busy} onClick={save}>保存</Button>
        </div>
      </div>
    </div>
  )
}
