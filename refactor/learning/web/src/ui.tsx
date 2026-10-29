import * as React from "react"
import { CheckCircle2, ChevronDown, CircleHelp, Info, XCircle, TriangleAlert } from "lucide-react"
import { createPortal } from "react-dom"
import { useApp, type ModelFault } from "./store"

// ------------------------------------------------------------------ brand
export function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 40 40" aria-hidden>
      <defs>
        <linearGradient id="brandFill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent-3)" />
          <stop offset="1" stopColor="var(--accent-2)" />
        </linearGradient>
      </defs>
      <rect width="40" height="40" rx="12" fill="url(#brandFill)" />
      <g transform="translate(20 21)">
        {[0, 72, 144, 216, 288].map((a) => (
          <path key={a} transform={`rotate(${a}) scale(.42)`} d="M0 0C-10-7-13-22-6-31L0-26.5L6-31C13-22 10-7 0 0Z" fill="#fff" opacity=".95" />
        ))}
        <circle r="2.6" fill="var(--accent-2)" />
      </g>
    </svg>
  )
}

// ------------------------------------------------------------------ ambient
const petals = Array.from({ length: 14 }, (_, i) => ({
  left: (i * 71) % 100,
  delay: -((i * 37) % 26),
  duration: 18 + ((i * 13) % 12),
  swayDuration: 3 + ((i * 7) % 4),
  scale: 0.7 + ((i * 29) % 60) / 100,
}))
const stars = Array.from({ length: 40 }, (_, i) => ({ left: (i * 53) % 100, top: (i * 37) % 60, delay: -((i * 17) % 40) / 10 }))

export function Ambient({ theme }: { theme: string }) {
  return (
    <div className="ambient" aria-hidden>
      <div className="blob b1" />
      <div className="blob b2" />
      <div className="blob b3" />
      {theme === "aurora" ? (
        <>
          <div className="aurora-band a1" />
          <div className="aurora-band a2" />
          {stars.map((s, i) => (
            <span key={i} className="star" style={{ left: `${s.left}%`, top: `${s.top}%`, animationDelay: `${s.delay}s` }} />
          ))}
        </>
      ) : (
        petals.map((p, i) => (
          <span key={i} className="petal"
                style={{ left: `${p.left}%`, animationDuration: `${p.duration}s`, animationDelay: `${p.delay}s`, transform: `scale(${p.scale})` }}>
            <svg viewBox="0 0 20 18" style={{ animationDuration: `${p.swayDuration}s` }}>
              <path d="M10 17C4 13 0 9 1.5 4.5 3 0 8 1 10 5c2-4 7-5 8.5-.5C20 9 16 13 10 17z"
                    fill={i % 3 ? "var(--petal)" : "var(--petal-2)"} />
            </svg>
          </span>
        ))
      )}
    </div>
  )
}

// --------------------------------------------------------------------- ring
export function Ring({ value, total, tentative = 0, size = 132, stroke = 12, children }:
  { value: number; total: number; tentative?: number; size?: number; stroke?: number; children?: React.ReactNode }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const [shown, setShown] = React.useState(0)
  React.useEffect(() => {
    const id = requestAnimationFrame(() => setShown(1))
    return () => cancelAnimationFrame(id)
  }, [])
  const done = total > 0 ? Math.min(1, value / total) : 0
  const tent = total > 0 ? Math.min(1, (value + tentative) / total) : 0
  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <defs>
          <linearGradient id="ringGradient" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--accent-3)" />
            <stop offset="100%" stopColor="var(--accent-2)" />
          </linearGradient>
        </defs>
        <circle className="track" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} />
        <circle className="tent" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} strokeLinecap="round"
                strokeDasharray={c} strokeDashoffset={c * (1 - tent * shown)} />
        <circle className="bar" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} strokeLinecap="round"
                strokeDasharray={c} strokeDashoffset={c * (1 - done * shown)} />
      </svg>
      <div className="center">{children}</div>
    </div>
  )
}

// ------------------------------------------------------------------ toasts
export function Toasts() {
  const toasts = useApp((s) => s.toasts)
  const dismiss = useApp((s) => s.dismiss)
  const fault = useApp((s) => s.modelFault)
  // User 2026-10-15: a red bar at the top centre that leaves by itself, like the other error toasts.
  React.useEffect(() => {
    if (!fault) return
    const timer = setTimeout(() => { if (useApp.getState().modelFault === fault) useApp.getState().set({ modelFault: null }) }, 6000)
    return () => clearTimeout(timer)
  }, [fault])
  return (<>
    {/* Notice only (user 2026-10-15): no buttons; the in-page panel keeps retry/settings. */}
    {fault && <div className="toast error model-fault-toast" role="alert">
      <TriangleAlert aria-hidden="true"/><div className="grow">{fault.message}</div>
    </div>}
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} onClick={() => dismiss(t.id)}>
          {t.kind === "ok" ? <CheckCircle2 /> : t.kind === "error" ? <XCircle /> : <Info />}
          <div>{t.text}</div>
        </div>
      ))}
    </div>
  </>)
}

