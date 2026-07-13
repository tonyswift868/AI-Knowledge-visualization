import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, BookOpen, RefreshCw, ShieldCheck } from 'lucide-react'
import { matchStoryCatalog, requestCuration, type StoryCatalogResult } from './api'

const fallbackProfile = (workspace: any, task?: any) => ({
  goal: workspace?.goal?.payload?.goal_statement || task?.title || '',
  task_title: task?.title || '',
  expected_output: workspace?.goal?.payload?.expected_output || '',
  learning_style: workspace?.goal?.payload?.preferred_learning_style || 'mixed',
})

function profileLabel(value: unknown) {
  const labels: Record<string, string> = { coding_practice: '编程练习', practical_project: '实践项目', case_analysis: '案例分析', language_acquisition: '语言学习', concept_course: '概念课程', long_form_creation: '长篇创作', memory_drill: '记忆训练', exam_cram: '考试冲刺', mixed: '综合学习', prerequisite_graph: '前置知识图', evidence_network: '证据网络', repeated_patterns: '重复模式', cumulative_project: '累积项目', independent_units: '独立单元', strong: '强耦合', weak: '弱耦合', parallel: '并行融合' }
  return labels[String(value)] || String(value || '未说明')
}

function CatalogCard({ item, pending, workspace, profile, onSelect, onMessage }: { item: StoryCatalogResult; pending: boolean; workspace: any; profile: Record<string, unknown>; onSelect: (storyPackId: string) => void; onMessage: (message: string) => void }) {
  const title = item.title_zh || item.title || item.catalog_id
  const duration = item.duration_days?.length ? `${item.duration_days.join('–')} 天` : '周期待策展'
  return <article className={`catalog-card ${item.playable ? 'ready' : ''} ${pending ? 'pack-required' : ''}`} data-testid={`catalog-card-${item.catalog_id}`}>
    <div className="catalog-card-top"><span className="catalog-tier">{item.record_tier === 'curated_dna' ? 'CURATED DNA' : '候选目录'}</span><span className={`pack-status ${item.playable ? 'ready' : ''}`}>{item.playable ? 'PACK READY' : 'PACK_REQUIRED'}</span></div>
    <h2>{title}</h2>
    <div className="catalog-score"><b>{item.match_score}</b><span>匹配分</span></div>
    <div className="catalog-tags">{(item.tags || item.narrative_signals || []).slice(0, 4).map((tag) => <span key={tag}>{tag}</span>)}</div>
    <ul className="catalog-reasons">{(item.match_reasons || []).slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}</ul>
    <div className="catalog-fit-grid"><span>任务契合 <b>{Math.round(item.task_shape_fit * 100)}%</b></span><span>周期契合 <b>{Math.round(item.duration_fit * 100)}%</b></span><span>融合契合 <b>{Math.round(item.coupling_fit * 100)}%</b></span></div>
    <div className="catalog-meta"><span>推荐融合：{profileLabel(profile.coupling_mode)} · {item.interaction_style}</span><span>建议周期：{duration}</span></div>
    {pending && <p className="catalog-blocked-reason">当前只有叙事元数据，尚未安装可执行 Story Pack，因此不能启动 Campaign。</p>}
    <div className="catalog-card-actions">{item.playable ? <button className="primary" onClick={() => onSelect(item.story_pack_id || item.catalog_id.replace('builtin:', ''))}><BookOpen size={15} />选择并启动</button> : <button className="secondary" onClick={async () => { try { const result = await requestCuration(item.catalog_id, workspace.id, profile); onMessage(`已创建策展请求：${result.request_id}`) } catch (error: any) { onMessage(error.message) } }}>提交策展请求</button>}<ArrowRight size={16} /></div>
  </article>
}

export function StoryCatalogView({ workspace, task, onSelect }: { workspace: any; task?: any; onSelect: (storyPackId: string) => void }) {
  const [results, setResults] = useState<StoryCatalogResult[]>([])
  const [profile, setProfile] = useState<Record<string, any>>(fallbackProfile(workspace, task))
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [profileError, setProfileError] = useState('')

  async function load() {
    setLoading(true); setMessage(''); setProfileError('')
    try {
      let currentProfile = fallbackProfile(workspace, task)
      if (workspace?.id) {
        const profileResponse = await fetch(`/api/workspaces/${workspace.id}/task-profile`)
        const profileData = await profileResponse.json()
        if (profileResponse.ok) {
          currentProfile = profileData
          setProfile(profileData)
        }
        else setProfileError('当前任务画像暂不可用，将使用目标字段重新匹配。')
      }
      const match = await matchStoryCatalog(currentProfile, workspace?.id)
      setProfile((match.task_profile || currentProfile) as Record<string, any>); setResults(match.results)
    } catch (error: any) { setMessage(error.message) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [workspace?.id, task?.id])
  const playable = useMemo(() => results.filter((item) => item.playable), [results])
  const pending = useMemo(() => results.filter((item) => !item.playable), [results])
  const structures = Array.from(new Set(results.map((item) => item.narrative_structure).filter(Boolean))).slice(0, 3)
  return <div className="catalog-page">
    <div className="page-title"><div><div className="eyebrow">STORY SELECT · OFFLINE CATALOG</div><h1>选择你的学习故事</h1><p>先根据当前资料和任务画像匹配，再决定立即进入或提交策展请求。</p></div><button className="secondary" onClick={load} disabled={loading}><RefreshCw size={15} />{loading ? '匹配中…' : '重新匹配'}</button></div>
    <div className="catalog-safety"><ShieldCheck size={17} /><div><b>本地目录 · 不联网</b><span>已扫描目录后按任务画像评分，默认展示 3 个候选；没有 Story Pack 的候选会保留并明确标记为 PACK_REQUIRED。</span></div></div>
    <section className="task-profile-summary" data-testid="task-profile-summary"><div><b>当前任务画像</b><span>推荐由当前 Workspace 资料、目标和期限共同生成</span></div><p>任务类型：{profileLabel(profile.task_shape)}　知识结构：{profileLabel(profile.knowledge_structure)}　复习需求：{profileLabel(profile.review_need)}</p><p>融合建议：{profileLabel(profile.coupling_mode || profile.coupling_recommendation?.mode)}　推荐剧情结构：{structures.join(' / ') || '等待匹配'}</p></section>
    {profileError && <div className="narrative-error">{profileError}</div>}
    {message && <div className="narrative-error" role="status">{message}</div>}
    <section className="catalog-section" data-testid="playable-section"><div className="catalog-section-heading"><div><div className="eyebrow">AVAILABLE NOW</div><h2>可立即游玩</h2></div><span>{playable.length} 个</span></div>{playable.length ? <div className="catalog-grid">{playable.map((item) => <CatalogCard key={item.catalog_id} item={item} pending={false} workspace={workspace} profile={profile} onSelect={onSelect} onMessage={setMessage} />)}</div> : <div className="catalog-empty">当前没有可立即启动的 Story Pack。</div>}</section>
    <section className="catalog-section" data-testid="required-section"><div className="catalog-section-heading"><div><div className="eyebrow">CURATION QUEUE</div><h2>高度匹配，等待 Story Pack</h2></div><span>{pending.length} 个</span></div>{pending.length ? <div className="catalog-grid">{pending.map((item) => <CatalogCard key={item.catalog_id} item={item} pending workspace={workspace} profile={profile} onSelect={onSelect} onMessage={setMessage} />)}</div> : <div className="catalog-empty">没有待策展候选。</div>}</section>
    <div className="catalog-total">总计 {results.length} 个候选 · PACK_REQUIRED 只可提交策展请求，不能启动战役</div>
  </div>
}
