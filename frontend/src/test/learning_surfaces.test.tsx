import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { KnowledgeMapView } from '../features/knowledge/KnowledgeMapView'
import type { KnowledgeMapSnapshot } from '../features/knowledge/api'
import { LearningFlow } from '../features/learning/LearningFlow'
import { PlanningView } from '../features/planning/PlanningView'
import type { PlanningSnapshot } from '../features/planning/api'
import { TutorView } from '../features/tutor/TutorView'
import type { TutorSession } from '../features/tutor/api'

const map: KnowledgeMapSnapshot = {
  map_id: 'map-1',
  workspace_id: 'ws-1',
  revision: 2,
  generation_mode: 'deterministic_fallback',
  provider_used: false,
  nodes: [{
    node_id: 'node-graph',
    label: '图模型',
    summary: '顶点与边组成图。',
    source_refs: [{ source_id: 'source-1', chunk_id: 'chunk-1', heading: '第一章', page_start: 3, quote: '图由顶点与边组成。' }],
  }, {
    node_id: 'node-bfs',
    label: '广度优先搜索',
    source_refs: [{ source_id: 'source-1', chunk_id: 'chunk-2', heading: '第二章', page_start: 4, quote: 'BFS 按层遍历。' }],
  }],
  edges: [{ edge_id: 'edge-1', source_node_id: 'node-graph', target_node_id: 'node-bfs', relation: 'prerequisite' }],
  quality: { node_count: 2, edge_count: 1, sourced_node_ratio: 1, chunk_coverage_ratio: 1, orphan_count: 0, invalid_prerequisite_count: 0 },
}

const planning: PlanningSnapshot = {
  plan: {
    id: 'plan-1',
    version: 2,
    title: '算法路线',
    status: 'active',
    capacity_status: 'insufficient',
    scheduled_minutes: 120,
    unscheduled_minutes: 30,
    phase_policy: { first_pass_ratio: 0.4, review_ratio: 0.6 },
    diff: null,
    study_days: [
      { id: 'day-1', date: '2030-01-01', phase: 'first_pass', tasks: [{ id: 'task-1', title: '图基础', task_type: 'lesson', estimated_minutes: 30 }] },
      { id: 'day-2', date: '2030-01-02', phase: 'review', tasks: [{ id: 'task-2', title: '复习图', task_type: 'review', estimated_minutes: 20, locked: true, lock_reason: '先完成图基础' }] },
    ],
  },
  today: { date: '2030-01-01', day: { id: 'day-1', date: '2030-01-01', tasks: [{ id: 'task-1', title: '图基础', task_type: 'lesson', estimated_minutes: 30 }] } },
  calendar: [
    { id: 'day-1', date: '2030-01-01', phase: 'first_pass', tasks: [{ id: 'task-1', title: '图基础', task_type: 'lesson', estimated_minutes: 30 }] },
    { id: 'day-2', date: '2030-01-02', phase: 'review', tasks: [{ id: 'task-2', title: '复习图', task_type: 'review', estimated_minutes: 20, locked: true, lock_reason: '先完成图基础' }] },
  ],
  progress: { completed_tasks: 0, total_tasks: 2 },
}

const tutorSession: TutorSession = {
  run_id: 'run-1',
  workspace_id: 'ws-1',
  task_id: 'task-1',
  phase: 'guided_practice',
  status: 'active',
  revision: 3,
  state: { student_state: { mastery: 0.6, confidence: 0.5, retrievability: 0.4, hint_dependence: 0.2, transfer_ability: 0.3, learning_velocity: 0.7, misconceptions: ['把队列当成栈'] } },
  messages: [{ id: 'm1', role: 'tutor', content: '请先说明队列顺序。', grounding: { status: 'grounded', citations: [{ chunk_id: 'c1', source_id: 's1', quote: 'FIFO' }] } }],
}

afterEach(cleanup)

