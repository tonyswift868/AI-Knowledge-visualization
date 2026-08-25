import { FormEvent, useEffect, useMemo, useState } from 'react'
import { REVIEW_CAMPAIGN_STORAGE_KEY, reviewApi, type ReviewCampaignView } from './reviewApi'

function newSubmissionId() {
  return `web-${crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`
}

function percent(value: number) {
  return `${Math.round(value * 100)}%`
}

const passLabel = {
  pending: '待本轮完成后判定',
  passed: '本轮通过',
  not_passed: '本轮未通过',
}

export function ReviewDemoApp() {
  const [campaign, setCampaign] = useState<ReviewCampaignView | null>(null)
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [recovered, setRecovered] = useState(false)
  const [hint, setHint] = useState('')
  const [explanation, setExplanation] = useState('')

  useEffect(() => {
    const campaignId = localStorage.getItem(REVIEW_CAMPAIGN_STORAGE_KEY)
    if (!campaignId) {
      setLoading(false)
      return
    }
    reviewApi.get(campaignId)
      .then((value) => {
        setCampaign(value)
        setRecovered(true)
        setHint(value.question?.active_hint ?? '')
      })
      .catch(() => localStorage.removeItem(REVIEW_CAMPAIGN_STORAGE_KEY))
      .finally(() => setLoading(false))
  }, [])

  const progress = useMemo(() => {
    if (!campaign) return 0
    return Math.min(100, Math.round((campaign.metrics.evaluated_items / campaign.metrics.target_items) * 100))
  }, [campaign])

  async function perform(action: () => Promise<ReviewCampaignView>) {
    setBusy(true)
    setError('')
    try {
      const value = await action()
      setCampaign(value)
      setHint(value.hint?.text ?? value.question?.active_hint ?? '')
      setExplanation(value.explanation?.text ?? '')
      return value
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败')
      return null
    } finally {
      setBusy(false)
    }
  }

  async function createCampaign() {
    const value = await perform(reviewApi.create)
    if (value) {
      localStorage.setItem(REVIEW_CAMPAIGN_STORAGE_KEY, value.campaign_id)
      setRecovered(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!campaign || !answer.trim()) return
    const value = await perform(() => reviewApi.submit(campaign.campaign_id, answer.trim(), newSubmissionId()))
    if (value) setAnswer('')
  }

  async function startRound() {
    if (!campaign) return
    await perform(() => reviewApi.startRound(campaign.campaign_id))
  }

  async function nextQuestion() {
    if (!campaign) return
    setHint('')
    setExplanation('')
    await perform(() => reviewApi.nextQuestion(campaign.campaign_id))
  }

  async function resetDemo() {
    localStorage.removeItem(REVIEW_CAMPAIGN_STORAGE_KEY)
    setCampaign(null)
    setAnswer('')
    setHint('')
    setExplanation('')
    setRecovered(false)
  }

  if (loading) return <main className="review-shell review-loading">正在恢复 Review…</main>

  if (!campaign) {
    return (
      <main className="review-shell review-welcome">
        <section className="welcome-card">
          <span className="demo-pill">DEMO · 完全离线</span>
          <p className="eyebrow">MentorFlow Review</p>
          <h1>不是“看过”，是能从记忆里取出来。</h1>
          <p className="welcome-copy">10 个学习科学概念，20 道可信题，固定 3 轮。弱项会滚回确认，全程不需要 API Key。</p>
          <div className="fixture-strip" aria-label="演示活动规模">
            <span><strong>10</strong> concepts</span>
            <span><strong>20</strong> questions</span>
            <span><strong>3</strong> fixed rounds</span>
          </div>
          <button className="primary wide" onClick={createCampaign} disabled={busy}>创建离线复习活动</button>
          {error && <p role="alert" className="review-error">{error}</p>}
        </section>
      </main>
    )
  }

  const showResult = Boolean(campaign.last_result)
  const canFinish = campaign.state === 'round_evaluated'
  const canStartRound = campaign.state === 'created' || campaign.state === 'between_rounds'
  const complete = campaign.state === 'completed'

  return (
    <main className="review-shell">
      <header className="review-header">
        <div>
          <div className="header-badges">
            <span className="demo-pill">{campaign.demo_label}</span>
            {recovered && <span className="recovery-pill">已从关闭页面前恢复</span>}
          </div>
          <p className="eyebrow">MentorFlow Review</p>
          <h1>{campaign.round.display}</h1>
        </div>
        <div className="header-actions">
          {campaign.state === 'round_active' && <button className="ghost" disabled={busy} onClick={() => perform(() => reviewApi.pause(campaign.campaign_id))}>暂停</button>}
          {campaign.state === 'paused' && <button className="primary" disabled={busy} onClick={() => perform(() => reviewApi.resume(campaign.campaign_id))}>继续</button>}
          <button className="ghost" onClick={resetDemo}>重新开始</button>
        </div>
      </header>

      <section className="metrics-grid" aria-label="本轮复习指标">
        <article><span>当前 retrieval score</span><strong>{percent(campaign.metrics.retrieval_score)}</strong><small>依据回忆结果，不按见题数通过</small></article>
        <article><span>进度</span><strong>{campaign.metrics.evaluated_items} / {campaign.metrics.target_items}</strong><div className="progress-track"><i style={{ width: `${progress}%` }} /></div></article>
        <article><span>弱项</span><strong>{campaign.metrics.weak_count}</strong><small>仍待稳定 {campaign.metrics.remaining_weak_items} 项</small></article>
        <article className={`pass-card ${campaign.metrics.pass_status}`}><span>本轮判定</span><strong>{passLabel[campaign.metrics.pass_status]}</strong><small>分数 + 必答项 + critical gates</small></article>
      </section>

      <section className="critical-row" aria-label="关键概念状态">
        <span>Critical concepts</span>
        {campaign.metrics.critical_concepts.map((concept) => <b className={`concept-chip ${concept.status}`} key={concept.concept_id}>{concept.label} · {concept.status === 'passed' ? '稳' : concept.status === 'weak' ? '弱' : '待答'}</b>)}
      </section>

      {error && <p role="alert" className="review-error">{error}</p>}

      {campaign.state === 'paused' && (
        <section className="review-card centered"><p className="eyebrow">活动已暂停</p><h2>你的轮次、题目与提示状态都还在。</h2><button className="primary" disabled={busy} onClick={() => perform(() => reviewApi.resume(campaign.campaign_id))}>从当前题继续</button></section>
      )}

      {canStartRound && (
        <section className="review-card centered">
          {campaign.round_result ? <RoundResult result={campaign.round_result} /> : <><p className="eyebrow">Active Recall</p><h2>准备好后再开始计时作答。</h2><p>每题先独立回答；提示会被诚实记录为“依赖提示”。</p></>}
          <button className="primary" disabled={busy} onClick={startRound}>{campaign.state === 'created' ? '开始第 1 / 3 轮' : `开始第 ${campaign.round.current + 1} / 3 轮`}</button>
        </section>
      )}

      {complete && campaign.campaign_result && (
        <section className="review-card campaign-complete">
          <p className="eyebrow">3 / 3 轮完成</p>
          <h2>{campaign.campaign_result.all_rounds_passed ? '三轮均通过' : '活动完成，仍有知识点需要复习'}</h2>
          <div className="completion-numbers"><span><strong>{percent(campaign.campaign_result.retrieval_score)}</strong>活动提取得分</span><span><strong>{campaign.campaign_result.passed_rounds} / 3</strong>通过轮次</span><span><strong>{campaign.campaign_result.remaining_weak_items}</strong>累计弱项观察</span></div>
          <button className="primary" onClick={resetDemo}>再体验一次</button>
        </section>
      )}

      {showResult && campaign.last_result && campaign.state !== 'paused' && (
        <section className={`review-card result-card ${campaign.last_result.tone}`}>
          <p className="eyebrow">本题结果 · {campaign.last_result.concept_label}</p>
          <h2>{campaign.last_result.label}</h2>
          <p>{campaign.last_result.feedback}</p>
          <div className="answer-review"><span>提交后参考答案</span><strong>{campaign.last_result.expected_answer}</strong><p>{campaign.last_result.explanation}</p></div>
          {canFinish
            ? <button className="primary" disabled={busy} onClick={() => perform(() => reviewApi.finish(campaign.campaign_id))}>完成本轮并判定</button>
            : <button className="primary" disabled={busy} onClick={nextQuestion}>下一题</button>}
        </section>
      )}

      {!showResult && campaign.question && campaign.state === 'round_active' && (
        <section className="review-card question-card">
          {campaign.question.weak_repeat && <div className="weak-repeat">↻ {campaign.question.repeat_message}</div>}
          <div className="question-meta"><span>{campaign.question.concept_label}</span>{campaign.question.critical && <b>CRITICAL</b>}<span>第 {campaign.question.occurrence_number} 次出现</span></div>
          <h2>{campaign.question.prompt}</h2>
          <form onSubmit={submit}>
            <label htmlFor="review-answer">用自己的话回答</label>
            <textarea id="review-answer" value={answer} onChange={(event) => setAnswer(event.target.value)} placeholder="先从记忆中提取，不要翻资料…" rows={5} autoFocus />
            {hint && <div className="hint-box"><span>本地提示</span><p>{hint}</p></div>}
            {explanation && <div className="explanation-box"><span>为什么？</span><p>{explanation}</p></div>}
            <div className="answer-actions">
              <button type="button" className="ghost" disabled={busy || Boolean(hint)} onClick={() => perform(() => reviewApi.hint(campaign.campaign_id))}>给我一个提示</button>
              <button type="button" className="ghost" disabled={busy} onClick={() => perform(() => reviewApi.explain(campaign.campaign_id))}>为什么？</button>
              <button type="submit" className="primary" disabled={busy || !answer.trim()}>提交回答</button>
            </div>
          </form>
        </section>
      )}

      <footer className="cost-seam" aria-label="成本指示器">
        <span>Provider calls <strong>{campaign.cost.provider_calls}</strong></span>
        <span>Local-only operations <strong>{campaign.cost.local_only_operations}</strong></span>
        <span>FSRS <strong>只排题，不改 3 轮</strong></span>
      </footer>
    </main>
  )
}

function RoundResult({ result }: { result: NonNullable<ReviewCampaignView['round_result']> }) {
  return <div className="round-result"><p className="eyebrow">第 {result.round_number} 轮结果</p><h2 className={result.passed ? 'passed-text' : 'failed-text'}>{result.status_label}</h2><div className="completion-numbers"><span><strong>{percent(result.retrieval_score)}</strong>retrieval score</span><span><strong>{result.remaining_weak_items}</strong>remaining weak</span></div>{result.failed_gates.length > 0 && <p>未通过门槛：{result.failed_gates.join('、')}</p>}</div>
}
