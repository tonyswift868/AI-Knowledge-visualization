import { useEffect, useMemo, useState } from 'react'

import {
  KnowledgeApiError,
  type KnowledgeApi,
  type KnowledgeMapQuality,
  type KnowledgeMapSnapshot,
  type KnowledgeNode,
  knowledgeApi,
} from './api'

type Props = {
  workspaceId: string
  api?: KnowledgeApi
  initialMap?: KnowledgeMapSnapshot | null
  onMapReady?: (snapshot: KnowledgeMapSnapshot) => void
}

function percent(value: number | undefined): string {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '待评估'
}

function metric(value: number | undefined): string {
  return typeof value === 'number' ? String(value) : '待评估'
}

function actionFor(error: unknown): string {
  if (error instanceof KnowledgeApiError) {
    if (error.status === 404 || error.code === 'KNOWLEDGE_MAP_NOT_FOUND') return '先导入至少一份资料，再生成知识地图。'
    if (error.code === 'NO_CONVERTED_SOURCE') return '资料尚未转换成功，请回到导入结果查看失败原因。'
    if (error.status >= 500) return '服务暂时不可用。旧地图不会被覆盖，请稍后重试。'
  }
  return '检查网络连接后重试；若问题持续，请保留错误码并查看导入回执。'
}

function QualityPanel({ quality }: { quality: KnowledgeMapQuality }) {
  const warnings = [
    (quality.orphan_count || 0) > 0 ? `${quality.orphan_count} 个孤立节点` : '',
    (quality.invalid_prerequisite_count || 0) > 0 ? `${quality.invalid_prerequisite_count} 条无效前置关系` : '',
    (quality.duplicate_node_ratio || 0) > 0 ? `重复率 ${percent(quality.duplicate_node_ratio)}` : '',
  ].filter(Boolean)
  return <section aria-label="知识地图质量">
    <h3>质量检查</h3>
    <dl>
      <div><dt>节点</dt><dd>{metric(quality.node_count)}</dd></div>
      <div><dt>关系</dt><dd>{metric(quality.edge_count)}</dd></div>
      <div><dt>来源覆盖</dt><dd>{percent(quality.sourced_node_ratio)}</dd></div>
      <div><dt>资料块覆盖</dt><dd>{percent(quality.chunk_coverage_ratio)}</dd></div>
    </dl>
    {warnings.length
      ? <ul>{warnings.map(item => <li key={item}>{item}</li>)}</ul>
      : <p>未发现已知结构问题。</p>}
  </section>
}

function SourceList({ node }: { node: KnowledgeNode }) {
  if (!node.source_refs?.length) return <p role="note">该节点缺少来源，不能作为教学事实使用。</p>
  return <details>
    <summary>查看来源（{node.source_refs.length}）</summary>
    <ul>
      {node.source_refs.map(reference => <li key={`${reference.source_id}:${reference.chunk_id}`}>
        <b>{reference.heading || reference.source_id}</b>
        <span>
          {reference.page_start
            ? `第 ${reference.page_start}${reference.page_end && reference.page_end !== reference.page_start ? `–${reference.page_end}` : ''} 页`
            : '无页码'}
        </span>
        <blockquote>{reference.quote}</blockquote>
      </li>)}
    </ul>
  </details>
}

export function KnowledgeMapView({ workspaceId, api = knowledgeApi, initialMap = null, onMapReady }: Props) {
  const [snapshot, setSnapshot] = useState<KnowledgeMapSnapshot | null>(initialMap)
  const [loading, setLoading] = useState(!initialMap)
  const [regenerating, setRegenerating] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function load(signal?: AbortSignal) {
    setLoading(true)
    setError(null)
    try {
      const next = await api.get(workspaceId, signal)
      setSnapshot(next)
      onMapReady?.(next)
    } catch (value) {
      if ((value as Error)?.name !== 'AbortError') setError(value)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (initialMap) {
      setSnapshot(initialMap)
      setLoading(false)
      return
    }
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [workspaceId, initialMap])

  const labels = useMemo(
    () => new Map((snapshot?.nodes || []).map(node => [node.node_id, node.label])),
    [snapshot],
  )

  async function regenerate() {
    setRegenerating(true)
    setError(null)
    try {
      const next = await api.regenerate(workspaceId)
      setSnapshot(next)
      onMapReady?.(next)
    } catch (value) {
      setError(value)
    } finally {
      setRegenerating(false)
    }
  }

  if (loading && !snapshot) return <section aria-label="知识地图"><p role="status">正在读取知识地图…</p></section>

  return <section aria-label="知识地图">
    <header>
      <div>
        <span>KNOWLEDGE MAP</span>
        <h2>知识地图</h2>
        <p>节点和关系必须来自已转换资料；模型失败时保留上一版。</p>
      </div>
      <button type="button" onClick={regenerate} disabled={regenerating}>
        {regenerating ? '正在重新生成…' : snapshot ? '重新生成' : '生成地图'}
      </button>
    </header>

    {Boolean(error) && <div role="alert">
      <b>{error instanceof Error ? error.message : '知识地图暂时不可用'}</b>
      <p>{actionFor(error)}</p>
      <button type="button" onClick={() => void load()}>重试读取</button>
    </div>}

    {!snapshot && !error && <p>当前还没有知识地图。</p>}

    {snapshot && <>
      <div>
        <span>版本 {snapshot.revision}</span>
        <span>{snapshot.generation_mode || '生成方式待确认'}</span>
        <span>{snapshot.provider_used === false ? '无需模型的确定性回退' : '结构化模型辅助'}</span>
      </div>

      <QualityPanel quality={snapshot.quality || {}} />

      {(snapshot.warnings || []).length > 0 && <aside role="note">
        <b>生成警告</b>
        <ul>{snapshot.warnings?.map(item => <li key={item}>{item}</li>)}</ul>
      </aside>}

      <div>
        <section aria-label="知识节点">
          <h3>知识节点</h3>
          {snapshot.nodes.length === 0
            ? <p>没有可用节点，请检查资料转换结果。</p>
            : snapshot.nodes.map(node => <article key={node.node_id}>
              <div>
                <span>{node.kind || 'concept'}</span>
                <span>{node.status || 'active'}</span>
                {node.revision && <span>r{node.revision}</span>}
              </div>
              <h4>{node.label}</h4>
              {node.summary && <p>{node.summary}</p>}
              <SourceList node={node} />
            </article>)}
        </section>

        <section aria-label="知识关系">
          <h3>关系</h3>
          {snapshot.edges.length === 0
            ? <p>当前节点之间尚无可验证关系。</p>
            : <ul>{snapshot.edges.map(edge => <li key={edge.edge_id}>
              <b>{labels.get(edge.source_node_id) || edge.source_node_id}</b>
              <span> —{edge.relation}→ </span>
              <b>{labels.get(edge.target_node_id) || edge.target_node_id}</b>
            </li>)}</ul>}
        </section>
      </div>
    </>}
  </section>
}
