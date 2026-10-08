import * as React from "react"
import { ArrowLeft, CheckCircle2, ClipboardList, FileUp, Plus, Trash2 } from "lucide-react"
import { call } from "../bridge"
import { Button } from "../ui"
import { RecallQuestion } from "../classroom/RecallQuestion"
import type { BankQuestion } from "../classroom/Conversation"
import "./self-test.css"
import { Loading } from '../loading/Loading'

type SourceQuestion = { id: string; stem: string; kind: string; options: string[]; reference?: string; answer?: string; key_points?: string[]; reason?: string; warning?: string }
type SetSummary = { id: string; name: string; count: number; missing: number; draft: boolean; state: string }
type QuestionSet = { id: string; name: string; draft: boolean; questions: SourceQuestion[]; missing: SourceQuestion[]; rejected: {stem: string; reason: string}[]; warnings: string[]; run: any }
type TestView = { id: string; name: string; question: BankQuestion | null; passed: number; total: number;
  run: { required: number; elapsed_seconds: number; completed: boolean; stats: Record<string, {streak: number; correct: number; wrong: number}>;
    current: {attempt_id: string; question_id: string; revealed: boolean; verdict: {correct: boolean; missed: number[]} | null} | null };
  results: {id: string; stem: string; correct: number; wrong: number; streak: number}[] }
type Request = typeof call
const duration = (seconds: number) => `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`

