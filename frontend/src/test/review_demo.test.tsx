import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReviewDemoApp } from '../features/review-demo/ReviewDemoApp'
import { REVIEW_CAMPAIGN_STORAGE_KEY, type ReviewCampaignView } from '../features/review-demo/reviewApi'

const activeCampaign: ReviewCampaignView = {
  schema_version: 'review-product-v1.v1', campaign_id: 'review-demo-1', mode: 'demo', demo: true,
  demo_label: 'DEMO · 完全离线', state: 'round_active',
  round: { current: 1, fixed_total: 3, completed: 0, display: '第 1 / 3 轮', fsrs_controls_round_count: false },
  fixture: { concept_count: 10, question_count: 20, round_count: 3 },
  question: { item_id: 'q1', concept_id: 'c1', concept_label: '主动回忆', prompt: '提交前安全题目', occurrence_number: 1, critical: true, required: true, weak_repeat: false, repeat_message: null, active_hint: null },
  last_result: null, round_result: null,
  metrics: { retrieval_score: 0, evaluated_items: 0, target_items: 10, weak_count: 0, remaining_weak_items: 0, pass_status: 'pending', critical_concepts: [{ concept_id: 'c1', label: '主动回忆', status: 'pending' }] },
  cost: { provider_calls: 0, local_only_operations: 2, mode: 'local_only' }, campaign_result: null,
  resume: { recoverable: true, storage_key: REVIEW_CAMPAIGN_STORAGE_KEY },
}

function response(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } }))
}

describe('ReviewDemoApp', () => {
  beforeEach(() => { localStorage.clear(); vi.restoreAllMocks() })
  afterEach(() => cleanup())

  it('shows the real offline campaign entry and fixture scale', () => {
    render(<ReviewDemoApp />)
    expect(screen.getByText('10', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText('20', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建离线复习活动' })).toBeEnabled()
  })

  it('never renders the accepted answer before submission', async () => {
    localStorage.setItem(REVIEW_CAMPAIGN_STORAGE_KEY, activeCampaign.campaign_id)
    vi.stubGlobal('fetch', vi.fn(() => response(activeCampaign)))
    render(<ReviewDemoApp />)
    expect(await screen.findByText('提交前安全题目')).toBeInTheDocument()
    expect(screen.queryByText('秘密标准答案')).not.toBeInTheDocument()
    expect(screen.getByLabelText('用自己的话回答')).toBeInTheDocument()
  })

  it('restores a closed-page campaign and reports the recovery', async () => {
    localStorage.setItem(REVIEW_CAMPAIGN_STORAGE_KEY, activeCampaign.campaign_id)
    vi.stubGlobal('fetch', vi.fn(() => response(activeCampaign)))
    render(<ReviewDemoApp />)
    expect(await screen.findByText('已从关闭页面前恢复')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining(`/campaigns/${activeCampaign.campaign_id}`), expect.anything())
  })

  it('renders weak rolling as an intentional confirmation', async () => {
    const repeat = { ...activeCampaign, question: { ...activeCampaign.question!, occurrence_number: 2, weak_repeat: true, repeat_message: '这个知识点刚才掌握不稳，再确认一次。' }, metrics: { ...activeCampaign.metrics, weak_count: 1, remaining_weak_items: 1 } }
    localStorage.setItem(REVIEW_CAMPAIGN_STORAGE_KEY, repeat.campaign_id)
    vi.stubGlobal('fetch', vi.fn(() => response(repeat)))
    render(<ReviewDemoApp />)
    expect(await screen.findByText(/这个知识点刚才掌握不稳，再确认一次/)).toBeInTheDocument()
    expect(screen.queryByText(/系统错误重复题/)).not.toBeInTheDocument()
  })

  it('shows outcome language and reference answer only after submit', async () => {
    const submitted: ReviewCampaignView = {
      ...activeCampaign,
      question: { ...activeCampaign.question!, item_id: 'q2', prompt: '下一题' },
      metrics: { ...activeCampaign.metrics, retrieval_score: 1, evaluated_items: 1, pass_status: 'pending' },
      last_result: { item_id: 'q1', concept_id: 'c1', concept_label: '主动回忆', outcome: 'recalled', label: '答对', tone: 'success', retrieval_score: 1, feedback: '独立回忆成功', expected_answer: '秘密标准答案', explanation: '提交后解释', provider_calls: 0 },
    }
    localStorage.setItem(REVIEW_CAMPAIGN_STORAGE_KEY, activeCampaign.campaign_id)
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response(activeCampaign))
      .mockImplementationOnce(() => response(submitted))
    vi.stubGlobal('fetch', fetchMock)
    render(<ReviewDemoApp />)
    fireEvent.change(await screen.findByLabelText('用自己的话回答'), { target: { value: '我的回答' } })
    fireEvent.click(screen.getByRole('button', { name: '提交回答' }))
    expect(await screen.findByText('答对')).toBeInTheDocument()
    expect(screen.getByText('秘密标准答案')).toBeInTheDocument()
    expect(screen.getByText('Provider calls').parentElement).toHaveTextContent('0')
  })

  it('requests hint locally and displays it without an answer field', async () => {
    const hinted = { ...activeCampaign, question: { ...activeCampaign.question!, active_hint: '先从记忆内部找线索。' }, hint: { hint_id: 'h1', text: '先从记忆内部找线索。', source: 'local_demo', provider_calls: 0 } }
    localStorage.setItem(REVIEW_CAMPAIGN_STORAGE_KEY, activeCampaign.campaign_id)
    const fetchMock = vi.fn().mockImplementationOnce(() => response(activeCampaign)).mockImplementationOnce(() => response(hinted))
    vi.stubGlobal('fetch', fetchMock)
    render(<ReviewDemoApp />)
    fireEvent.click(await screen.findByRole('button', { name: '给我一个提示' }))
    expect(await screen.findByText('先从记忆内部找线索。')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls[1][0]).toContain('/hints'))
  })
})
