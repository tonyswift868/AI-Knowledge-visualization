import * as React from "react"
import { BookOpen, ChevronDown, ChevronRight, Library, LoaderCircle, X } from "lucide-react"
import { Button } from "../ui"
import { ClassroomMarkdown } from "./Markdown"
import "./conversation.css"

export type SourceCard = { id: string; text: string; title: string; url: string | null; locator: string }
export type Conversation = { point_id: string; options: string[]; interrupted: boolean }
export type NavigationEntry = { id: string; title: string; reason: string; sources: SourceCard[] }
export type Concept = { point_id: string; title: string; goal: string; subpoint_id: string; title_current: string; number: number; total: number;
  notes: { text: string; source: SourceCard }[] }

function ModuleNavigation({ entries }: { entries: NavigationEntry[] }) {
  return <>
    <p className="small muted">供查阅，不讲解、不检测、不计时长。</p>
    {entries.map(entry => <section key={entry.id}><h4>{entry.title}</h4><p>{entry.reason}</p><OriginalCards cards={entry.sources} /></section>)}
  </>
}

function ConceptMaterials({ concept }: { concept: Concept }) {
  const refs = React.useRef(new Map<number, OriginalCardsHandle>())
  return <section aria-label="小要点讲义">
      <p className="small muted">掌握目标：{concept.goal}</p>
      {concept.notes.map((note, index) => <div key={index} className="class-note-entry">
        <ClassroomMarkdown content={note.text} />
        <Button small kind="ghost" onClick={() => refs.current.get(index)?.reveal(note.source.id)}>看这条的原文</Button>
        <OriginalCards cards={[note.source]} ref={handle => { if (handle) refs.current.set(index, handle); else refs.current.delete(index) }} />
      </div>)}
  </section>
}

/** Non-modal native dialog: it overlays the conversation without reflowing it. */
export function MaterialToolbar({ concept, entries }: { concept?: Concept | null; entries: NavigationEntry[] }) {
  const [open, setOpen] = React.useState<'notes' | 'navigation' | null>(null)
  const dialog = React.useRef<HTMLDialogElement>(null)
  const trigger = React.useRef<HTMLButtonElement | null>(null)
  const id = React.useId()
  const navigationKey = entries.map(entry => entry.id).join('|')
  const close = React.useCallback(() => { setOpen(null); trigger.current?.focus({ preventScroll: true }) }, [])
  React.useEffect(() => { setOpen(null) }, [concept?.subpoint_id, navigationKey])
  React.useEffect(() => {
    if (!open || !dialog.current) return
    const popup = dialog.current
    popup.show()
    popup.focus({ preventScroll: true })
    const outside = (event: PointerEvent) => {
      const target = event.target as Element
      if (!popup.contains(target) && !target.closest('[data-materials]')) { event.preventDefault(); close() }
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); close() } }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => { popup.close(); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape) }
  }, [open, close])
  if (!concept && !entries.length) return null
  function toggle(kind: 'notes' | 'navigation', button: HTMLButtonElement) {
    trigger.current = button
    if (open === kind) close(); else setOpen(kind)
  }
  return <div className="class-material-row129">
    {concept && <span className="class-subpoint129" title={`小要点 ${concept.number}/${concept.total} · ${concept.title_current}`}>
      小要点 {concept.number}/{concept.total} · {concept.title_current}</span>}
    <div className="class-material-buttons129">
      {!!concept?.notes.length && <button data-materials="notes" aria-label={`讲义（${concept.notes.length}）`} title="讲义"
        aria-expanded={open === 'notes'} aria-controls={id} onClick={e => toggle('notes', e.currentTarget)}>
        <BookOpen size={15}/><span>讲义（{concept.notes.length}）</span></button>}
      {!!entries.length && <button data-materials="navigation" aria-label={`资料导航（${entries.length}）`} title="资料导航"
        aria-expanded={open === 'navigation'} aria-controls={id} onClick={e => toggle('navigation', e.currentTarget)}>
        <Library size={15}/><span>资料导航（{entries.length}）</span></button>}
    </div>
    {open && <dialog ref={dialog} id={id} tabIndex={-1} className="class-material-popup129" aria-label={open === 'notes' ? '讲义' : '资料导航'}>
      <header><b>{open === 'notes' ? '讲义' : '资料导航'}</b><button aria-label="关闭资料浮层" onClick={close}><X size={16}/></button></header>
      {open === 'notes' && concept ? <ConceptMaterials concept={concept}/> : <ModuleNavigation entries={entries}/>}
    </dialog>}
  </div>
}
export type Preparation = { done: number; total: number; text: string; error?: string | null }
export type BankQuestion = { id: string; kind: "choice" | "multi" | "fill" | "short" | "essay" | "calc" | "recall" | "objective";
  question_type?: "choice" | "multi" | "fill" | "calc"; revealed?: boolean; ticked?: number[]; reference?: string; key_points?: string[]; answer?: string;
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

export function PreparationBadge({ preparation, act, busy, reducedMotion }: { preparation: Preparation; act: Act; busy: boolean; reducedMotion: boolean }) {
  if (preparation.error) return <button className="class-preparation129 failed" title={preparation.error} disabled={busy}
    onClick={() => act('classroom.retry', {}, '重新准备…')}>准备失败，重试</button>
  return <span className="class-preparation129" role="status" title={preparation.text} data-reduced={reducedMotion || undefined}>
    <LoaderCircle size={13}/><span>题目准备中 {preparation.done}/{preparation.total}</span></span>
}

export function PreparationWait({ preparation, act, busy }: { preparation?: Preparation | null; act: Act; busy: boolean }) {
  return <div className="class-wait129" role="status">
    <span>正在准备这个模块的题（{preparation?.done ?? 0}/{preparation?.total ?? 0}），备好后自动继续</span>
    <Button small disabled={busy} onClick={() => act('classroom.cancel_preparation', {}, '回去接着学…')}>先回去接着学</Button>
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
