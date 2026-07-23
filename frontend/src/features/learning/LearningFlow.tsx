import { useEffect, useMemo, useState } from 'react'

import { KnowledgeMapView } from '../knowledge/KnowledgeMapView'
import type { KnowledgeApi, KnowledgeMapSnapshot } from '../knowledge/api'
import { PlanningView } from '../planning/PlanningView'
import type { PlanningApi, PlanningDay, PlanningSnapshot, PlanningTask } from '../planning/api'
import { TutorView } from '../tutor/TutorView'
import type { TutorApi, TutorSession } from '../tutor/api'
import {
  type ImportReceipt,
  type ImportedSource,
  type LearningFlowApi,
  learningFlowApi,
} from './api'

type FlowStage = 'import' | 'knowledge' | 'planning' | 'today' | 'tutor'

type Props = {
  workspaceId: string
  importReceipt?: ImportReceipt | null
  api?: LearningFlowApi
  knowledgeApi?: KnowledgeApi
  planningApi?: PlanningApi
  tutorApi?: TutorApi
  initialMap?: KnowledgeMapSnapshot | null
  initialPlanning?: PlanningSnapshot | null
  initialTutorSession?: TutorSession | null
  onRequestImport?: () => void
  onDayClosed?: (session: TutorSession) => void
}

const STAGES: Array<[FlowStage, string]> = [
  ['import', '导入结果'],
  ['knowledge', '知识地图'],
  ['planning', '长期计划'],
  ['today', '今日任务'],
  ['tutor', 'AI 教学'],
]

function sourceFromReceipt(item: NonNullable<ImportReceipt['imported']>[number]): ImportedSource | null {
  if ('id' in item && 'title' in item) return item
  return item.source || null
}

function ImportResult({
  sources,
  receipt,
  loading,
  error,
  onRetry,
  onRequestImport,
  onContinue,
}: {
  sources: ImportedSource[]
  receipt?: ImportReceipt | null
  loading: boolean
  error: string
  onRetry: () => void
  onRequestImport?: () => void
  onContinue: () => void
}) {
  const receiptSources = (receipt?.imported || []).map(sourceFromReceipt).filter(Boolean) as ImportedSource[]
  const visibleSources = receiptSources.length ? receiptSources : sources
  return <section aria-label="资料导入结果">
    <header><span>IMPORT RECEIPT</span><h2>资料导入结果</h2><p>先确认可学习内容与失败项，再进入知识地图。</p></header>
    {loading && <p role="status">正在核对已导入资料…</p>}
    {error && <div role="alert"><b>{error}</b><p>检查后端后重试；已显示的回执不会被清空。</p><button onClick={onRetry}>重试</button></div>}
    <dl>
      <div><dt>成功</dt><dd>{visibleSources.length}</dd></div>
      <div><dt>失败</dt><dd>{receipt?.failed?.length || 0}</dd></div>
      <div><dt>重复</dt><dd>{receipt?.duplicates?.length || 0}</dd></div>
      <div><dt>忽略</dt><dd>{receipt?.ignored?.length || 0}</dd></div>
    </dl>
    {visibleSources.length > 0
      ? <ul>{visibleSources.map(source => <li key={source.id}>
        <b>{source.title}</b>
        <span>{source.status || 'ready'} · {source.chunk_count ?? '待统计'} 个资料块</span>
        {source.parse_error && <p>{source.parse_error}</p>}
      </li>)}</ul>
      : !loading && <div>
        <p>还没有可学习资料。知识地图不会凭空生成。</p>
        {onRequestImport && <button type="button" onClick={onRequestImport}>返回导入资料</button>}
      </div>}
    {(receipt?.failed || []).length > 0 && <section aria-label="导入失败项">
      <h3>需要处理</h3>
      <ul>{receipt?.failed?.map((item, index) => <li key={`${item.relative_path || 'failed'}-${index}`}>
        <b>{item.relative_path || '未命名文件'}</b><span>{item.error || '转换失败，请查看后端回执'}</span>
      </li>)}</ul>
    </section>}
    <button type="button" onClick={onContinue} disabled={visibleSources.length === 0}>继续生成知识地图</button>
  </section>
}

