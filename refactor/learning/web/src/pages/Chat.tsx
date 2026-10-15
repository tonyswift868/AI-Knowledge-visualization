import * as React from "react"
import { useRoundScroll } from "../useRoundScroll"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import "katex/dist/katex.min.css"
import { ArrowLeft, Check, Copy, MessageSquarePlus, SendHorizontal, Square, Trash2, UserRoundPen } from "lucide-react"
import { BridgeFailure, call, on } from "../bridge"
import { useApp } from "../store"
import { Button, SakuraArt, useModelRetry } from "../ui"
import { Loading, MODEL_SLOW_MESSAGE } from '../loading/Loading'
import './chat-copy.css'

type Msg = { role: "user" | "assistant"; content: string; seq?: number }
type Summary = { id: string; title: string; updated_at: string; count: number }
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
  const [list, setList] = React.useState<Summary[] | null>(null)
  const [loadError,setLoadError]=React.useState('')
  const [listLoading,setListLoading]=React.useState(true)
  const [current, setCurrent] = React.useState<Session | null>(null)
  const [draft, setDraft] = React.useState("")
  const [pending, setPending] = React.useState<{ user: string; reply: string } | null>(null)
  const [personaOpen, setPersonaOpen] = React.useState(false)
  const abort = React.useRef<AbortController | null>(null)
  const scroller = React.useRef<HTMLDivElement>(null)
  const beginRound = useRoundScroll(scroller, current?.id)
  const input = React.useRef<HTMLTextAreaElement>(null)

  const refresh = React.useCallback(async () => {
    setListLoading(true)
    try {
      const r = await call<{ sessions: Summary[] }>("chat.list")
      setLoadError('');setList(r.sessions)
      return r.sessions
    } finally {setListLoading(false)}
  }, [])

  const open = React.useCallback(async (id: string) => {
    try {
      const r = await call<{ session: Session }>("chat.get", { session_id: id })
      setCurrent(r.session)
    } catch (e) { toast("error", (e as Error).message) }
  }, [toast])

  React.useEffect(() => {
    refresh().then((sessions) => { if (sessions[0]) open(sessions[0].id) }).catch((e) => { setLoadError(e.message); toast("error", e.message) })
    const off = on("chat_changed", () => { refresh().catch(() => {}) })
    return () => { off(); abort.current?.abort() }
  }, [refresh, open, toast])

  async function fresh() {
    if (pending) return
    if (current && current.count === 0) { input.current?.focus(); return }
    try {
      const r = await call<{ session: Session }>("chat.create")
      setCurrent(r.session)
      await refresh()
      input.current?.focus()
    } catch (e) { toast("error", (e as Error).message) }
  }

  async function remove(id: string) {
    try {
      await call("chat.delete", { session_id: id })
      const sessions = await refresh()
      if (current?.id === id) {
        setCurrent(null)
        if (sessions[0]) open(sessions[0].id)
      }
    } catch (e) { toast("error", (e as Error).message) }
  }

  useModelRetry(()=>{void send()},f=>f.purpose==='free_chat' || f.purpose==='chat.send')
  async function send() {
    const text = draft.trim()
    if (!text || pending) return
    let session = current
    try {
      if (!session) {
        session = (await call<{ session: Session }>("chat.create")).session
        setCurrent(session)
        refresh()
      }
    } catch (e) { toast("error", (e as Error).message); return }
    beginRound(session.id)
    setDraft("")
    setPending({ user: text, reply: "" })
    const controller = new AbortController()
    abort.current = controller
    try {
      const r = await call<{ session: Session }>("chat.send", { session_id: session.id, text }, {
        signal: controller.signal,
        onStream: (d) => setPending((p) => p && { ...p, reply: p.reply + d }),
      })
      setCurrent(r.session)
      refresh()
    } catch (e) {
      const err = e as BridgeFailure
      setDraft((d) => d || text)
      if (err.code === "CANCELLED") toast("info", err.message)
      else toast("error", err.message)
    } finally {
      abort.current = null
      setPending(null)
    }
  }

  const messages = current?.messages ?? []

  return (
    <div className="page chat-page">
      <aside className="card chat-list">
        <div className="row between">
          <Button small kind="ghost" onClick={() => go("tools")}><ArrowLeft />更多工具</Button>
        </div>
        <Button kind="primary" onClick={fresh} disabled={!!pending}><MessageSquarePlus />新对话</Button>
        <div className="chat-sessions">
          {list!==null && <Loading active={listLoading} label="正在更新对话列表…"/>}
          {list === null ? loadError ? <div role="alert">{loadError}<Button small onClick={()=>void refresh().catch(e=>setLoadError(e.message))}>重试</Button></div> : <Loading shape="cards" label="正在读取对话…" onRetry={()=>void refresh().catch(e=>setLoadError(e.message))}/>
            : list.length === 0 ? <p className="muted small" style={{ padding: "4px 6px" }}>还没有对话。</p>
            : list.map((s) => (
              <SessionRow key={s.id} s={s} on={current?.id === s.id} disabled={!!pending}
                          onOpen={() => open(s.id)} onDelete={() => remove(s.id)} />
            ))}
        </div>
      </aside>

      <section className="card chat-main">
        <header className="chat-head">
          <div style={{ minWidth: 0 }}>
            <b className="chat-title">{current?.title ?? "教师闲聊"}</b>
            <span className="muted small">想聊什么都行，不会进入课程</span>
          </div>
          <Button small kind={personaOpen ? "primary" : undefined} onClick={() => setPersonaOpen((v) => !v)}>
            <UserRoundPen />人设
          </Button>
        </header>
        {personaOpen && <Persona onClose={() => setPersonaOpen(false)} />}

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

function SessionRow({ s, on, disabled, onOpen, onDelete }:
  { s: Summary; on: boolean; disabled: boolean; onOpen: () => void; onDelete: () => void }) {
  const [confirm, setConfirm] = React.useState(false)
  React.useEffect(() => {
    if (!confirm) return
    const t = setTimeout(() => setConfirm(false), 3000)
    return () => clearTimeout(t)
  }, [confirm])
  return (
    <div className={`chat-session ${on ? "on" : ""}`} onClick={() => !disabled && onOpen()}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <b>{s.title}</b>
        <span>{when(s.updated_at)} · {s.count} 条</span>
      </div>
      <button className={`chat-del ${confirm ? "confirm" : ""}`} disabled={disabled} title="删除这段对话"
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
