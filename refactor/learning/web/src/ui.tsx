import * as React from "react"
import { CheckCircle2, Info, Loader2, XCircle } from "lucide-react"
import { useApp } from "./store"

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

export function SakuraArt() {
  return (
    <svg className="art" viewBox="0 0 150 120" aria-hidden>
      <defs>
        <linearGradient id="artG" x1="0" x2="1" y1="0" y2="1">
          <stop offset="0" stopColor="var(--accent-3)" />
          <stop offset="1" stopColor="var(--accent-2)" />
        </linearGradient>
      </defs>
      <path d="M10 104c30-6 52-24 70-52 8-13 20-24 34-30" stroke="var(--text-3)" strokeWidth="3" fill="none" strokeLinecap="round" opacity=".55" />
      <path d="M58 76c6-10 18-14 26-12" stroke="var(--text-3)" strokeWidth="2.4" fill="none" strokeLinecap="round" opacity=".45" />
      {[[112, 22, 1], [84, 50, 0.8], [60, 74, 0.9], [96, 64, 0.65], [126, 40, 0.6], [36, 92, 0.7]].map(([x, y, s], i) => (
        <g key={i} transform={`translate(${x} ${y}) scale(${s})`}>
          {[0, 72, 144, 216, 288].map((a) => (
            <path key={a} d="M0 0c-5-7-4-15 0-17 4 2 5 10 0 17z" fill="url(#artG)" opacity=".85" transform={`rotate(${a})`} />
          ))}
          <circle r="2.6" fill="var(--surface-solid)" />
        </g>
      ))}
    </svg>
  )
}