function TodayView({
  day,
  onStart,
  onBack,
}: {
  day: PlanningDay | null
  onStart: (task: PlanningTask) => void
  onBack: () => void
}) {
  const actionable = day?.tasks.find(task => !task.locked && task.status !== 'completed') || null
  return <section aria-label="今日任务">
    <header><span>TODAY</span><h2>今日任务</h2><p>{day?.date || '尚未安排日期'}</p></header>
    {!day && <div role="alert"><b>今天还没有可执行任务</b><p>返回计划检查容量、截止日期与锁定原因。</p></div>}
    {day && <ul>{day.tasks.map(task => <li key={task.id}>
      <b>{task.title}</b>
      <span>{task.estimated_minutes} 分钟 · {task.status || 'not_started'}</span>
      {task.locked && <p>已锁定：{task.lock_reason || '前置任务尚未完成'}</p>}
      {!task.locked && task.status !== 'completed' && <button type="button" onClick={() => onStart(task)}>开始教学</button>}
    </li>)}</ul>}
    {!actionable && day && <p>今日任务已完成或仍处于锁定状态。</p>}
    <button type="button" onClick={onBack}>返回计划</button>
  </section>
}

export function LearningFlow({
  workspaceId,
  importReceipt = null,
  api = learningFlowApi,
  knowledgeApi,
  planningApi,
  tutorApi,
  initialMap = null,
  initialPlanning = null,
  initialTutorSession = null,
  onRequestImport,
  onDayClosed,
}: Props) {
  const [stage, setStage] = useState<FlowStage>('import')
  const [sources, setSources] = useState<ImportedSource[]>([])
  const [sourceLoading, setSourceLoading] = useState(true)
  const [sourceError, setSourceError] = useState('')
  const [map, setMap] = useState<KnowledgeMapSnapshot | null>(initialMap)
  const [today, setToday] = useState<PlanningDay | null>(initialPlanning?.today?.day || null)
  const [task, setTask] = useState<PlanningTask | null>(null)
  const [session, setSession] = useState<TutorSession | null>(initialTutorSession)

  async function loadSources(signal?: AbortSignal) {
    setSourceLoading(true)
    setSourceError('')
    try {
      setSources(await api.sources(workspaceId, signal))
    } catch (value) {
      if ((value as Error)?.name !== 'AbortError') setSourceError((value as Error)?.message || '资料读取失败')
    } finally {
      setSourceLoading(false)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void loadSources(controller.signal)
    return () => controller.abort()
  }, [workspaceId])

  const importedCount = useMemo(() => {
    const fromReceipt = (importReceipt?.imported || []).map(sourceFromReceipt).filter(Boolean).length
    return Math.max(fromReceipt, sources.length)
  }, [importReceipt, sources])

  const accessible = useMemo<Record<FlowStage, boolean>>(() => ({
    import: true,
    knowledge: importedCount > 0,
    planning: Boolean(map),
    today: Boolean(today),
    tutor: Boolean(task),
  }), [importedCount, map, today, task])

  function startTutor(nextTask: PlanningTask) {
    setTask(nextTask)
    setSession(initialTutorSession?.task_id === nextTask.id ? initialTutorSession : null)
    setStage('tutor')
  }

  return <section aria-label="学习主流程">
    <nav aria-label="学习流程阶段">
      <ol>{STAGES.map(([id, label], index) => <li key={id}>
        <button type="button" disabled={!accessible[id]} aria-current={stage === id ? 'step' : undefined} onClick={() => setStage(id)}>
          <span>{index + 1}</span>{label}
        </button>
      </li>)}</ol>
    </nav>

    {stage === 'import' && <ImportResult
      sources={sources}
      receipt={importReceipt}
      loading={sourceLoading}
      error={sourceError}
      onRetry={() => void loadSources()}
      onRequestImport={onRequestImport}
      onContinue={() => setStage('knowledge')}
    />}

    {stage === 'knowledge' && <div>
      <KnowledgeMapView
        workspaceId={workspaceId}
        api={knowledgeApi}
        initialMap={map}
        onMapReady={setMap}
      />
      <button type="button" disabled={!map} onClick={() => setStage('planning')}>继续制定计划</button>
    </div>}

    {stage === 'planning' && <div>
      <PlanningView
        workspaceId={workspaceId}
        api={planningApi}
        initialSnapshot={initialPlanning}
        onTodayReady={setToday}
      />
      <button type="button" disabled={!today} onClick={() => setStage('today')}>查看今日任务</button>
    </div>}

    {stage === 'today' && <TodayView day={today} onStart={startTutor} onBack={() => setStage('planning')} />}

    {stage === 'tutor' && <TutorView
      workspaceId={workspaceId}
      task={task}
      api={tutorApi}
      initialSession={session}
      onSessionChange={setSession}
      onDayClosed={onDayClosed}
      onSelfStudy={setSession}
    />}
  </section>
}
