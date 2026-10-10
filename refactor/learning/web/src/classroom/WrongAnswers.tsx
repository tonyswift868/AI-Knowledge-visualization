import * as React from "react"
import { call } from "../bridge"
import { Button } from "../ui"
import { ClassroomMarkdown } from "./Markdown"
import { Loading } from "../loading/Loading"
import "./wrong-answers.css"

type Question = { stem: string; kind: string; objective_kind?: string; options?: string[]; correct_options?: number[]; answer?: unknown; reference?: string; key_points?: string[]; explanation?: string }
type Attempt = { id: string; at: string; context: string; attempt_id?: string; correct: boolean; self_correct?: boolean | null;
  answer?: unknown; ticked: number[]; missed: number[]; question: Question; dialogue: {id: string; role: string; content: string}[] }
export type WrongAnswer = {question_id: string; question: Question; wrong_count: number; attempts: Attempt[]}
const CONTEXT: Record<string,string> = {module_test: "模块检测", skip_test: "直接考", retest: "重测", check: "最终检验", practice: "自由检验", quick_confirm: "快速确认（历史）"}
function text(value: unknown): string {
  if (value == null) return "未保存（历史记录）"
  return Array.isArray(value) ? value.map(text).join("；") : String(value)
}

export function WrongAnswers({planId, onClose}: {planId: string; onClose: () => void}) {
  const dialog = React.useRef<HTMLDialogElement>(null)
  const [rows, setRows] = React.useState<WrongAnswer[] | null>(null)
  const [error, setError] = React.useState("")
  const [attempt, retry] = React.useReducer(n=>n+1, 0)
  React.useEffect(()=>{
    let active = true
    setError("")
    call<WrongAnswer[]>("classroom.wrong_answers", {plan_id: planId}).then(r=>{if(active)setRows(r)}).catch(e=>{if(active)setError(e.message)})
    return ()=>{active=false}
  },[planId,attempt])
  React.useEffect(()=>{dialog.current?.showModal()},[])
  return <dialog ref={dialog} className="wrong-answers card" aria-label="错题集" onCancel={onClose}>
    <div className="row between"><h2>错题集</h2><Button small onClick={onClose}>关闭错题集</Button></div>
    <p className="muted small">按答错次数排序，包含每次作答。查看不会改变检验进度。</p>
    {error ? <div role="alert">{error}<Button small onClick={retry}>重试读取</Button></div> : !rows ? <Loading label="正在读取错题…" active/> : !rows.length ? <p>还没有答错过的题。</p> :
      <div className="stack">{rows.map(row=><details key={row.question_id} className="card wrong-question">
        <summary><b>{row.question.stem}</b><span className="muted small"> · 错过 {row.wrong_count} 次 · 共 {row.attempts.length} 次作答</span></summary>
        {row.attempts.map(a=><section key={a.id} className="wrong-attempt">
          <h3>{CONTEXT[a.context] ?? a.context} · {a.correct ? "✓ 算对" : "✗ 算错"}</h3>
          <div className="muted small"><time dateTime={a.at}>{new Date(a.at).toLocaleString()}</time> · {a.self_correct == null ? "程序判定 / 历史结果" : "学生自己判定"}{a.attempt_id && ` · 本次 ${a.attempt_id.slice(0,8)}`}</div>
          <ClassroomMarkdown content={a.question.stem}/>
          <p className="wrong-own"><b>我的回答：</b>{a.question.options?.length && Array.isArray(a.answer) ? a.answer.map(i=>`${Number(i)+1}. ${a.question.options?.[Number(i)] ?? text(i)}`).join("；") : text(a.answer)}</p>
          {a.question.kind === "recall" ? <ul className="wrong-points">{a.question.key_points?.map((point,i)=><li key={i} className={a.missed.includes(i)?"not-hit":"hit"}>
            <span aria-label={a.missed.includes(i)?"没答到":"答到"}>{a.missed.includes(i)?"✗":"✓"}</span> {point}</li>)}</ul> :
            <ul className="wrong-options">{a.question.options?.map((option,i)=>{
              const key=a.question.correct_options ?? a.question.answer
              const right=Array.isArray(key) ? key.includes(i) : String(key).normalize("NFKC").trim()===option.normalize("NFKC").trim()
              const chosen=Array.isArray(a.answer)&&a.answer.includes(i)
              return <li key={i} className={right?"hit":chosen?"not-hit":""}>{right?"✓ ":chosen?"✗ ":""}{i+1}. {option}{chosen?"（我的选择）":""}{right?"（正确答案）":""}</li>
            })}</ul>}
          <details open><summary>参考答案</summary><ClassroomMarkdown content={a.question.reference || text(a.question.answer)}/>
            {a.question.explanation && <ClassroomMarkdown content={a.question.explanation}/>}</details>
          <details><summary>当时前后的对话</summary>{a.dialogue.length ? a.dialogue.map(m=><div key={m.id} className="wrong-dialogue"><b>{m.role==="user"?"我":"课堂"}</b><ClassroomMarkdown content={m.content}/></div>) : <p className="muted">历史记录未保存对应对话。</p>}</details>
        </section>)}
      </details>)}</div>}
  </dialog>
}