export function SelfTest({ back, request = call }: {back: () => void; request?: Request}) {
  const [sets, setSets] = React.useState<SetSummary[]>([])
  const [selected, setSelected] = React.useState<QuestionSet | null>(null)
  const [view, setView] = React.useState<TestView | null>(null)
  const [importing, setImporting] = React.useState(false)
  const [text, setText] = React.useState("")
  const [file, setFile] = React.useState<{token: string; name: string} | null>(null)
  const [name, setName] = React.useState("")
  const [required, setRequired] = React.useState(2)
  const [answers, setAnswers] = React.useState<Record<string, string>>({})
  const [choice, setChoice] = React.useState<number[]>([])
  const [values, setValues] = React.useState<string[]>([])
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState("")
  const [loading,setLoading]=React.useState(true)
  const locked = React.useRef(false)
  const cancel = React.useRef<AbortController | null>(null)
  const activeSet = React.useRef<string | null>(null)
  const live = React.useRef(true)

  const refresh = React.useCallback(async () => {
    setLoading(true)
    try {
      const data = await request<{sets: SetSummary[]}>("selftest.list")
      if (live.current) setSets(data.sets)
    } finally {if(live.current)setLoading(false)}
  }, [request])
  React.useEffect(() => {
    live.current = true
    refresh().catch(e => {setError(e.message);setLoading(false)})
    return () => {
      live.current = false; cancel.current?.abort()
      if (activeSet.current) request("selftest.pause", {set_id: activeSet.current}).catch(() => {})
    }
  }, [refresh, request])
  React.useEffect(() => {
    if (!view || view.run.completed) return
    activeSet.current = view.id
    const timer = setInterval(() => request("selftest.touch", {set_id: view.id}).catch(() => {}), 10000)
    return () => clearInterval(timer)
  }, [view?.id, view?.run.completed, request])
  React.useEffect(() => { setChoice([]); setValues([]) }, [view?.run.current?.attempt_id])

  async function run(action: () => Promise<void>) {
    if (locked.current) return
    locked.current = true; setBusy(true); setError("")
    try { await action() }
    catch (e) { if (live.current) setError(e instanceof Error ? e.message : "操作未完成，请重试。") }
    finally { locked.current = false; if (live.current) setBusy(false) }
  }
  async function open(id: string) {
    await run(async () => {
      const data = await request<{set: QuestionSet}>("selftest.get", {set_id: id})
      setSelected(data.set); setName(data.set.name); setImporting(false); setView(null)
    })
  }
  async function edit(params: Record<string, unknown>) {
    const data = await request<{set: QuestionSet}>("selftest.edit", {set_id: selected!.id, ...params})
    setSelected(data.set); await refresh()
  }
  async function leave() {
    if (view) await request("selftest.pause", {set_id: view.id})
    activeSet.current = null; setView(null); setSelected(null); setImporting(false); await refresh()
  }
  async function begin(resume: boolean) {
    const result = await request<TestView>(resume ? "selftest.resume" : "selftest.start", {set_id: selected!.id, required,
      restart: !!selected!.run?.completed})
    activeSet.current = result.run.completed ? null : result.id
    setView(result)
  }
  async function next() {
    await run(async () => {
      const result = await request<TestView>("selftest.next", {set_id: view!.id, attempt_id: view!.run.current!.attempt_id})
      setView(result)
      if (result.run.completed) { activeSet.current = null; await refresh() }
    })
  }
  async function recall<T>(method: string, params: Record<string, unknown>): Promise<T> {
    const result = await request<TestView>(method === "class.recall_reveal" ? "selftest.reveal" : "selftest.submit",
      {set_id: view!.id, attempt_id: view!.run.current!.attempt_id, ...params})
    if (live.current) setView(result)
    return (method === "class.recall_reveal" ? {reference: result.question!.reference, key_points: result.question!.key_points}
      : result.run.current!.verdict) as T
  }
  const q = view?.question
  const verdict = view?.run.current?.verdict

  return <div className="page self-test-page">
    <Loading active={loading || busy} shape={importing ? 'form' : 'inline'} label={importing ? '正在识别资料中的题目与答案…' : '正在读取或保存题目自测…'} onCancel={cancel.current ? ()=>cancel.current?.abort() : undefined}/>
    <div className="self-test-title"><Button small disabled={busy} onClick={() => run(async () => {
      if (view || selected || importing) await leave(); else back()
    })}><ArrowLeft size={16} />{view || selected || importing ? "返回题集" : "更多工具"}</Button>
      <h2><ClipboardList size={24} />上传题目自测</h2></div>
    <p className="muted">只测你提供的题与答案。客观题由程序判，问答题由你对照参考答案逐条勾要点。</p>
    {error && <p role="alert" className="self-test-error">{error}{!busy && <Button small onClick={()=>void refresh().then(()=>setError('')).catch(e=>{setError(e.message);setLoading(false)})}>重试读取题集</Button>}</p>}
    {view ? <>
      <div className="card self-test-progress"><b>{view.name}</b><span>已达标 {view.passed} / {view.total} · 每题连续答对 {view.run.required} 遍</span></div>
      {view.run.completed ? <section className="card self-test-complete" aria-label="自测完成">
        <CheckCircle2 size={40} /><h3>全部达标，自测完成</h3><p>用时 {duration(view.run.elapsed_seconds)}</p>
        <div className="self-test-results">{view.results.map(result => <div key={result.id}><b>{result.stem}</b>
          <span>答对 {result.correct} 次 · 答错 {result.wrong} 次 · 连续答对 {result.streak} 次</span></div>)}</div>
        <Button disabled={busy} onClick={() => run(leave)}>返回题集</Button>
      </section> : q && <section className="self-test-current" aria-label="自测中">
        <p className="muted">本题连续答对 {Math.min(view.run.stats[q.id].streak, view.run.required)} / {view.run.required} 遍。{view.run.stats[q.id].streak >= view.run.required ? "为隔开重复题，再复习一次。" : ""}答错会清零。</p>
        {verdict ? <div className="card self-test-verdict" role="status"><h3>{q.stem}</h3>
          <b>{verdict.correct ? "答对了" : q.kind === "recall" ? `漏了 ${verdict.missed.length} 条要点，连续计数清零` : "答错了，连续计数清零"}</b>
          <p>你的回答：{q.answer}</p><details open><summary>原文参考答案</summary><div className="self-test-original">{q.reference}</div></details>
          {q.key_points?.map((point, i) => <p key={i} className={verdict.missed.includes(i) ? "self-test-error" : ""}>要点 {i+1}：{point}{verdict.missed.includes(i) ? "（未勾选）" : ""}</p>)}
          <Button kind="primary" disabled={busy} onClick={next}>继续</Button>
        </div> : q.kind === "recall" ? <RecallQuestion key={view.run.current!.attempt_id} question={q} request={recall} busy={busy} next={next} />
          : <div className="card self-test-objective"><h3>{q.stem}</h3>
            {q.options.length ? <fieldset disabled={busy}><legend>{q.question_type === "multi" ? "选择所有正确选项" : "选择一个选项"}</legend>
              {q.options.map((option, i) => <label key={i}><input type={q.question_type === "multi" ? "checkbox" : "radio"} name="self-test-choice"
                checked={choice.includes(i)} onChange={e => setChoice(q.question_type === "multi" ? e.target.checked ? [...choice, i] : choice.filter(v => v !== i) : [i])} />{option}</label>)}</fieldset>
              : Array.from({length: q.question_type === "fill" ? q.blanks || 1 : 1}, (_, i) => <label key={i}> {q.question_type === "fill" ? `第 ${i+1} 个空` : "计算结果（可带单位）"}
                <input className="input" aria-label={q.question_type === "fill" ? `第 ${i+1} 个空` : "计算结果"} value={values[i] || ""} disabled={busy}
                  onChange={e => setValues(old => {const updated = [...old]; updated[i] = e.target.value; return updated})} /></label>)}
            <Button kind="primary" disabled={busy || (q.options.length ? !choice.length : !values.length || values.some(v => !v?.trim()) || values.length < (q.blanks || 1))}
              onClick={() => run(async () => setView(await request<TestView>("selftest.submit", {set_id: view.id, attempt_id: view.run.current!.attempt_id,
                answer: q.options.length ? choice : q.question_type === "fill" ? values : values[0]})))}>提交答案</Button>
          </div>}
      </section>}
    </> : selected ? <>
      <section className="card self-test-import-result" aria-label="导入结果">
        <label>题集名称<input className="input" value={name} onChange={e => setName(e.target.value)} /></label>
        <h3>识别出 {selected.questions.length} 道题（客观 {selected.questions.filter(q => q.kind === "objective").length}、问答 {selected.questions.filter(q => q.kind === "recall").length}），另有 {selected.missing.length} 道没有答案未加入</h3>
        {selected.warnings.map((warning, i) => <p className="muted" key={i}>{warning}</p>)}
        <p className="muted">题集与进度保存在当前账号的本机目录，暂不跨设备同步。</p>
        <div className="self-test-actions"><Button disabled={busy || !name.trim()} onClick={() => run(() => edit({name, save: true}))}>{selected.draft ? "保存题集" : "保存名称"}</Button>
          {!selected.run || selected.run.completed ? <><label>每道题连续答对<select className="input" aria-label="连续答对次数" value={required} onChange={e => setRequired(Number(e.target.value))}><option value={2}>2 遍</option><option value={3}>3 遍</option></select></label>
            <Button kind="primary" disabled={busy || !selected.questions.length} onClick={() => run(async () => {await edit({name, save: true}); await begin(false)})}>{selected.run?.completed ? "再测一轮" : "开始自测"}</Button></>
            : <Button kind="primary" disabled={busy} onClick={() => run(() => begin(true))}>{selected.run.current?.verdict ? "继续自测" : "接着做"}</Button>}
        </div>
      </section>
      <section className="card self-test-question-list"><h3>已加入的题</h3>{selected.questions.map((question, i) => <details key={question.id}><summary>第 {i+1} 题 · {question.stem}</summary>
        <div>{question.options.map((option, j) => <p key={j}>{option}</p>)}<p className="self-test-original">参考答案：{question.reference || question.answer}</p>
          {question.warning && <p>{question.warning}</p>}<Button small disabled={busy} onClick={() => run(() => edit({question_id: question.id}))}><Trash2 size={14} />删除这道题</Button></div></details>)}</section>
      {!!selected.missing.length && <section className="card self-test-missing"><h3>没有答案，未加入自测</h3><p className="muted">请从你的资料补上原答案；程序不会替你补答案。</p>
        {selected.missing.map(question => <details key={question.id}><summary>{question.stem}</summary><p>{question.options.join("\n")}</p>
          <textarea className="input" aria-label={`补充答案：${question.stem}`} rows={3} value={answers[question.id] || ""} onChange={e => setAnswers(old => ({...old, [question.id]: e.target.value}))} />
          <Button small disabled={busy || (!!selected.run && !selected.run.completed) || !answers[question.id]?.trim()} onClick={() => run(async () => {
            cancel.current = new AbortController()
            const data = await request<{set: QuestionSet}>("selftest.add_answer", {set_id: selected.id, question_id: question.id, answer: answers[question.id]}, {signal: cancel.current.signal})
            setSelected(data.set); await refresh()
          })}>补上答案并加入</Button><Button small disabled={busy} onClick={() => run(() => edit({question_id: question.id}))}>移除</Button></details>)}</section>}
      {!!selected.rejected.length && <section className="card"><h3>未能核对，未加入</h3>{selected.rejected.map((row, i) => <details key={i}><summary>{row.stem}</summary><p>{row.reason} 请核对原题格式后重新导入。</p></details>)}</section>}
    </> : importing ? <section className="card self-test-import">
      <h3>导入你自己的题目</h3><p>支持 PDF、Word（.docx）、文本、Markdown、HTML、CSV 等可提取文字的文件。图片请先转为文字。</p>
      <Button disabled={busy} onClick={() => run(async () => {const result = await request<{file: {token: string; name: string} | null}>("selftest.pick"); setFile(result.file)})}><FileUp size={17} />选择文件</Button>
      {file && <p>{file.name} <Button small disabled={busy} onClick={() => setFile(null)}>改用粘贴文字</Button></p>}
      {!file && <label>或粘贴题目和答案<textarea className="input" aria-label="题目和答案" rows={12} value={text} disabled={busy} onChange={e => setText(e.target.value)} placeholder="把题干、选项和原参考答案一起粘贴到这里。没有答案的题也会列出来。" /></label>}
      <div className="self-test-actions"><Button kind="primary" disabled={busy || (!file && !text.trim())} onClick={() => run(async () => {
        cancel.current = new AbortController()
        const data = await request<{set: QuestionSet}>("selftest.import", file ? {file_token: file.token} : {text}, {signal: cancel.current.signal})
        if (live.current) {setSelected(data.set); setName(data.set.name); setImporting(false); await refresh()}
      })}>拆出原题</Button>
        {busy && <Button onClick={() => cancel.current?.abort()}>取消拆题</Button>}</div>
    </section> : <>
      <Button kind="primary" disabled={busy} onClick={() => {setImporting(true); setText(""); setFile(null)}}><Plus size={17} />上传或粘贴题目</Button>
      {!loading && !error && !sets.length && <div className="card self-test-empty">还没有题集。先导入一份带答案的题吧。</div>}
      <div className="grid two self-test-sets">{sets.map(item => <div className="card" key={item.id}>
        <h3>{item.name}</h3><p>{item.count} 道可测 · {item.missing} 道缺答案{item.draft ? " · 尚未确认保存" : ""}</p>
        <div className="self-test-actions"><Button disabled={busy} onClick={() => open(item.id)}>{item.state === "in_progress" ? "接着做" : item.state === "completed" ? "查看 / 再测" : "打开题集"}</Button>
          <Button small disabled={busy} onClick={() => {if (window.confirm(`删除题集“${item.name}”及其自测进度？`)) run(async () => {await request("selftest.delete", {set_id: item.id}); await refresh()})}}><Trash2 size={15} />删除</Button></div>
      </div>)}</div>
    </>}
  </div>
}
