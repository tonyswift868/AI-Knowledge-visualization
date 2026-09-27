import * as React from "react"
import { CheckCircle2, Info, Loader2, XCircle } from "lucide-react"
import { useApp } from "./store"

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
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} onClick={() => dismiss(t.id)}>
          {t.kind === "ok" ? <CheckCircle2 /> : t.kind === "error" ? <XCircle /> : <Info />}
          <div>{t.text}</div>
        </div>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ controls
export function Button({ kind, small, busy, children, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "ghost" | "danger"; small?: boolean; busy?: boolean }) {
  return (
    <button {...rest} className={`btn ${kind ?? ""} ${small ? "small" : ""} ${rest.className ?? ""}`} disabled={rest.disabled || busy}>
      {busy && <Loader2 className="spin" />}
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

export function Field({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
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
