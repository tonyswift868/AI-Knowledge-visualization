import * as React from "react"
import { BookOpen, Loader2 } from "lucide-react"
import { Button } from "../ui"
import "./conversation.css"

export type SourceCard = { id: string; text: string; title: string; url: string | null; locator: string }
export type Conversation = { point_id: string; options: string[]; interrupted: boolean }
export type Preparation = { done: number; total: number; text: string; error?: string | null }
export type BankQuestion = { id: string; kind: "choice" | "multi" | "fill" | "short" | "essay" | "calc";
  stem: string; options: string[]; source: { title: string; url: string | null; locator: string }; origin: string;
  purpose: string; blanks?: number; number?: number; total?: number }
export type Act = (method: string, params?: Record<string, unknown>, label?: string) => Promise<boolean>

export function OriginalCards({ cards, open }: { cards: SourceCard[]; open: (id: string) => void }) {
  return <div className="original-cards">{cards.map((s) => <button className="original-card" key={s.id} onClick={() => open(s.id)}>
    <BookOpen size={15} /><span><b>原文 · {s.title}</b><small>{s.locator}</small></span></button>)}</div>
}

export function ConversationActions({ step, conversation, act, busy, ask }: {
  step: string; conversation?: Conversation | null; act: Act; busy: boolean; ask: () => void;
}) {
  return <div className="conversation-actions">
    {conversation?.interrupted && <Button disabled={busy} onClick={() => act("classroom.back")} >回到刚才</Button>}
    {step === "opening" && conversation?.options.map((o, i) => <Button key={`${i}:${o}`} disabled={busy}
      onClick={() => act("classroom.choose", { option: i }, "老师在接着讲…")}>{o}</Button>)}
    {step === "teaching" && <>
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.next", {}, "正在取题…")}>懂了，继续</Button>
      <Button disabled={busy} onClick={() => act("classroom.confused", {}, "换个角度看…")}>没太懂</Button>
      <Button disabled={busy} onClick={ask}>我想问…</Button>
    </>}
    {step === "closing" && <>
      <Button kind="primary" disabled={busy} onClick={() => act("classroom.next", {}, "老师在接着讲…")}>没有，下一个</Button>
      <Button disabled={busy} onClick={ask}>我想问…</Button>
    </>}
  </div>
}

export function MaterialsPreparing({ preparation, act, busy }: { preparation: Preparation; act: Act; busy: boolean }) {
  return <div className="class-note" role="status">
    <Loader2 size={15} className="spin" />{preparation.text} · {preparation.done}/{preparation.total}
    {preparation.error && <><span role="alert">{preparation.error}</span><Button small disabled={busy}
      onClick={() => act("classroom.retry", {}, "重新准备…")}>重试</Button></>}
  </div>
}

export function QuestionInputs({ question: q, act, busy }: { question: BankQuestion; act: Act; busy: boolean }) {
  const [selected, setSelected] = React.useState<number[]>([])
  const [blanks, setBlanks] = React.useState<string[]>([])
  React.useEffect(() => { setSelected([]); setBlanks(Array(q.blanks ?? 0).fill("")) }, [q.id, q.blanks])
  const submit = (value: unknown) => act("classroom.answer", { value }, "正在判定…")
  return <section aria-label="题库题目" className="bank-question-controls">
    <small className="muted">题目出处：{q.source.url ? <a href={q.source.url} target="_blank" rel="noreferrer">{q.source.title}</a> : q.source.title} · {q.source.locator}</small>
    {(q.kind === "choice" || q.kind === "multi") && <div className="class-options">
      {q.options.map((o, i) => <button key={`${i}:${o}`} className={`choice ${selected.includes(i) ? "on" : ""}`} disabled={busy}
        aria-pressed={selected.includes(i)} onClick={() => {
          if (q.kind === "choice") { setSelected([i]); void submit([i]) }
          else setSelected((prev) => prev.includes(i) ? prev.filter((v) => v !== i) : [...prev, i])
        }}><b>{String.fromCharCode(65 + i)}</b><span>{o}</span></button>)}
      {q.kind === "multi" && <Button disabled={busy || !selected.length} onClick={() => submit(selected)}>提交多选</Button>}
    </div>}
    {q.kind === "fill" && <div className="fill-answer">{blanks.map((v, i) => <input key={i} className="input"
      aria-label={`第${i + 1}空`} value={v} disabled={busy} onChange={(e) => setBlanks((prev) => prev.map((s, n) => n === i ? e.target.value : s))} />)}
      <Button disabled={busy || blanks.some((s) => !s.trim())} onClick={() => submit(blanks)}>提交填空</Button></div>}
  </section>
}