const modelRetries=new Set<{run:()=>void;accepts:(fault:ModelFault)=>boolean}>()
export function ModelFaultActions({fault, onRetry}: {fault:ModelFault; onRetry?:()=>void}) {
  const retry = useApp(s=>s.modelRetry)
  const canRetry=!!retry && [...modelRetries].some(entry=>entry.accepts(fault))
  return <div className="row model-fault-actions">{fault.actions.map(a=>a.id === 'recharge' && a.url
    ? <a key={a.id} className="btn small" href={a.url} target="_blank" rel="noreferrer">{a.label}</a>
    : <Button key={a.id} small onClick={()=>['settings','recharge'].includes(a.id) ? useApp.getState().go('settings','models') : (onRetry ?? retry)?.()}
        disabled={a.id === 'retry' && !onRetry && !canRetry}>{a.label}</Button>)}</div>
}

export function ModelFaultPanel({fault,onRetry}: {fault:ModelFault;onRetry?:()=>void}) {
  return <div className="model-fault-panel" role="alert"><TriangleAlert aria-hidden="true"/><div><p>{fault.message}</p>
    <ModelFaultActions fault={fault} onRetry={onRetry}/></div></div>
}

// A retry repeats the page's existing action, including its result/event handling.
// Registration lasts only while that page is mounted; it never replays saved input after navigation.
export function useModelRetry(retry:()=>void, accepts:(fault:ModelFault)=>boolean = ()=>true) {
  const latest=React.useRef({retry,accepts})
  latest.current={retry,accepts}
  React.useEffect(()=>{
    const entry={run:()=>latest.current.retry(),accepts:(fault:ModelFault)=>latest.current.accepts(fault)}
    modelRetries.add(entry)
    const run=()=>{const fault=useApp.getState().modelStatus.fault;if(fault)[...modelRetries].reverse().find(e=>e.accepts(fault))?.run()}
    useApp.getState().set({modelRetry:run})
    return ()=>{modelRetries.delete(entry);useApp.getState().set({modelRetry:modelRetries.size ? run : null})}
  },[])
}

// ------------------------------------------------------------------ controls
export function Button({ kind, small, busy, children, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "ghost" | "danger"; small?: boolean; busy?: boolean }) {
  return (
    <button {...rest} className={`btn ${kind ?? ""} ${small ? "small" : ""} ${rest.className ?? ""}`} disabled={rest.disabled || busy}>
      {busy && <span className="wait-dot" aria-hidden="true" />}
      {children}
    </button>
  )
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={on} aria-label={label} className={`toggle ${on ? "on" : ""}`} onClick={() => onChange(!on)} />
}

