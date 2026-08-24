import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { CitationCard } from '../components/CitationCard'
import { GroundingBadge } from '../components/GroundingBadge'
import { ConflictPanel } from '../components/ConflictPanel'
import { Create, SourceImportPanel } from '../App'
import { apiRequest, uploadFiles } from '../api/client'
import { useMentorStore } from '../stores/useMentorStore'
import { useNarrativeStore } from '../stores/useNarrativeStore'

const citation = { source_id: 's1', source_title: 'Source title', chunk_id: 'c1', page_start: 3, page_end: 3, heading: 'Chapter', quote: 'Source quote', trust_level: 'reference' as const }

describe('grounding components', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear() })
  afterEach(() => cleanup())
  it('renders grounded badge', () => { render(<GroundingBadge status="grounded" />); expect(screen.getByText('grounded')).toBeInTheDocument() })
  it('renders citation source metadata collapsed', () => { render(<CitationCard citation={citation} />); expect(screen.getByText('Source title')).toBeInTheDocument(); expect(screen.queryByText('Source quote')).not.toBeInTheDocument() })
  it('expands citation quote', () => { render(<CitationCard citation={citation} />); fireEvent.click(screen.getByRole('button', { name: /Source title/ })); expect(screen.getByText('Source quote')).toBeVisible() })
  it('renders no conflict panel for empty input', () => { const { container } = render(<ConflictPanel conflicts={[]} />); expect(container.firstChild).toBeNull() })
  it('renders both conflict options with evidence metadata', () => { render(<ConflictPanel conflicts={[{ id: 'x', topic: 'topic', evidence_a: 'A', evidence_b: 'B', source_a_id: 'a', source_b_id: 'b', source_a_title: 'Source A', source_b_title: 'Source B', chunk_a_id: 'ca', chunk_b_id: 'cb', page_a: 2, page_b: 4 }]} />); expect(screen.getAllByRole('button')).toHaveLength(2); expect(screen.getByText(/Source A · chunk ca · page 2/)).toBeInTheDocument(); expect(screen.getByText(/Source B · chunk cb · page 4/)).toBeInTheDocument() })
  it('fires conflict resolution callback', () => { const onResolve = vi.fn(); render(<ConflictPanel conflicts={[{ id: 'x', topic: 'topic', evidence_a: 'A', evidence_b: 'B', source_a_id: 'a', source_b_id: 'b' }]} onResolve={onResolve} />); fireEvent.click(screen.getAllByRole('button')[0]); expect(onResolve).toHaveBeenCalledWith('x', 'a') })
})

