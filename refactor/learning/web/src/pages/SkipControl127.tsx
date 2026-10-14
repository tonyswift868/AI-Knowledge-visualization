import * as React from "react"
import { ChevronRight, SkipForward } from "lucide-react"
import "./skip127.css"

type Props = {
  module: boolean
  busy: boolean
  hasPoint: boolean
  reducedMotion: boolean
  onMode: (module: boolean) => void
  onSkip: () => void
}

/** The handle changes scope; only the separate main button executes a skip. */
export function SkipControl127({ module, busy, hasPoint, reducedMotion, onMode, onSkip }: Props) {
  const [position, setPosition] = React.useState<number | null>(null)
  const [changed, setChanged] = React.useState(false)
  const hint = React.useId()
  const drag = React.useRef<{ id: number; x: number; start: number; travel: number; moved: boolean; node: HTMLButtonElement } | null>(null)
  const ignoreClick = React.useRef(false)
  const change = (value: boolean) => { if (!busy) { setChanged(true); onMode(value) } }
  const release = () => {
    const active = drag.current; drag.current = null
    if (active?.node.hasPointerCapture(active.id)) active.node.releasePointerCapture(active.id)
  }
  React.useEffect(() => {
    if (busy) { release(); setPosition(null) }
    else ignoreClick.current = false
  }, [busy])
  React.useEffect(() => () => release(), [])
  const value = position ?? (module ? 1 : 0)
  return <div className="skip127" data-module={module} data-reduced={reducedMotion} data-dragging={position !== null}
    role="group" aria-label="跳过范围与操作" aria-disabled={busy}>
    <button type="button" className="skip127-main" disabled={busy || (!module && !hasPoint)}
      aria-label={module ? "跳过该模块" : "跳过该知识点"} onClick={onSkip}>
      <SkipForward size={17} aria-hidden="true" /><span>跳过该</span>
      <span className="skip127-word-slot"><span key={String(module)} className={`skip127-word${changed ? " replaced" : ""}`}>
        {module ? "模块" : "知识点"}</span></span>
    </button>
    <button type="button" className="skip127-handle" role="switch" aria-label="切换为跳过该模块"
      aria-checked={module} aria-describedby={hint} disabled={busy}
      onPointerDown={e => {
        if (busy || e.button !== 0 || !e.isPrimary) return
        e.preventDefault(); e.currentTarget.focus(); ignoreClick.current = false
        const thumb = e.currentTarget.querySelector<HTMLElement>(".skip127-thumb")!
        const travel = e.currentTarget.clientWidth - 8 - thumb.offsetWidth
        drag.current = { id: e.pointerId, x: e.clientX, start: module ? 1 : 0, travel: Math.max(1, travel), moved: false, node: e.currentTarget }
        e.currentTarget.setPointerCapture(e.pointerId)
        setPosition(module ? 1 : 0)
      }}
      onPointerMove={e => {
        const active = drag.current
        if (!active || active.id !== e.pointerId || busy) return
        const delta = e.clientX - active.x
        if (Math.abs(delta) >= 4) active.moved = true
        setPosition(Math.max(0, Math.min(1, active.start + delta / active.travel)))
      }}
      onPointerUp={e => {
        const active = drag.current
        if (!active || active.id !== e.pointerId) return
        const next = Math.max(0, Math.min(1, active.start + (e.clientX - active.x) / active.travel))
        const moved = active.moved || Math.abs(e.clientX - active.x) >= 4
        ignoreClick.current = moved
        if (moved) change(next > .5)
        release(); setPosition(null)
      }}
      onPointerCancel={() => { ignoreClick.current = true; release(); setPosition(null) }}
      onLostPointerCapture={() => { if (drag.current) { ignoreClick.current = true; drag.current = null; setPosition(null) } }}
      onClick={e => { if (ignoreClick.current && e.detail > 0) { ignoreClick.current = false; return }; ignoreClick.current = false; change(!module) }}
      onKeyDown={e => {
        if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
          e.preventDefault(); ignoreClick.current = false; change(!module)
        }
      }}>
      <span className="skip127-thumb" style={{ left: `calc(4px + (100% - 40px) * ${value})` }}>
        <ChevronRight size={22} aria-hidden="true" />
      </span>
      <span id={hint} role="tooltip" className="skip127-hint">{module ? "向左拉切回知识点，或点按切换" : "向右拉切换为跳过模块"}</span>
    </button>
  </div>
}
