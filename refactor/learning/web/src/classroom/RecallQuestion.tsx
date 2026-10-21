import * as React from "react"
import { CheckCircle2, ChevronRight, ClipboardCheck } from "lucide-react"
import { Button } from "../ui"
import { MicButton, useVoiceInput } from "../voice"
import type { BankQuestion } from "./Conversation"
import "./recall.css"
import { Loading } from '../loading/Loading'

type Reference = { reference: string; key_points: string[] }
type Verdict = { correct: boolean; missed: number[] }
export type RecallRequest = <T>(method: string, params: Record<string, unknown>) => Promise<T>

/** Only reveal or an explicitly revealed persisted snapshot can show answers. */
export function RecallQuestion({ question, request, busy, next, selfJudge = true, voiceContext, voiceScopeKey }: {
  question: BankQuestion; request: RecallRequest; busy: boolean; next: () => void; selfJudge?: boolean;
  voiceContext?: { plan_id: string; module_id: string; point_id?: string };
  voiceScopeKey?: string;
}) {
  const [answer, setAnswer] = React.useState(question.revealed ? question.answer ?? "" : "")
  const [reference, setReference] = React.useState<Reference | null>(question.revealed && question.reference && question.key_points ? {reference: question.reference, key_points: question.key_points} : null)
  const [saved, setSaved] = React.useState(!!question.revealed)
  const [ticked, setTicked] = React.useState<number[]>(question.ticked ?? [])
  const [verdict, setVerdict] = React.useState<Verdict | null>(null)
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState("")
  const locked = React.useRef(false)
  const active = React.useRef(true)
  // The callback can arrive between a successful reveal response and React's
  // next effect. Keep this synchronous fence so an immutable answer never
  // receives a late ASR transcript.
  const answerEditable = React.useRef(!question.revealed)
  const onVoiceChange = React.useCallback((value: string) => {
    if (answerEditable.current) setAnswer(value)
  }, [])
  const answerVoice = useVoiceInput({
    value: answer,
    onChange: onVoiceChange,
    context: voiceContext,
    scopeKey: voiceScopeKey ?? question.id,
    disabled: saved || pending || busy,
  })
  const voiceBusy = ["checking", "starting", "recording", "finishing"].includes(answerVoice.state)
  React.useEffect(() => { active.current = true; return () => { active.current = false } }, [])

  async function run(submit: boolean, correct?: boolean) {
    if (locked.current || busy || verdict || (!submit && (!answer.trim() || voiceBusy))) return
    locked.current = true; setPending(true); setError("")
    try {
      if (submit) {
        const result = await request<Verdict>("class.recall_submit", { question_id: question.id, ticked: [...ticked].sort((a, b) => a - b), correct })
        if (!active.current) return
        if (typeof result.correct !== "boolean" || !Array.isArray(result.missed) || result.missed.some((i) =>
          !Number.isInteger(i) || i < 0 || i >= (reference?.key_points.length ?? 0))) throw new Error("勾选结果未能读取，请重试。")
        setVerdict(result)
      } else {
        const result = await request<Reference>("class.recall_reveal", { question_id: question.id, answer })
        if (!active.current) return
        answerEditable.current = false
        setSaved(true)
        if (typeof result.reference !== "string" || !result.reference.trim() || !Array.isArray(result.key_points) ||
          result.key_points.length < 1 || result.key_points.some((point) => typeof point !== "string" || !point.trim())) {
          throw new Error("参考答案或要点未能读取，请重试。")
        }
        setReference(result)
      }
    } catch (e) { if (active.current) setError(e instanceof Error ? e.message : "操作未完成，请重试。") }
    finally { locked.current = false; if (active.current) setPending(false) }
  }

  async function tick(index: number, checked: boolean) {
    if (locked.current || busy || verdict) return
    // Uploaded-question self-test has its own grading/storage API, not classroom drafts.
    if (!selfJudge) { setTicked(previous => checked ? [...previous, index] : previous.filter(i => i !== index)); return }
    const previous = ticked
    const updated = (checked ? [...ticked, index] : ticked.filter(i => i !== index)).sort((a,b) => a-b)
    locked.current = true; setPending(true); setTicked(updated); setError("")
    try {
      const result = await request<{failure?: {message: string}}>("class.recall_draft", {question_id: question.id, ticked: updated})
      if (result.failure) throw new Error(result.failure.message)
    } catch (e) { if (active.current) { setTicked(previous); setError(e instanceof Error ? e.message : "勾选未保存，请重试。") } }
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
        {!reference && <div className="recall-answer-actions">
          <div data-testid="recall-answer-mic" role="group" aria-label="问答题作答语音输入"><MicButton voice={answerVoice} /></div>
          <Button kind="primary" disabled={!answer.trim() || pending || busy || voiceBusy} onClick={() => run(false)}>
            写好了，看参考答案</Button>
        </div>}
      </div>
      {reference && <div className="recall-review">
        <details open className="recall-reference"><summary>原文参考答案</summary><div data-selectable>{reference.reference}</div></details>
        <fieldset disabled={pending || busy || !!verdict}>
          <legend>对照你的回答，勾出你答到的要点</legend>
          {reference.key_points.map((point, i) => <label key={i} className={`recall-point ${verdict?.missed.includes(i) ? "missed" : ""}`}>
            <input type="checkbox" checked={ticked.includes(i)} aria-label={`要点 ${i + 1}`} onChange={(e) =>
              void tick(i, e.target.checked)} />
            <span><b>要点 {i + 1}</b>{point}{verdict?.missed.includes(i) && <em>✗ 没答到</em>}</span>
          </label>)}
        </fieldset>
        {!verdict && (selfJudge ? <div className="row" role="group" aria-label="自己判断这次作答">
          <Button kind="primary" disabled={pending || busy} onClick={() => run(true, true)}>算我答对</Button>
          <Button disabled={pending || busy} onClick={() => run(true, false)}>算我答错</Button>
        </div> : <Button kind="primary" disabled={pending || busy} onClick={() => run(true)}>提交</Button>)}
      </div>}
    </div>
    <Loading active={pending} label={reference ? '正在记录答到的要点…' : '正在保存回答并展开参考答案…'}/>
    {error && <p className="recall-error" role="alert">{error}</p>}
    {verdict && <div className={`recall-result ${verdict.correct ? "correct" : "missed"}`} role="status">
      <span>{verdict.correct && <CheckCircle2 size={18} />}{selfJudge ? (verdict.correct ? "你选择：算我答对" : "你选择：算我答错") : (verdict.correct ? "全部答到，算对" : `漏了 ${verdict.missed.length} 条`)}</span>
      <Button disabled={pending || busy} onClick={next}><ChevronRight size={16} />继续</Button>
    </div>}
  </section>
}
