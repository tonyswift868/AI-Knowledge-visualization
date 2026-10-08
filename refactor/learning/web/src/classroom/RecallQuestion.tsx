import * as React from "react"
import { CheckCircle2, ChevronRight, ClipboardCheck } from "lucide-react"
import { Button } from "../ui"
import type { BankQuestion } from "./Conversation"
import "./recall.css"
import { Loading } from '../loading/Loading'

type Reference = { reference: string; key_points: string[] }
type Verdict = { correct: boolean; missed: number[] }
export type RecallRequest = <T>(method: string, params: Record<string, unknown>) => Promise<T>

/** Only reveal or an explicitly revealed persisted snapshot can show answers. */
export function RecallQuestion({ question, request, busy, next }: {
  question: BankQuestion; request: RecallRequest; busy: boolean; next: () => void;
}) {
  const [answer, setAnswer] = React.useState(question.revealed ? question.answer ?? "" : "")
  const [reference, setReference] = React.useState<Reference | null>(question.revealed && question.reference && question.key_points ? {reference: question.reference, key_points: question.key_points} : null)
  const [saved, setSaved] = React.useState(!!question.revealed)
  const [ticked, setTicked] = React.useState<number[]>([])
  const [verdict, setVerdict] = React.useState<Verdict | null>(null)
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState("")
  const locked = React.useRef(false)
  const active = React.useRef(true)
  React.useEffect(() => { active.current = true; return () => { active.current = false } }, [])

  async function run(submit: boolean) {
    if (locked.current || busy || verdict || (!submit && !answer.trim())) return
    locked.current = true; setPending(true); setError("")
    try {
      if (submit) {
        const result = await request<Verdict>("class.recall_submit", { question_id: question.id, ticked: [...ticked].sort((a, b) => a - b) })
        if (!active.current) return
        if (typeof result.correct !== "boolean" || !Array.isArray(result.missed) || result.missed.some((i) =>
          !Number.isInteger(i) || i < 0 || i >= (reference?.key_points.length ?? 0)) || result.correct !== (result.missed.length === 0)) throw new Error("勾选结果未能读取，请重试。")
        setVerdict(result)
      } else {
        const result = await request<Reference>("class.recall_reveal", { question_id: question.id, answer })
        if (!active.current) return
        setSaved(true)
        if (typeof result.reference !== "string" || !result.reference.trim() || !Array.isArray(result.key_points) ||
          result.key_points.length < 1 || result.key_points.length > 5 || result.key_points.some((point) => typeof point !== "string" || !point.trim())) {
          throw new Error("参考答案或要点未能读取，请重试。")
        }
        setReference(result)
      }
    } catch (e) { if (active.current) setError(e instanceof Error ? e.message : "操作未完成，请重试。") }
    finally { locked.current = false; if (active.current) setPending(false) }
  }

  return <section className="recall-question" aria-label="问答题">
    <div className="recall-heading"><ClipboardCheck size={18} /><b>问答题{question.number ? ` · 第 ${question.number}${question.total ? ` / ${question.total}` : ""} 题` : ""}</b></div>
    <p className="recall-stem" data-selectable>{question.stem}</p>
    <div className={`recall-columns ${reference ? "revealed" : ""}`}>
      <div className="recall-own">
        <label htmlFor={`recall-answer-${question.id}`}>{saved ? "你的回答（已保存，不能再修改）" : "先写下你的回答"}</label>
        <textarea id={`recall-answer-${question.id}`} aria-label="先写下你的回答" className="input" rows={5}
          value={answer} readOnly={saved} disabled={pending || busy} placeholder="先凭记忆回答，再对照原文检查要点。"
          onChange={(e) => { if (!saved) setAnswer(e.target.value) }} />
        {!reference && <Button kind="primary" disabled={!answer.trim() || pending || busy} onClick={() => run(false)}>
          写好了，看参考答案</Button>}
      </div>
      {reference && <div className="recall-review">
        <details open className="recall-reference"><summary>原文参考答案</summary><div data-selectable>{reference.reference}</div></details>
        <fieldset disabled={pending || busy || !!verdict}>
          <legend>对照你的回答，勾出你答到的要点</legend>
          {reference.key_points.map((point, i) => <label key={i} className={`recall-point ${verdict?.missed.includes(i) ? "missed" : ""}`}>
            <input type="checkbox" checked={ticked.includes(i)} aria-label={`要点 ${i + 1}`} onChange={(e) =>
              setTicked((previous) => e.target.checked ? [...previous, i] : previous.filter((index) => index !== i))} />
            <span><b>要点 {i + 1}</b>{point}{verdict?.missed.includes(i) && <em>未勾选</em>}</span>
          </label>)}
        </fieldset>
        {!verdict && <Button kind="primary" disabled={pending || busy} onClick={() => run(true)}>
          提交</Button>}
      </div>}
    </div>
    <Loading active={pending} label={reference ? '正在记录答到的要点…' : '正在保存回答并展开参考答案…'}/>
    {error && <p className="recall-error" role="alert">{error}</p>}
    {verdict && <div className={`recall-result ${verdict.correct ? "correct" : "missed"}`} role="status">
      <span>{verdict.correct && <CheckCircle2 size={18} />}{verdict.correct ? "全部答到，算对" : `漏了 ${verdict.missed.length} 条`}</span>
      <Button disabled={pending || busy} onClick={next}><ChevronRight size={16} />继续</Button>
    </div>}
  </section>
}
