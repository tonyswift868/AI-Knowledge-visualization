import * as React from "react"
import { BookOpen, ChevronDown, ChevronRight, Loader2 } from "lucide-react"
import { Button } from "../ui"
import { ClassroomMarkdown } from "./Markdown"
import "./conversation.css"

export type SourceCard = { id: string; text: string; title: string; url: string | null; locator: string }
export type Conversation = { point_id: string; options: string[]; interrupted: boolean }
export type Preparation = { done: number; total: number; text: string; error?: string | null }
export type BankQuestion = { id: string; kind: "choice" | "multi" | "fill" | "short" | "essay" | "calc" | "recall" | "objective";
  question_type?: "choice" | "multi" | "fill" | "calc"; revealed?: boolean; reference?: string; key_points?: string[]; answer?: string;
  stem: string; options: string[]; source: { title: string; url: string | null; locator: string }; origin: string;
  purpose: string; blanks?: number; number?: number; total?: number }
export type Act = (method: string, params?: Record<string, unknown>, label?: string) => Promise<boolean>

export type OriginalCardsHandle = { reveal: (id: string) => boolean }
export const OriginalCards = React.forwardRef<OriginalCardsHandle, { cards: SourceCard[]; reducedMotion?: boolean }>(
  function OriginalCards({ cards, reducedMotion }, ref) {
    const [shown, setShown] = React.useState(false)
    const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set())
    const [jump, setJump] = React.useState<{ id: string } | null>(null)
    const entries = React.useRef(new Map<string, HTMLDivElement>())
    const listId = React.useId()
    const unique = React.useMemo(() => [...new Map(cards.map(s => [s.id, s])).values()], [cards])
    const groups = React.useMemo(() => {
      const result = new Map<string, { title: string; cards: SourceCard[] }>()
      for (const card of unique) {
        // The current card contract identifies material by its title and URL.
        const key = JSON.stringify([card.title, (card.url || "").split("#")[0]])
        if (!result.has(key)) result.set(key, { title: card.title, cards: [] })
        result.get(key)!.cards.push(card)
      }
      for (const group of result.values()) group.cards.sort((a, b) => a.locator.localeCompare(b.locator, "zh-CN", { numeric: true }))
      return [...result.values()]
    }, [unique])
    React.useImperativeHandle(ref, () => ({ reveal(id) {
      if (!unique.some(s => s.id === id)) return false
      setShown(true); setExpanded(previous => new Set(previous).add(id)); setJump({ id })
      return true
    } }), [unique])
    React.useLayoutEffect(() => {
      if (!jump || !shown) return
      const entry = entries.current.get(jump.id)
      entry?.scrollIntoView({ block: "nearest", behavior: reducedMotion || matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" })
      entry?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true })
    }, [jump, shown, reducedMotion])
    if (!unique.length) return null
    return <section className="original-cards" aria-label="原文出处">
      <button className="original-toggle" aria-expanded={shown} aria-controls={listId} onClick={() => setShown(value => !value)}>
        <BookOpen size={15} /><span>原文出处（{unique.length} 处）</span>{shown ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {shown && <div className="original-groups" id={listId}>{groups.map((group, index) => <section className="original-group" key={index}>
        <h4>{group.title}</h4>
        {group.cards.map(s => {
          const full = expanded.has(s.id)
          const textId = `${listId}-${unique.findIndex(card => card.id === s.id)}`
          return <div className="original-entry" key={s.id} data-source-id={s.id} data-source-target={jump?.id === s.id || undefined}
            ref={element => { if (element) entries.current.set(s.id, element); else entries.current.delete(s.id) }}>
            <button className="original-card" aria-expanded={full} aria-controls={textId} onClick={() => setExpanded(previous => {
              const next = new Set(previous); if (next.has(s.id)) next.delete(s.id); else next.add(s.id); return next
            })}>{full ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<span>{s.locator || "原文段落"}</span><small>{full ? "收起全文" : "展开全文"}</small></button>
            {full && <div className="original-fulltext" id={textId} data-selectable><ClassroomMarkdown content={s.text} /></div>}
          </div>
        })}
      </section>)}</div>}
    </section>
  }
)

export function ConversationActions({ step, conversation, act, busy, ask }: {
  step: string; conversation?: Conversation | null; act: Act; busy: boolean; ask: () => void;
}) {
  return <div className="conversation-actions">
    {conversation?.interrupted && <Button disabled={busy} onClick={() => act("classroom.back")} >回到刚才</Button>}
    {step === "opening" && conversation?.options.map((o, i) => <Button key={`${i}:${o}`} disabled={busy}
      onClick={() => act("classroom.choose", { option: i }, "老师在接着讲…")}>{o}</Button>)}
    {step === "teaching" && <>
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.next", {}, "老师在接着讲…")}>懂了，继续</Button>
      <Button disabled={busy} onClick={() => act("classroom.confused", {}, "换个角度看…")}>没太懂</Button>
      <Button disabled={busy} onClick={ask}>我想问…</Button>
    </>}
    {step === "closing" && <>
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.next", {}, "老师在接着讲…")}>没有，下一个</Button>
      <Button disabled={busy} onClick={ask}>我想问…</Button>
    </>}
    {["testing", "checking", "practice"].includes(step) && <Button disabled={busy} onClick={ask}>我想问…</Button>}
  </div>
}

export function MaterialsPreparing({ preparation, act, busy }: { preparation: Preparation; act: Act; busy: boolean }) {
  return <div className="class-note" role="status">
    <Loader2 size={15} className="spin" />{preparation.text} · {preparation.done}/{preparation.total}
    {preparation.error && <><span role="alert">{preparation.error}</span><Button small disabled={busy}
      onClick={() => act("classroom.retry", {}, "重新准备…")}>重试</Button></>}
    <Button small disabled={busy} onClick={() => act("classroom.cancel_preparation", {}, "取消准备…")}>取消准备</Button>
  </div>
}

export function QuestionInputs({ question: q, act, busy }: { question: BankQuestion; act: Act; busy: boolean }) {
  const [selected, setSelected] = React.useState<number[]>([])
  const [blanks, setBlanks] = React.useState<string[]>([])
  React.useEffect(() => { setSelected([]); setBlanks(Array(q.blanks ?? 0).fill("")) }, [q.id, q.blanks])
  // Stale quick cards from an older host never become a scored interaction.
  if (q.purpose === "quick") return null
  const kind = q.kind === "objective" ? q.question_type : q.kind
  const submit = (value: unknown) => act("classroom.answer", { value }, "正在判定…")
  return <section aria-label="题库题目" className="bank-question-controls">
    <small className="muted">题目出处：{q.source.url ? <a href={q.source.url} target="_blank" rel="noreferrer">{q.source.title}</a> : q.source.title} · {q.source.locator}</small>
    {(kind === "choice" || kind === "multi") && <div className="class-options">
      {q.options.map((o, i) => <button key={`${i}:${o}`} className={`choice ${selected.includes(i) ? "on" : ""}`} disabled={busy}
        aria-pressed={selected.includes(i)} onClick={() => {
          if (kind === "choice") { setSelected([i]); void submit([i]) }
          else setSelected((prev) => prev.includes(i) ? prev.filter((v) => v !== i) : [...prev, i])
        }}><b>{String.fromCharCode(65 + i)}</b><span>{o}</span></button>)}
      {kind === "multi" && <Button disabled={busy || !selected.length} onClick={() => submit(selected)}>提交多选</Button>}
    </div>}
    {kind === "fill" && <div className="fill-answer">{blanks.map((v, i) => <input key={i} className="input"
      aria-label={`第${i + 1}空`} value={v} disabled={busy} onChange={(e) => setBlanks((prev) => prev.map((s, n) => n === i ? e.target.value : s))} />)}
      <Button disabled={busy || blanks.some((s) => !s.trim())} onClick={() => submit(blanks)}>提交填空</Button></div>}
  </section>
}