export function Segmented<T extends string | number>({ value, options, onChange }:
  { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map((o) => (
        <button key={String(o.value)} role="radio" aria-checked={o.value === value} className={o.value === value ? "on" : ""}
                onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  )
}

export type SelectMenuOption = { value: string; label: string }

/** A theme-aware listbox for compact selection lists that must not use the OS popup. */
export function SelectMenu({ id, value, options, placeholder, ariaLabel, disabled = false,
                             onChange, testId, optionTestIdPrefix, className = "" }:
  { id?: string; value: string; options: SelectMenuOption[]; placeholder: string; ariaLabel: string;
    disabled?: boolean; onChange: (value: string) => void; testId?: string;
    optionTestIdPrefix?: string; className?: string }) {
  const triggerRef = React.useRef<HTMLButtonElement>(null)
  const menuRef = React.useRef<HTMLDivElement>(null)
  const [open, setOpen] = React.useState(false)
  const [active, setActive] = React.useState(0)
  const [position, setPosition] = React.useState<{ left: number; top: number; width: number; maxHeight: number } | null>(null)
  const instanceId = React.useId()
  const listId = `${instanceId}-listbox`
  const selectedIndex = options.findIndex((option) => option.value === value)
  const shown = selectedIndex >= 0 ? options[selectedIndex]!.label : placeholder
  const activeOptionId = options[active] ? `${listId}-option-${active}` : undefined

  const placeMenu = React.useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect()
    if (!rect) return
    const viewportPadding = 8
    const gap = 5
    const width = Math.min(rect.width, window.innerWidth - viewportPadding * 2)
    const left = Math.min(Math.max(rect.left, viewportPadding), window.innerWidth - width - viewportPadding)
    const below = Math.max(0, window.innerHeight - rect.bottom - viewportPadding - gap)
    const above = Math.max(0, rect.top - viewportPadding - gap)
    const openBelow = below >= Math.min(180, Math.max(80, options.length * 46)) || below >= above
    const maxHeight = Math.min(320, openBelow ? below : above)
    const top = openBelow ? rect.bottom + gap : rect.top - maxHeight - gap
    setPosition({ left, top, width, maxHeight })
  }, [options.length])

  React.useEffect(() => {
    if (!open) { setPosition(null); return }
    placeMenu()
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false)
    }
    document.addEventListener("pointerdown", onPointerDown)
    window.addEventListener("resize", placeMenu)
    window.addEventListener("scroll", placeMenu, true)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown)
      window.removeEventListener("resize", placeMenu)
      window.removeEventListener("scroll", placeMenu, true)
    }
  }, [open, placeMenu])

  React.useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  React.useEffect(() => {
    if (!options.length) { setOpen(false); setActive(0); return }
    setActive((index) => Math.min(index, options.length - 1))
  }, [options.length])

  React.useEffect(() => {
    if (open && position && activeOptionId) document.getElementById(activeOptionId)?.scrollIntoView({ block: "nearest" })
  }, [open, activeOptionId, position !== null])

  function openAt(index: number) {
    if (disabled || !options.length) return
    setActive(Math.max(0, Math.min(options.length - 1, index)))
    setOpen(true)
  }
  function choose(index: number) {
    const option = options[index]
    if (!option || disabled) return
    onChange(option.value)
    setActive(index)
    setOpen(false)
    triggerRef.current?.focus({ preventScroll: true })
  }
  function onKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault()
      if (!open) openAt(selectedIndex >= 0 ? selectedIndex : 0)
      else setActive((index) => Math.min(options.length - 1, index + 1))
    } else if (event.key === "ArrowUp") {
      event.preventDefault()
      if (!open) openAt(selectedIndex >= 0 ? selectedIndex : options.length - 1)
      else setActive((index) => Math.max(0, index - 1))
    } else if (event.key === "Home" && open) {
      event.preventDefault(); setActive(0)
    } else if (event.key === "End" && open) {
      event.preventDefault(); setActive(options.length - 1)
    } else if ((event.key === "Enter" || event.key === " ") && open) {
      event.preventDefault(); choose(active)
    } else if (event.key === "Escape" && open) {
      event.preventDefault(); setOpen(false); triggerRef.current?.focus({ preventScroll: true })
    }
  }

  return <>
    <button ref={triggerRef} id={id} type="button" className={`select-menu-trigger ${className}`}
      role="combobox" aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open}
      aria-controls={listId} aria-activedescendant={open ? activeOptionId : undefined}
      data-testid={testId} title={shown} disabled={disabled}
      onClick={() => open ? setOpen(false) : openAt(selectedIndex >= 0 ? selectedIndex : 0)}
      onKeyDown={onKeyDown} onBlur={() => setOpen(false)}>
      <span className="select-menu-label">{shown}</span><ChevronDown size={16} aria-hidden="true" />
    </button>
    {open && position && createPortal(
      <div ref={menuRef} id={listId} className="select-menu-popup" role="listbox" aria-label={ariaLabel}
        style={{ left: position.left, top: position.top, width: position.width, maxHeight: position.maxHeight }}>
        {options.map((option, index) => <div key={option.value || "__empty"} id={`${listId}-option-${index}`}
          className={`select-menu-option ${index === active ? "active" : ""} ${option.value === value ? "selected" : ""}`}
          role="option" aria-selected={option.value === value} data-testid={optionTestIdPrefix ? `${optionTestIdPrefix}${option.value || "empty"}` : undefined}
          title={option.label} onMouseMove={() => setActive(index)} onMouseDown={(event) => event.preventDefault()}
          onClick={() => choose(index)}>
          <span>{option.label}</span>{option.value === value && <span className="select-menu-check" aria-hidden="true">✓</span>}
        </div>)}
      </div>, document.body)}
  </>
}

// One definition used everywhere a daily duration is shown (PLAN-05).
export const TASK_TIME_TIP = "任务时长：每天安排给你的学习量，按学完的内容计算，不是开着软件的时间。" +
  "讲解、随口问答、模块检测都算在内；讲完答对先记“暂记”，模块检测通过才算正式完成。" +
  "整份计划的总时长会平均分到每个能学的日子（半天按一半），每天不超过你设的上限。"

