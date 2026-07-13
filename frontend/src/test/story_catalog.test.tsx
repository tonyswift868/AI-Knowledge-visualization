import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StoryCatalogView } from '../features/story-catalog/StoryCatalogView'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const result = (id: string, playable: boolean, title: string) => ({ catalog_id: id, title, record_tier: 'curated_dna', match_score: playable ? 88 : 96, match_reasons: ['资料适配理由', '建议周期 14–45 天', '弱耦合匹配'], task_shape_fit: .9, duration_fit: .8, coupling_fit: .9, narrative_structure: `${id}-structure`, relationship_engine: `${id}-relationship`, interaction_style: `${id}-interaction`, emotional_tone: `${id}-tone`, time_mechanic: `${id}-time`, tags: ['学习'], pack_status: playable ? 'ready' : 'PACK_REQUIRED', story_pack_status: playable ? 'ready' : 'PACK_REQUIRED', story_pack_id: playable ? 'reference_echo_before_rain' : null, playable, curation_status: playable ? 'curated' : 'curated_v1_merged', duration_days: [14, 45] })

function mockCatalogFetch() {
  const results = [result('builtin:reference_echo_before_rain', true, '雨停之前的回声'), result('curated:oneshot', false, 'OneShot'), result('curated:gnosia', false, 'GNOSIA')]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/task-profile')) return new Response(JSON.stringify({ domain: 'algorithms', task_shape: 'coding_practice', knowledge_structure: 'prerequisite_graph', review_need: 'high', coupling_recommendation: { mode: 'weak' } }), { status: 200 })
    if (url.endsWith('/story-catalog/match')) return new Response(JSON.stringify({ count: 3, results, task_profile: { domain: 'algorithms', task_shape: 'coding_practice', knowledge_structure: 'prerequisite_graph', review_need: 'high', coupling_mode: 'weak' } }), { status: 200 })
    if (url.includes('/curation-request')) return new Response(JSON.stringify({ request_id: 'curation_123', status: 'queued' }), { status: 200 })
    return new Response('{}', { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('story catalog candidate selection', () => {
  it('shows the task profile, three cards, and separate playable/required sections', async () => {
    mockCatalogFetch()
    render(<StoryCatalogView workspace={{ id: 'ws_1' }} onSelect={vi.fn()} />)
    await waitFor(() => expect(screen.getAllByTestId(/catalog-card-/)).toHaveLength(3))
    expect(screen.getByTestId('task-profile-summary')).toHaveTextContent('编程练习')
    expect(screen.getByTestId('playable-section')).toHaveTextContent('可立即游玩')
    expect(screen.getByTestId('required-section')).toHaveTextContent('高度匹配，等待 Story Pack')
    expect(screen.getAllByText('PACK_REQUIRED')).toHaveLength(2)
    expect(screen.getAllByText('当前只有叙事元数据，尚未安装可执行 Story Pack，因此不能启动 Campaign。')).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: '提交策展请求' })).toHaveLength(2)
    expect(screen.getByRole('button', { name: '选择并启动' })).toBeInTheDocument()
  })

  it('submits curation for PACK_REQUIRED and refreshes on rematch without starting a campaign', async () => {
    const fetchMock = mockCatalogFetch()
    const onSelect = vi.fn()
    render(<StoryCatalogView workspace={{ id: 'ws_1' }} onSelect={onSelect} />)
    await waitFor(() => expect(screen.getAllByTestId(/catalog-card-/)).toHaveLength(3))
    fireEvent.click(screen.getAllByRole('button', { name: '提交策展请求' })[0])
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('curation_123'))
    expect(onSelect).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '重新匹配' }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/story-catalog/match')).length).toBe(2))
  })
})