describe('api and state', () => {
  beforeEach(() => vi.restoreAllMocks())
  afterEach(() => cleanup())
  it('parses successful API response', async () => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))); await expect(apiRequest('/health')).resolves.toEqual({ ok: true }) })
  it('surfaces API error message', async () => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { message: 'failure' } }), { status: 400 }))); await expect(apiRequest('/bad')).rejects.toThrow('failure') })
  it('uploads every selected file as multipart', async () => { const fetchMock = vi.fn().mockResolvedValue(new Response('{}', { status: 200 })); vi.stubGlobal('fetch', fetchMock); await uploadFiles('/workspaces/w/sources/upload', [new File(['a'], 'a.md'), new File(['b'], 'b.md')]); expect(fetchMock).toHaveBeenCalledTimes(2); expect(fetchMock.mock.calls[0][1].body).toBeInstanceOf(FormData) })
  it('stores selected workspace', () => { useMentorStore.getState().setWorkspace('w1'); expect(useMentorStore.getState().activeWorkspaceId).toBe('w1') })
  it('stores provider selection', () => { useMentorStore.getState().setProvider('gemini'); expect(useMentorStore.getState().provider).toBe('gemini') })
  it('restores the narrative scene, gate and revision in one store update', () => { useNarrativeStore.getState().setCampaign({ campaign_id: 'c1', revision: 4, current_scene: { scene_id: 's1', choices: [{ choice_id: 'a' }] }, state: { pending_learning_gate: { status: 'pending' } } }); expect(useNarrativeStore.getState().currentScene.scene_id).toBe('s1'); expect(useNarrativeStore.getState().availableChoices).toHaveLength(1); expect(useNarrativeStore.getState().learningGate.status).toBe('pending'); expect(useNarrativeStore.getState().revision).toBe(4) })
  it('renders real file picker', () => { render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); expect(screen.getByTestId('file-input')).toHaveAttribute('type', 'file') })
  it('shows selected file count', () => { const { container } = render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['x'], 'x.md')] } }); expect(container.textContent).toContain('1') })
  it('enables URL import only after a URL is entered', () => { const { container } = render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); const url = screen.getByLabelText(/URL/); const buttons = container.querySelectorAll('button'); expect(buttons[1]).toBeDisabled(); fireEvent.change(url, { target: { value: 'https://example.com/article' } }); expect(buttons[1]).toBeEnabled() })
  it('accepts dropped files and exposes retryable upload control', () => { const { container } = render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); fireEvent.drop(container.querySelector('.source-import-panel')!, { dataTransfer: { files: [new File(['x'], 'drop.md')] } }); expect(container.textContent).toContain('1'); expect(container.querySelectorAll('button')[0]).toBeEnabled() })
  it('accepts a drop on the visible dropzone, not only the control row', () => { const { container } = render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); fireEvent.drop(container.querySelector('.dropzone')!, { dataTransfer: { files: [new File(['x'], 'drop.zip')] } }); expect(container.textContent).toContain('1 个文件已接收') })
  it('allows jumping to any saved stage and disables future stages', () => { const jumpToStep = vi.fn(); const form = { title: 'x', description: 'x', sourceText: 'source', goal_statement: 'goal statement', motivation: 'why', expected_output: 'output', success_criteria: ['done'], deadline: '2030-01-01', minutes_per_day: 30, days_per_week: 5, current_level: 'beginner', constraints: '', character_id: 'warm' }; render(<Create step={1} maxStep={3} jumpToStep={jumpToStep} setStep={vi.fn()} form={form} setField={vi.fn()} ws={{ id: 'w1' }} busy={false} create={vi.fn()} analyze={vi.fn()} assessment={null} generate={vi.fn()} />); const savedStage = screen.getByRole('button', { name: '4时间约束' }); expect(savedStage).toBeEnabled(); fireEvent.click(savedStage); expect(jumpToStep).toHaveBeenCalledWith(3); expect(screen.getByRole('button', { name: '5可行性' })).toBeDisabled() })
  it('saves a named preset without losing the current form', () => { const form = { title: 'LeetCode Java17', description: 'Top 150', sourceText: 'notes', goal_statement: '完成题目', motivation: '面试', expected_output: '解法资料', success_criteria: ['代码通过'], deadline: '2030-01-01', minutes_per_day: 30, days_per_week: 5, current_level: 'beginner', constraints: '', character_id: 'warm' }; render(<Create step={2} maxStep={3} jumpToStep={vi.fn()} setStep={vi.fn()} form={form} setForm={vi.fn()} setField={vi.fn()} ws={{ id: 'w1' }} busy={false} create={vi.fn()} analyze={vi.fn()} assessment={null} generate={vi.fn()} />); fireEvent.click(screen.getByRole('button', { name: '保存预设' })); expect(localStorage.getItem('mentorflow_create_presets_v1')).toContain('LeetCode Java17'); expect(screen.getByText(/已保存/)).toBeInTheDocument() })
  it('shows the system allocation after feasibility analysis', () => { const form = { title: 'x', description: 'x', sourceText: 'source', goal_statement: 'goal statement', motivation: 'why', expected_output: 'output', success_criteria: ['done'], deadline: '2030-01-01', minutes_per_day: 30, days_per_week: 5, current_level: 'beginner', constraints: '', character_id: 'warm' }; render(<Create step={4} maxStep={4} jumpToStep={vi.fn()} setStep={vi.fn()} form={form} setForm={vi.fn()} setField={vi.fn()} ws={{ id: 'w1' }} busy={false} create={vi.fn()} analyze={vi.fn()} assessment={{ recommended_minutes_per_day: 45, available_study_days: 20 }} generate={vi.fn()} />); expect(screen.getByText('45 分钟 / 天')).toBeInTheDocument(); expect(screen.getByText(/20% 计划损耗/)).toBeInTheDocument() })
  it('keeps the current allocation by default and does not rewrite the saved form', () => {
    const form = { title: 'x', description: 'x', sourceText: 'source', goal_statement: 'goal statement', motivation: 'why', expected_output: 'output', success_criteria: ['done'], deadline: '2030-01-01', minutes_per_day: 30, days_per_week: 5, current_level: 'beginner', constraints: '', character_id: 'warm' }
    const assessment = { recommended_minutes_per_day: 45, suggested_goal_patch: { minutes_per_day: 45 }, available_study_days: 20 }
    const setField = vi.fn()
    const setStep = vi.fn()
    const generate = vi.fn()
    const { rerender } = render(<Create step={4} maxStep={4} jumpToStep={vi.fn()} setStep={setStep} setField={setField} form={form} ws={{ id: 'w1' }} busy={false} create={vi.fn()} analyze={vi.fn()} assessment={assessment} generate={generate} />)
    expect(screen.getByRole('radio', { name: /保持当前投入/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /采用建议投入/ })).not.toBeChecked()
    expect(setField).not.toHaveBeenCalledWith('minutes_per_day', 45)
    fireEvent.click(screen.getByRole('button', { name: /确认分析并选择角色/ }))
    expect(setStep).toHaveBeenCalledTimes(1)
    expect(setStep).toHaveBeenCalledWith(5)
    rerender(<Create step={5} maxStep={4} jumpToStep={vi.fn()} setStep={setStep} setField={setField} form={form} ws={{ id: 'w1' }} busy={false} create={vi.fn()} analyze={vi.fn()} assessment={assessment} generate={generate} />)
    fireEvent.click(screen.getByRole('button', { name: /生成计划/ }))
    expect(generate).toHaveBeenCalledWith('keep_current')
  })
  it('sends the explicit adopt choice only after the learner selects it', () => {
    const form = { title: 'x', description: 'x', sourceText: 'source', goal_statement: 'goal statement', motivation: 'why', expected_output: 'output', success_criteria: ['done'], deadline: '2030-01-01', minutes_per_day: 30, days_per_week: 5, current_level: 'beginner', constraints: '', character_id: 'warm' }
    const assessment = { recommended_minutes_per_day: 45, suggested_goal_patch: { minutes_per_day: 45 }, available_study_days: 20 }
    const setStep = vi.fn()
    const generate = vi.fn()
    const { rerender } = render(<Create step={4} maxStep={4} jumpToStep={vi.fn()} setStep={setStep} setField={vi.fn()} form={form} ws={{ id: 'w1' }} busy={false} create={vi.fn()} analyze={vi.fn()} assessment={assessment} generate={generate} />)
    const adopt = screen.getByRole('radio', { name: /采用建议投入/ })
    fireEvent.click(adopt)
    expect(adopt).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: /确认分析并选择角色/ }))
    expect(setStep).toHaveBeenCalledTimes(1)
    expect(setStep).toHaveBeenCalledWith(5)
    rerender(<Create step={5} maxStep={4} jumpToStep={vi.fn()} setStep={setStep} setField={vi.fn()} form={form} ws={{ id: 'w1' }} busy={false} create={vi.fn()} analyze={vi.fn()} assessment={assessment} generate={generate} />)
    fireEvent.click(screen.getByRole('button', { name: /生成计划/ }))
    expect(generate).toHaveBeenCalledWith('increase_daily_minutes')
  })
})