describe('learning feature surfaces', () => {
  it('renders knowledge nodes, relations, sources and quality, then regenerates', async () => {
    const regenerated = { ...map, revision: 3 }
    const api = { get: vi.fn(), regenerate: vi.fn().mockResolvedValue(regenerated) }
    render(<KnowledgeMapView workspaceId="ws-1" api={api as any} initialMap={map} />)

    expect(screen.getAllByText('图模型')).toHaveLength(2)
    expect(screen.getByText('来源覆盖').parentElement).toHaveTextContent('100%')
    expect(screen.getByText(/prerequisite/)).toBeInTheDocument()
    fireEvent.click(screen.getAllByText(/查看来源/)[0])
    expect(screen.getByText('图由顶点与边组成。')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }))
    await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith('ws-1'))
    expect(screen.getByText('版本 3')).toBeInTheDocument()
  })

  it('shows real planning policy, capacity, locks and diff placeholder', () => {
    render(<PlanningView workspaceId="ws-1" initialSnapshot={planning} />)

    expect(screen.getByText('40%')).toBeInTheDocument()
    expect(screen.getByText('60%')).toBeInTheDocument()
    expect(screen.getByText('30 分钟')).toBeInTheDocument()
    expect(screen.getByText(/已锁定：先完成图基础/)).toBeInTheDocument()
    expect(screen.getByText(/尚无调整记录/)).toBeInTheDocument()
  })

  it('exposes structured teacher actions, student dimensions and no-KKS fallback', async () => {
    const closed = { ...tutorSession, revision: 4, last_teacher_action: 'close_day' as const }
    const api = { start: vi.fn(), act: vi.fn().mockResolvedValue(closed) }
    const onDayClosed = vi.fn()
    render(<TutorView workspaceId="ws-1" task={{ id: 'task-1', title: '图基础' }} api={api as any} initialSession={tutorSession} onDayClosed={onDayClosed} />)

    expect(screen.getByText('3D 教师不可用时仍可完整学习')).toBeInTheDocument()
    expect(screen.getByText('掌握度').parentElement).toHaveTextContent('60%')
    expect(screen.getByText('提示依赖').parentElement).toHaveTextContent('20%')
    expect(screen.getByRole('button', { name: '生活类比' })).toBeInTheDocument()
    expect(screen.getByText('把队列当成栈')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '结束今天' }))
    await waitFor(() => expect(api.act).toHaveBeenCalledWith(tutorSession, 'close_day'))
    expect(onDayClosed).toHaveBeenCalledWith(closed)
  })

  it('moves through import, map, plan, today and tutor with real injected contracts', async () => {
    const flowApi = { sources: vi.fn().mockResolvedValue([{ id: 'source-1', title: '算法资料', status: 'ready', chunk_count: 2 }]) }
    const knowledge = { get: vi.fn(), regenerate: vi.fn() }
    const planApi = { load: vi.fn(), activate: vi.fn(), advance: vi.fn(), replan: vi.fn() }
    const tutor = { start: vi.fn(), act: vi.fn() }
    render(<LearningFlow
      workspaceId="ws-1"
      api={flowApi}
      knowledgeApi={knowledge as any}
      planningApi={planApi as any}
      tutorApi={tutor as any}
      initialMap={map}
      initialPlanning={planning}
      initialTutorSession={tutorSession}
    />)

    await waitFor(() => expect(screen.getByText('算法资料')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '继续生成知识地图' }))
    expect(screen.getByRole('heading', { name: '知识地图' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '继续制定计划' }))
    expect(screen.getByRole('heading', { name: '算法路线' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '查看今日任务' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '查看今日任务' }))
    const today = screen.getByRole('region', { name: '今日任务' })
    fireEvent.click(within(today).getByRole('button', { name: '开始教学' }))
    expect(screen.getByRole('heading', { name: 'AI 教师' })).toBeInTheDocument()
  })
})