// The bubble lives in a body-level portal with fixed positioning, so cards with
// overflow:hidden or scroll areas never clip it; it opens toward the roomier side.
export function Tip({ text }: { text: string }) {
  const ref = React.useRef<HTMLSpanElement>(null)
  const [pos, setPos] = React.useState<{ x: number; y: number; below: boolean } | null>(null)
  const show = () => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    const below = r.top < 170
    const x = Math.min(Math.max(r.left + r.width / 2, 166), window.innerWidth - 166)
    setPos({ x, y: below ? r.bottom + 10 : r.top - 10, below })
  }
  return (
    <span ref={ref} className="tip" tabIndex={0} aria-label={text} onMouseEnter={show} onFocus={show}
          onMouseLeave={() => setPos(null)} onBlur={() => setPos(null)} onClick={(e) => e.preventDefault()}>
      <CircleHelp />
      {pos && createPortal(
        <span className={`tip-bubble ${pos.below ? "below" : ""}`} role="tooltip" style={{ left: pos.x, top: pos.y }}>{text}</span>,
        document.body)}
    </span>
  )
}

export function Field({ label, hint, tip, children }: { label: string; hint?: React.ReactNode; tip?: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}{tip && <Tip text={tip} />}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  )
}

export function Skeleton({ h = 18, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} />
}

// Five notched petals around a glowing core; everything animates with transforms only.
const PETAL = "M0 0C-10-7-13-22-6-31L0-26.5L6-31C13-22 10-7 0 0Z"

function Blossom({ id, stamens = true }: { id: string; stamens?: boolean }) {
  return (
    <g className="bloom-spin">
      {[0, 72, 144, 216, 288].map((a, i) => (
        <g key={a} transform={`rotate(${a})`}>
          <path className="bloom-petal" d={PETAL} fill={`url(#${id})`} style={{ animationDelay: `${-i * 0.7}s` }} />
          <path d="M0-4L0-20" stroke="var(--accent-2)" strokeWidth=".8" opacity=".22" strokeLinecap="round" />
        </g>
      ))}
      {stamens && Array.from({ length: 10 }, (_, i) => {
        const a = (i * 36 + 18) * Math.PI / 180
        const r = i % 2 ? 8 : 10.5
        return <g key={i}><line x1="0" y1="0" x2={Math.sin(a) * r} y2={-Math.cos(a) * r} stroke="var(--stamen)" strokeWidth=".9" />
          <circle cx={Math.sin(a) * r} cy={-Math.cos(a) * r} r="1.35" fill="var(--stamen)" /></g>
      })}
      <circle r="3.6" fill="var(--accent-2)" />
    </g>
  )
}

export function SakuraArt() {
  return (
    <svg className="art bloom" viewBox="0 0 200 160" aria-hidden>
      <defs>
        <linearGradient id="bloomPetal" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--petal-2)" />
          <stop offset=".55" stopColor="var(--accent-3)" />
          <stop offset="1" stopColor="var(--accent)" />
        </linearGradient>
        <radialGradient id="bloomHalo">
          <stop offset="0" stopColor="var(--accent-3)" stopOpacity=".45" />
          <stop offset="1" stopColor="var(--accent-3)" stopOpacity="0" />
        </radialGradient>
      </defs>
      <g transform="translate(100 80)">
        <circle className="bloom-halo" r="66" fill="url(#bloomHalo)" />
        <circle className="bloom-orbit" r="58" fill="none" stroke="var(--border-strong)" strokeWidth="1" strokeDasharray="2 7" />
        <g transform="scale(1.45)"><Blossom id="bloomPetal" /></g>
      </g>
      <g transform="translate(40 122)"><g className="bloom-bob"><g transform="scale(.62)"><Blossom id="bloomPetal" /></g></g></g>
      <g transform="translate(164 34)"><g className="bloom-bob b2"><g transform="scale(.46)"><Blossom id="bloomPetal" stamens={false} /></g></g></g>
      {[[10, 20, 0], [0, 58, 1], [30, 4, 2], [-4, 96, 3], [56, -6, 4]].map(([x, y, i]) => (
        <g key={i} transform={`translate(${x} ${y})`}>
          <path className="bloom-fly" d={PETAL} transform="scale(.3)" fill="url(#bloomPetal)"
                style={{ animationDuration: `${7 + i * 1.3}s`, animationDelay: `${-i * 2.1}s` }} />
        </g>
      ))}
      {[[150, 104], [58, 30], [178, 78]].map(([x, y], i) => (
        <path key={i} className="bloom-spark" transform={`translate(${x} ${y})`} d="M0-5L1.2-1.2L5 0L1.2 1.2L0 5L-1.2 1.2L-5 0L-1.2-1.2Z"
              fill="var(--accent-3)" style={{ animationDelay: `${-i * 1.1}s` }} />
      ))}
    </svg>
  )
}
