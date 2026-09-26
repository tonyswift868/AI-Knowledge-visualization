import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const STEP_LABELS = ['需求对话', '资料选择', '目标与成果', '时间约束', '可行性', '教师风格', '确认计划']
const SEARCH_READY_FIELDS = ['goal_statement']
const CHARACTERS = [
  { id: 'warm', name: '温柔陪伴型', icon: '☁', desc: '温和鼓励，小步行动' },
  { id: 'researcher', name: '严谨研究员型', icon: '⌁', desc: '证据优先，边界清晰' },
  { id: 'socratic', name: '苏格拉底提问型', icon: '◌', desc: '通过追问自己想清楚' },
  { id: 'coach', name: '高压教练型', icon: '↗', desc: '短句直接，强调交付' },
]
const WORKFLOW_ROLES = [
  { id: 'analyst', name: 'A · 需求分析师', desc: '澄清目标、边界和资料依据' },
  { id: 'builder', name: 'B · 路线编排师', desc: '把资料和时间编排成课程' },
  { id: 'coach', name: 'C · 学习教练', desc: '把课程转成今天能开始的练习' },
]

function dateValue(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function defaultForm() {
  const start = new Date()
  const deadline = new Date(start)
  deadline.setDate(deadline.getDate() + 14)
  return {
    title: '', description: '', goal_statement: '', motivation: '', expected_output: '',
    success_criteria: [''], start_date: dateValue(start), deadline: dateValue(deadline),
    minutes_per_day: 60, preferred_session_minutes: 30, weekdays: [0, 1, 2, 3, 4, 5, 6], current_level: 'beginner',
    constraints: '', character_id: 'warm', workflow_role: 'analyst', sourceText: '', manual_tasks: '', material_ids: [], source_ids: [], conversation_history: [], conversation_confirmed_fields: [], conversation_pending_proposal: {},
  }
}

function normalizeForm(value) {
  const base = defaultForm()
  if (!value || typeof value !== 'object') return base
  return {
    ...base, ...value,
    success_criteria: Array.isArray(value.success_criteria) ? value.success_criteria.map(String) : base.success_criteria,
    weekdays: Array.isArray(value.weekdays) ? value.weekdays.map(Number).filter((day) => day >= 0 && day <= 6) : base.weekdays,
    material_ids: Array.isArray(value.material_ids) ? value.material_ids.map(String) : base.material_ids,
    source_ids: Array.isArray(value.source_ids) ? value.source_ids.map(String) : base.source_ids,
    conversation_history: Array.isArray(value.conversation_history) ? value.conversation_history
      .filter((message) => message && ['user', 'assistant'].includes(message.role) && typeof message.text === 'string')
      .map((message, index) => ({ id: `restored-${index}`, role: message.role, text: message.text })) : [],
    conversation_confirmed_fields: Array.isArray(value.conversation_confirmed_fields) ? [...new Set(value.conversation_confirmed_fields.map(String))] : [],
    conversation_pending_proposal: value.conversation_pending_proposal && typeof value.conversation_pending_proposal === 'object' ? value.conversation_pending_proposal : {},
    minutes_per_day: Number.isFinite(Number(value.minutes_per_day)) ? Number(value.minutes_per_day) : base.minutes_per_day,
    preferred_session_minutes: Number.isFinite(Number(value.preferred_session_minutes)) ? Number(value.preferred_session_minutes) : base.preferred_session_minutes,
  }
}

class BridgeError extends Error {
  constructor(message, code = 'PLANNING_BRIDGE_ERROR') {
    super(message)
    this.code = code
  }
}

class PlanningBridge {
  constructor() {
    this.pending = new Map()
    this.sequence = 0
    this.disposed = false
    this.hasTransport = Boolean(window.qt?.webChannelTransport && window.QWebChannel)
    this.ready = new Promise((resolve, reject) => {
      this.resolveReady = resolve
      this.rejectReady = reject
      if (!this.hasTransport) {
        reject(new BridgeError('规划服务暂未连接。', 'PLANNING_UNAVAILABLE'))
        return
      }
      try {
        new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
          const planning = channel?.objects?.planning
          if (!planning || typeof planning.request !== 'function' || typeof planning.result?.connect !== 'function') {
            reject(new BridgeError('规划服务接口暂不可用。', 'PLANNING_INTERFACE_INVALID'))
            return
          }
          this.planning = planning
          planning.result.connect((raw) => this.receive(raw))
          if (!this.disposed) resolve()
        })
      } catch (error) {
        reject(new BridgeError('规划服务接口暂不可用。', 'PLANNING_INTERFACE_INVALID'))
      }
    })
  }

  request(action, payload = {}) {
    if (this.disposed) return Promise.reject(new BridgeError('规划页面已关闭。', 'PLANNING_DISPOSED'))
    return this.ready.then(() => new Promise((resolve, reject) => {
      const id = `planning-${Date.now()}-${++this.sequence}`
      this.pending.set(id, { resolve, reject })
      try {
        this.planning.request(JSON.stringify({ id, action, payload }))
      } catch (error) {
        this.pending.delete(id)
        reject(new BridgeError('规划请求未能发出。', 'PLANNING_REQUEST_FAILED'))
      }
    }))
  }

  receive(raw) {
    let result
    try { result = typeof raw === 'string' ? JSON.parse(raw) : raw } catch (error) { return }
    if (!result || typeof result.id !== 'string') return
    const pending = this.pending.get(result.id)
    if (!pending) return
    this.pending.delete(result.id)
    if (result.ok) pending.resolve(result.data || {})
    else pending.reject(new BridgeError(result.error?.message || '规划操作未完成。', result.error?.code || 'PLANNING_OPERATION_FAILED'))
  }

  dispose() {
    this.disposed = true
    this.rejectReady?.(new BridgeError('规划页面已关闭。', 'PLANNING_DISPOSED'))
    for (const pending of this.pending.values()) pending.reject(new BridgeError('规划页面已关闭。', 'PLANNING_DISPOSED'))
    this.pending.clear()
  }
}

function Metric({ label, value, suffix = '' }) {
  const available = value !== null && value !== undefined && value !== ''
  return <div className="metric"><span>{label}</span><b>{available ? `${value}${suffix}` : '未提供'}</b></div>
}

function PresetBar({ name, setName, presets, onSave, onLoad, message, disabled }) {
  return <div className="preset-bar">
    <div className="preset-row">
      <span className="save-state">草稿自动保存</span>
      <input aria-label="预设名称" value={name} onChange={(event) => setName(event.target.value)} placeholder="预设名称" disabled={disabled} />
      <button className="secondary" onClick={onSave} disabled={disabled}>保存预设</button>
      <select aria-label="加载已保存预设" defaultValue="" onChange={(event) => { onLoad(event.target.value); event.currentTarget.value = '' }} disabled={disabled}>
        <option value="">加载预设</option>
        {presets.map((preset) => <option key={preset.name} value={preset.name}>{preset.name}</option>)}
      </select>
    </div>
    {message && <small className="preset-message" role="status">{message}</small>}
  </div>
}

function Materials({ materials, selected, onToggle }) {
  if (!materials.length) return <div className="empty-materials">当前没有可选择的资料。</div>
  return <div className="materials-list" aria-label="可选择资料">
    {materials.map((material) => {
      const available = material.available === true
      return <label className={`material-row ${available ? '' : 'unavailable'}`} key={String(material.id)}>
        <input type="checkbox" checked={selected.includes(String(material.id))} disabled={!available} onChange={() => onToggle(String(material.id))} />
        <span><b>{material.name || String(material.id)}</b><small>{material.status || (available ? '可用' : '不可用')}</small></span>
      </label>
    })}
  </div>
}

function ResearchCandidates({ candidates, selected, onToggle }) {
  if (!candidates.length) return <div className="empty-materials">还没有候选资料；先输入需求并搜索。</div>
  return <div className="research-candidates" aria-label="检索候选资料">
    {candidates.map((candidate) => <label className="research-candidate" key={candidate.url}>
      <input type="checkbox" checked={selected.includes(candidate.url)} onChange={() => onToggle(candidate.url)} />
      <span><b>{candidate.title || candidate.url}</b><a href={candidate.url} target="_blank" rel="noreferrer">{candidate.url}</a><small>{candidate.snippet || '搜索服务未提供摘要。'}</small></span>
    </label>)}
  </div>
}

function ResearchSources({ sources, selected, onToggle }) {
  if (!sources.length) return <div className="empty-materials">尚未读取网页资料。</div>
  return <div className="research-sources" aria-label="已读取网页资料">
    {sources.map((source) => <label className="research-source" key={source.id}>
      <input type="checkbox" checked={selected.includes(source.id)} onChange={() => onToggle(source.id)} />
      <span><b>{source.title || source.final_url}</b><a href={source.final_url} target="_blank" rel="noreferrer">{source.final_url}</a><small>{source.chunk_count || 0} 个资料段 · {source.truncated ? '已按容量保存摘录' : '已读取正文'}</small></span>
    </label>)}
  </div>
}

const LEVEL_LABELS = { beginner: '入门', intermediate: '有基础', advanced: '熟练' }
const CHARACTER_LABELS = { warm: '温柔陪伴型', researcher: '严谨研究员型', socratic: '苏格拉底提问型', coach: '高压教练型' }

function previewDetails(value) {
  const details = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const text = (field) => typeof details[field] === 'string' && details[field].trim() ? details[field].trim() : ''
  const learningGoal = text('learning_goal') || text('goal_statement') || (typeof value === 'string' ? value.trim() : '')
  return {
    learningGoal,
    description: text('description'),
    expectedOutput: text('expected_output'),
    currentLevel: LEVEL_LABELS[details.current_level] || '',
    character: CHARACTER_LABELS[details.character_id] || '',
    startDate: text('start_date'),
    deadline: text('deadline'),
  }
}

function PlanPreview({ plan, onSave, onCancel, saving, saved, materials = [], selectedMaterialIds = [] }) {
  if (!plan) return <div className="empty-state"><span className="empty-icon">◇</span><h2>计划预览将在这里出现</h2><p>生成或手动编排后，任务和日期会显示在这里。</p></div>
  const tasks = Array.isArray(plan.tasks) ? plan.tasks : []
  const sources = Array.isArray(plan.details?.sources) ? plan.details.sources : []
  const localSources = materials.filter((material) => selectedMaterialIds.includes(String(material.id)))
  const dailyMinutes = tasks.reduce((totals, task) => {
    const day = task.day_date || '未提供日期'
    totals[day] = (totals[day] || 0) + (Number(task.estimated_task_minutes) || 0)
    return totals
  }, {})
  const details = previewDetails(plan.details)
  const knowledgeSteps = [...new Map(tasks.map((task) => [task.parent_task_id || task.id || task.title, task])).values()]
  const knowledgeTitles = new Map(knowledgeSteps.map((task) => [task.parent_task_id || task.id, task.title || '未命名知识任务']))
  const startDate = details.startDate || tasks[0]?.day_date
  const deadline = details.deadline || tasks[tasks.length - 1]?.day_date
  return <div className="plan-preview">
    <div className="page-title">
      <div><div className="eyebrow">PLAN PREVIEW</div><h2>{plan.title || '未命名计划'}</h2><p>{details.learningGoal || '请核对任务、日期和完成条件后保存。'}</p>{(details.description || details.expectedOutput || details.currentLevel || details.character) && <div className="plan-context">{details.description && <span><b>计划说明</b>{details.description}</span>}{details.expectedOutput && <span><b>预期产出</b>{details.expectedOutput}</span>}{details.currentLevel && <span><b>当前水平</b>{details.currentLevel}</span>}{details.character && <span><b>教师风格</b>{details.character}</span>}</div>}</div>
    </div>
    <div className="plan-summary">
      <Metric label="学习安排" value={tasks.length} suffix=" 段" />
      <Metric label="每天投入" value={plan.details?.minutes_per_day} suffix=" 分钟" />
      <Metric label="单次时长上限" value={plan.details?.preferred_session_minutes} suffix=" 分钟" />
      <Metric label="开始日期" value={startDate} />
      <Metric label="截止日期" value={deadline} />
      <Metric label="计划状态" value={saved ? '已保存' : '待保存'} />
    </div>
    <section className="knowledge-route" aria-label="待确认知识地图"><div><b>知识路线草图</b><small>箭头依据任务前置关系；核对后保存才会成为正式计划。</small></div><ol>{knowledgeSteps.map((task, index) => { const prerequisites = Array.isArray(task.prerequisite_task_ids) ? task.prerequisite_task_ids : []; return <li key={task.parent_task_id || task.id || index}><span>{index + 1}</span><div><b>{task.title || '未命名知识任务'}</b><small>{task.objective || task.acceptance_condition || '待补充学习目标'} · {prerequisites.length ? `前置：${prerequisites.map((id) => knowledgeTitles.get(id) || '未知节点').join('、')}` : '学习起点'}</small></div></li> })}</ol></section>
    <div className="task-cards">
      {tasks.map((task, index) => <article className="task-card" key={`${task.day_date || 'day'}-${index}`}>
        <div className="task-date">{task.day_date || '未提供日期'}</div>
        <div className="task-main"><h3>{task.title || '未命名任务'}</h3><p>{task.acceptance_condition || '未提供完成条件。'}</p></div>
        <strong>{task.estimated_task_minutes ?? '—'}{task.estimated_task_minutes == null ? '' : ' 分钟'}</strong>
      </article>)}
    </div>
    <div className="daily-schedule" aria-label="每日任务时长"><b>每日任务时长</b>{Object.entries(dailyMinutes).map(([day, minutes]) => <div key={day}><span>{day}</span><strong>{minutes} 分钟</strong></div>)}</div>
    <div className="plan-sources" id="plan-source-map">
      <div className="source-map-heading"><b>资料地图 · 生成依据</b><a href="#plan-source-map">查看入口</a></div>
      {localSources.map((source) => <div key={String(source.id)}><span>本机资料 · {source.name || source.filename || String(source.id)}</span><small>已纳入计划的本机资料</small></div>)}
      {sources.map((source) => <div key={source.id}><span>网页资料 · {source.title || source.final_url}</span><small>{source.final_url} · {source.content_sha256}</small></div>)}
      {!localSources.length && !sources.length && <small>本计划没有关联可展示的资料来源。</small>}
    </div>
    <div className="form-actions"><button className="secondary" onClick={onCancel} disabled={saving}>返回调整</button><button className="primary" onClick={onSave} disabled={saving || saved}>{saved ? '已保存' : saving ? '保存中…' : '保存计划'} <span>→</span></button></div>
  </div>
}

function DraftReview({ draft, busy, onReview, onLock, onConfirm }) {
  if (!draft) return <section className="draft-review"><b>计划草稿审阅</b><p className="muted">点击审阅，把当前表单投影为可锁定的计划阶段。</p><button className="secondary" onClick={onReview} disabled={Boolean(busy)}>审阅当前草稿</button></section>
  const stages = Array.isArray(draft.stages) ? draft.stages : []
  return <section className="draft-review" aria-label="计划草稿审阅">
    <div className="draft-review-heading"><div><b>计划草稿 · {draft.status}</b><small>{draft.target_deadline ? `目标截止 ${draft.target_deadline}` : '尚未确定截止日期'}</small></div><button className="secondary" onClick={onReview} disabled={Boolean(busy)}>重新审阅</button></div>
    <div className="draft-summary"><span><b>首次学习预估</b>{draft.proposed_first_pass?.target_date || '待 Scheduler 复核'}</span><span><b>初步学习量</b>{Number(draft.total_initial_learning_minutes || 0)} 分钟</span><span><b>日程风险</b>{draft.schedule_risk === 'high_risk' ? '高风险' : draft.schedule_risk === 'tight' ? '紧凑' : '可承受'}</span></div>
    {draft.assumptions?.length > 0 && <ul className="draft-assumptions">{draft.assumptions.map((item) => <li key={item}>{item}</li>)}</ul>}
    <div className="draft-stages">{stages.map((stage) => <div className="draft-stage" key={stage.id}><span><b>{stage.title}</b><small>{stage.summary}</small></span><button className="secondary" onClick={() => onLock(stage)} disabled={Boolean(busy)}>{stage.locked ? '解锁' : '锁定'}</button></div>)}</div>
    <button className="primary" onClick={onConfirm} disabled={Boolean(busy) || draft.status !== 'PLAN_READY' || draft.confirmed}>{draft.confirmed ? '已确认生成条件' : '确认这份草稿'}</button>
  </section>
}

export default function App() {
  const bridge = useMemo(() => new PlanningBridge(), [])
  const [form, setFormState] = useState(defaultForm)
  const formRef = useRef(form)
  const [materials, setMaterials] = useState([])
  const [researchCandidates, setResearchCandidates] = useState([])
  const [researchSources, setResearchSources] = useState([])
  const [researchSelection, setResearchSelection] = useState([])
  const [researchQuery, setResearchQuery] = useState('')
  const [conversationMessages, setConversationMessages] = useState([])
  const [conversationProposal, setConversationProposal] = useState(null)
  const [searchSuggestions, setSearchSuggestions] = useState([])
  const [conversationDraft, setConversationDraft] = useState('')
  const [manualSourceUrl, setManualSourceUrl] = useState('')
  const [researchFailures, setResearchFailures] = useState([])
  const [presets, setPresets] = useState([])
  const [step, setStep] = useState(0)
  const [maxStep, setMaxStep] = useState(0)
  const [assessment, setAssessment] = useState(null)
  const [plan, setPlan] = useState(null)
  const [busy, setBusy] = useState('')
  const [status, setStatus] = useState('正在连接规划服务…')
  const [draftStatus, setDraftStatus] = useState('')
  const [planSaved, setPlanSaved] = useState(false)
  const [presetName, setPresetName] = useState('我的学习预设')
  const [presetMessage, setPresetMessage] = useState('')
  const [feasibilityChoice, setFeasibilityChoice] = useState('keep_current')
  const [generationConfirmed, setGenerationConfirmed] = useState(false)
  const [draftPlan, setDraftPlan] = useState(null)
  const [draftRevision, setDraftRevision] = useState(1)
  const [intakeAnalysis, setIntakeAnalysis] = useState(null)
  const draftTimer = useRef(null)
  const draftPending = useRef(null)
  const draftQueue = useRef(Promise.resolve())
  const operation = useRef({ id: 0, action: '' })

  const setForm = useCallback((next) => {
    formRef.current = next
    setFormState(next)
  }, [])

  const persistDraft = useCallback((next) => {
    draftQueue.current = draftQueue.current.catch(() => {}).then(() => bridge.request('save_draft', { form: next }))
      .then((data) => {
        if (data?.draft_plan) setDraftPlan(data.draft_plan)
        if (data?.draft_revision) setDraftRevision(Number(data.draft_revision))
        setGenerationConfirmed(false)
        setDraftStatus('草稿已保存。')
      })
      .catch((error) => setDraftStatus(error.message))
    return draftQueue.current
  }, [bridge])

  const queueDraft = useCallback((next) => {
    if (!bridge.hasTransport) return
    draftPending.current = next
    setDraftStatus('正在保存草稿…')
    if (draftTimer.current) window.clearTimeout(draftTimer.current)
    draftTimer.current = window.setTimeout(() => {
      draftTimer.current = null
      const pending = draftPending.current
      draftPending.current = null
      if (pending) persistDraft(pending)
    }, 420)
  }, [bridge, persistDraft])

  const flushDraft = useCallback(() => {
    if (!bridge.hasTransport) return Promise.resolve()
    if (draftTimer.current) {
      window.clearTimeout(draftTimer.current)
      draftTimer.current = null
      const pending = draftPending.current
      draftPending.current = null
      if (pending) persistDraft(pending)
    }
    return draftQueue.current.catch(() => {})
  }, [bridge, persistDraft])

  const stageDraft = useCallback((next) => {
    if (!bridge.hasTransport) return
    bridge.request('stage_draft', { form: next }).catch(() => {})
  }, [bridge])

  const updateForm = useCallback((field, value) => {
    const next = { ...formRef.current, [field]: value }
    if (next.conversation_confirmed_fields.includes(field) && JSON.stringify(formRef.current[field]) !== JSON.stringify(value)) {
      next.conversation_confirmed_fields = next.conversation_confirmed_fields.filter((confirmed) => confirmed !== field)
    }
    setForm(next)
    if (plan) {
      setPlan(null)
      setPlanSaved(false)
    }
    // Editing any input invalidates the previous review/confirmation.  Keep
    // the stages visible for context, but require a fresh review before an
    // expensive formal curriculum generation can start.
    setDraftPlan((current) => current ? { ...current, confirmed: false } : current)
    setGenerationConfirmed(false)
    stageDraft(next)
    queueDraft(next)
  }, [plan, queueDraft, setForm, stageDraft])

  useEffect(() => {
    bridge.request('get_state', {}).then((data) => {
      const next = normalizeForm(data.form)
      setForm(next)
      setConversationMessages(next.conversation_history)
      setConversationProposal(Object.keys(next.conversation_pending_proposal).length ? next.conversation_pending_proposal : null)
      setMaterials(Array.isArray(data.materials) ? data.materials : [])
      setResearchSources(Array.isArray(data.research_sources) ? data.research_sources : [])
      setResearchSelection([])
      setPresets(Array.isArray(data.presets) ? data.presets : [])
      setDraftPlan(data.draft_plan || null)
      setDraftRevision(Number(data.draft_revision || 1))
      // Rehydrate the durable confirmation event as UI state.  The checkbox
      // is only a prompt; after refresh or on another device the server-side
      // DraftPlan + confirmation metadata remains the source of truth.
      setGenerationConfirmed(Boolean(data.draft_plan?.confirmed && data.draft_confirmation))
      setPresetName(next.title || '我的学习预设')
      setStatus('已读取当前计划设置。')
    }).catch((error) => setStatus(error.message))
    return () => {
      if (draftTimer.current) window.clearTimeout(draftTimer.current)
      draftPending.current = null
      bridge.dispose()
    }
  }, [bridge, setForm])

  const run = useCallback((action, payload, success, { flushDraftBefore = true } = {}) => {
    const current = { id: operation.current.id + 1, action }
    operation.current = current
    setBusy(action)
    setStatus('正在处理…')
    const active = () => operation.current === current
    const cancelled = () => new BridgeError('当前操作已取消。', 'PLANNING_CANCELLED')
    const task = (flushDraftBefore ? flushDraft() : Promise.resolve()).then(() => {
      if (!active()) throw cancelled()
      return bridge.request(action, payload)
    }).then((data) => {
      if (active()) success?.(data || {})
      return data
    }).catch((error) => {
      if (active() && error.code !== 'PLANNING_CANCELLED') setStatus(error.message)
      throw error
    }).finally(() => {
      if (active()) {
        setBusy('')
        operation.current = { id: current.id, action: '' }
      }
    })
    current.promise = task
    return task
  }, [bridge, flushDraft])

  const cancelCurrent = () => {
    const current = operation.current
    if (!['assist', 'generate', 'analyze', 'search_sources', 'fetch_sources'].includes(current.action)) return
    operation.current = { id: current.id + 1, action: 'cancel' }
    setBusy('cancel')
    setStatus('正在取消当前操作…')
    bridge.request('cancel', {}).then(() => setStatus('当前操作已取消。')).catch((error) => setStatus(error.message)).finally(() => {
      if (operation.current.action === 'cancel') {
        setBusy('')
        operation.current = { id: operation.current.id, action: '' }
      }
    })
  }

  const savePreset = () => {
    const name = (presetName || form.title || '我的学习预设').trim()
    if (!name) return
    run('save_preset', { name, form }, (data) => {
      if (Array.isArray(data.presets)) setPresets(data.presets)
      else setPresets((items) => [{ name, form }, ...items.filter((item) => item.name !== name)])
      setPresetMessage(`已保存“${name}”`)
    }).catch(() => {})
  }

  const loadPreset = (name) => {
    const preset = presets.find((item) => item.name === name)
    if (!preset?.form) return
    const next = normalizeForm({ ...formRef.current, ...preset.form })
    setForm(next)
    setConversationMessages(next.conversation_history)
    setConversationProposal(Object.keys(next.conversation_pending_proposal).length ? next.conversation_pending_proposal : null)
    setPresetName(preset.name)
    setPlan(null)
    setPlanSaved(false)
    setGenerationConfirmed(false)
    setPresetMessage(`已加载“${preset.name}”`)
    stageDraft(next)
    queueDraft(next)
  }

  const importMaterials = () => run('import_materials', {}, (data) => {
    setMaterials(Array.isArray(data.materials) ? data.materials : [])
    const imported = Array.isArray(data.imported_material_ids) ? data.imported_material_ids.map(String) : []
    if (imported.length) updateForm('material_ids', [...new Set([...formRef.current.material_ids, ...imported])])
    setStatus('资料选择已更新。')
  }).catch(() => {})

  const importText = () => {
    if (!form.sourceText.trim()) { setStatus('请先粘贴资料文字。'); return }
    run('import_text', { text: form.sourceText, title: form.title || '粘贴资料' }, (data) => {
      setMaterials(Array.isArray(data.materials) ? data.materials : [])
      const imported = Array.isArray(data.imported_material_ids) ? data.imported_material_ids.map(String) : []
      if (imported.length) updateForm('material_ids', [...new Set([...formRef.current.material_ids, ...imported])])
      setStatus(imported.length ? '粘贴资料已加入计划资料。' : '粘贴资料已导入；请勾选要用于计划的资料。')
    }).catch(() => {})
  }

  const conversationQuery = (extra = '') => [...conversationMessages.filter((message) => message.role === 'user').map((message) => message.text), extra.trim()].filter(Boolean).join('\n')

  const appendConversationUser = (text) => {
    const id = `${Date.now()}-${conversationMessages.length}`
    const message = { id, role: 'user', text }
    const nextMessages = [...conversationMessages, message]
    setConversationMessages(nextMessages)
    updateForm('conversation_history', nextMessages.map(({ role, text: content }) => ({ role, text: content })))
    if (!formRef.current.description.trim()) updateForm('description', text)
    setConversationDraft('')
  }

  const recordConversation = (value = conversationDraft) => {
    const text = value.trim()
    if (!text) { setStatus('请先写下想学习或完成的事情。'); return false }
    const history = conversationMessages.slice(-20)
    // Preserve the last reviewable proposal until this model turn succeeds.
    // A failed request must not silently discard information awaiting consent.
    appendConversationUser(text)
    return run('assist', { message: text, history }, (data) => {
      const reply = { id: `${Date.now()}-${conversationMessages.length}-assistant`, role: 'assistant', text: data.reply || '我已理解这段需求，可以继续补充。' }
      const nextMessages = [...formRef.current.conversation_history.map((message, index) => ({ ...message, id: `restored-${index}` })), reply]
      setConversationMessages(nextMessages)
      updateForm('conversation_history', nextMessages.map(({ role, text: content }) => ({ role, text: content })))
      const proposal = data.updates && Object.keys(data.updates).length ? data.updates : null
      setConversationProposal(proposal)
      updateForm('conversation_pending_proposal', proposal || {})
      const suggestions = Array.isArray(data.queries) ? data.queries.filter((item) => item && typeof item.query === 'string' && item.query.trim()).slice(0, 5) : []
      if (!suggestions.length && typeof data.query === 'string' && data.query.trim()) suggestions.push({ query: data.query.trim(), reason: '' })
      setSearchSuggestions(suggestions)
      if (data.query) setResearchQuery(data.query)
      setStatus(data.updates && Object.keys(data.updates).length ? '对话提取了可确认的计划信息；确认后才会更新草稿。' : data.query ? '模型已理解需求；可以根据建议搜索资料。' : '模型已理解需求，可以继续补充或进入资料选择。')
    }).catch(() => null)
  }

  const searchSources = (requestedQuery = '') => {
    if (!formRef.current.goal_statement.trim()) {
      setStatus('请先说明学习目标，再开始检索资料。')
      return
    }
    const query = requestedQuery.trim() || researchQuery.trim() || conversationQuery() || [form.title, form.description, form.goal_statement].filter((value) => value.trim()).join(' ')
    if (!query) { setStatus('请先填写计划名称、描述或目标，再搜索资料。'); return }
    // A new search invalidates the previous candidate list. Keeping old
    // candidates visible after an upstream failure makes it possible to read
    // stale results as if they belonged to the current goal.
    setResearchCandidates([])
    setResearchSelection([])
    setResearchFailures([])
    run('search_sources', { query, form, limit: 8 }, (data) => {
      setResearchQuery(data.query || query)
      setResearchCandidates(Array.isArray(data.candidates) ? data.candidates : [])
      setResearchSelection([])
      setResearchFailures([])
      setStatus(data.candidates?.length ? `已找到 ${data.candidates.length} 个候选资料，请勾选后读取。` : '搜索没有返回可用候选资料。')
    }).catch(() => {})
  }

  const searchFromConversation = (requestedQuery = '') => {
    const extra = conversationDraft.trim()
    if (extra) {
      recordConversation(extra).then((data) => {
        if (data.updates && Object.keys(data.updates).length) {
          setStatus('已生成待确认提案；确认学习目标后即可检索资料。')
          return
        }
        if (SEARCH_READY_FIELDS.every((field) => formRef.current.conversation_confirmed_fields.includes(field))) {
          const query = requestedQuery.trim() || data.query?.trim() || researchQuery.trim() || conversationQuery(extra)
          if (query) {
            setResearchQuery(query)
            setMaxStep((value) => Math.max(value, 1))
            setStep(1)
            searchSources(query)
            return
          }
        }
        setStatus('请先补充助手追问的关键信息，再开始检索资料。')
      }).catch(() => {})
      return
    }
    if (!SEARCH_READY_FIELDS.every((field) => formRef.current.conversation_confirmed_fields.includes(field))) {
      setStatus('请先通过对话确认学习目标，再开始检索资料。')
      return
    }
    const query = requestedQuery.trim() || researchQuery.trim() || conversationQuery()
    if (!query) { setStatus('请先通过对话说明学习目标。'); return }
    setResearchQuery(query)
    setMaxStep((value) => Math.max(value, 1))
    setStep(1)
    searchSources(query)
  }

  const readSources = () => {
    const chosen = researchCandidates.filter((candidate) => researchSelection.includes(candidate.url))
    const manual = manualSourceUrl.trim()
    if (manual) chosen.push({ url: manual, title: '手动提供的资料链接', snippet: '', query: researchQuery })
    if (!chosen.length) { setStatus('请先勾选候选资料，或输入一个网页链接。'); return }
    run('fetch_sources', { candidates: chosen }, (data) => {
      const nextSources = Array.isArray(data.research_sources) ? data.research_sources : researchSources
      const added = Array.isArray(data.sources) ? data.sources : []
      setResearchSources(nextSources)
      setResearchFailures(Array.isArray(data.failed) ? data.failed : [])
      if (added.length) {
        const ids = new Set(formRef.current.source_ids)
        added.forEach((source) => ids.add(String(source.id)))
        updateForm('source_ids', [...ids])
        setStatus(data.failed?.length ? `已读取 ${added.length} 个资料，另有 ${data.failed.length} 个失败，可重试。` : `已读取 ${added.length} 个资料。`)
      } else setStatus(data.failed?.[0]?.message || '没有成功读取资料。')
      setManualSourceUrl('')
    }).catch(() => {})
  }

  const acceptConversationProposal = () => {
    if (!conversationProposal) return
    const confirmed = [...new Set([...formRef.current.conversation_confirmed_fields, ...Object.keys(conversationProposal)])]
    const next = { ...formRef.current, ...conversationProposal, conversation_confirmed_fields: confirmed, conversation_pending_proposal: {} }
    setForm(next)
    setConversationProposal(null)
    setAssessment(null)
    setGenerationConfirmed(false)
    if (plan) { setPlan(null); setPlanSaved(false) }
    stageDraft(next)
    queueDraft(next)
    setStatus('已确认并保存对话整理的信息。你可以继续补充目标、资料或时间安排。')
  }

  const ignoreConversationProposal = () => {
    setConversationProposal(null)
    updateForm('conversation_pending_proposal', {})
  }

  const conversationReady = SEARCH_READY_FIELDS.every((field) => form.conversation_confirmed_fields.includes(field))
  const proposalPending = Boolean(conversationProposal) || Object.keys(form.conversation_pending_proposal || {}).length > 0
  const continueManually = () => { setMaxStep((value) => Math.max(value, 3)); setStep(2); setStatus('已切换到手动填写。填写学习目标后，也可以返回资料选择搜索或上传资料。') }

  const analyze = () => run('analyze', { form }, (data) => {
    setAssessment(data.assessment || data)
    setFeasibilityChoice('keep_current')
    setStatus('可行性分析已返回。')
    setMaxStep((value) => Math.max(value, 4))
    setStep(4)
  }).catch(() => {})
  const analyzeIntake = () => run('analyze_intake', { form: formRef.current }, (data) => {
    setIntakeAnalysis(data)
    setStatus(data.next_question ? `需求还需要确认：${data.next_question}` : '需求信息足够，可以开始选择资料。')
  }).catch(() => {})

  const reviewDraft = () => run('review_draft', { form: formRef.current }, (data) => {
    setDraftPlan(data.draft_plan || null)
    setDraftRevision(Number(data.draft_revision || 1))
    setGenerationConfirmed(false)
    setStatus('草稿已审阅；可以锁定阶段或确认生成条件。')
  }).catch(() => {})
  const toggleDraftStage = (stage) => run(stage.locked ? 'unlock_stage' : 'lock_stage', { stage_id: stage.id }, (data) => {
    setDraftPlan(data.draft_plan || null)
    setStatus(stage.locked ? `已解锁阶段：${stage.title}` : `已锁定阶段：${stage.title}`)
  }).catch(() => {})
  const confirmDraft = () => run('confirm_draft', { plan_id: draftPlan?.revision_id, expected_revision: draftRevision }, (data) => {
    setDraftPlan(data.draft_plan || null)
    setDraftRevision(Number(data.draft_revision || data.confirmed_revision || draftRevision))
    setGenerationConfirmed(true)
    setStatus('已确认计划草稿，可以生成知识路线。')
  }).catch(() => {})

  const generate = (mode) => {
    if (conversationProposal || Object.keys(formRef.current.conversation_pending_proposal || {}).length) {
      setStatus('对话整理的信息尚待确认；请先确认或忽略，再生成计划。')
      return
    }
    // The final review checkbox is the explicit user confirmation for the
    // generation request. DraftReview remains available for users who want
    // to inspect or lock stages, but it must not become a second hidden gate
    // in the established seven-step flow.
    if (!generationConfirmed) {
      setStatus('请先核对生成条件，再生成正式知识路线。')
      return
    }
    setPlan(null)
    setPlanSaved(false)
    setGenerationConfirmed(false)
    run('generate', { form, mode, selection: feasibilityChoice, generation_confirmed: generationConfirmed,
      plan_id: draftPlan?.revision_id, expected_revision: draftRevision }, (data) => {
      if (data.plan) {
        setPlan(data.plan)
        setMaxStep(6)
        setStep(6)
        setStatus('计划已生成，请核对后保存。')
      } else setStatus('规划服务未返回计划。')
    }).catch(() => {})
  }

  const savePlan = () => run('save', {}, () => { setPlanSaved(true); setStatus('计划已保存到当前账号。') }).catch(() => {})
  const cancelPlan = () => run('cancel', {}, () => { setPlan(null); setPlanSaved(false); setStep(5); setStatus('已取消本次计划预览。') }, { flushDraftBefore: false }).catch(() => {})

  const toggleMaterial = (id) => {
    const selected = new Set(formRef.current.material_ids)
    if (selected.has(id)) selected.delete(id); else selected.add(id)
    updateForm('material_ids', [...selected])
  }
  const toggleResearchCandidate = (url) => setResearchSelection((items) => items.includes(url) ? items.filter((item) => item !== url) : [...items, url])
  const toggleResearchSource = (id) => {
    const selected = new Set(formRef.current.source_ids)
    if (selected.has(id)) selected.delete(id); else selected.add(id)
    updateForm('source_ids', [...selected])
  }
  const toggleWeekday = (day) => {
    const selected = new Set(formRef.current.weekdays)
    if (selected.has(day)) selected.delete(day); else selected.add(day)
    updateForm('weekdays', [...selected].sort((a, b) => a - b))
  }
  const advance = (target) => { setMaxStep((value) => Math.max(value, target)); setStep(target) }
  const jump = (target) => { if (target <= maxStep && (target !== 6 || plan)) setStep(target) }

  const renderStep = () => {
    if (step === 0) return <>
      <h2>和模型聊需求</h2><p className="muted">先用自己的话说说想学什么、为什么学、希望完成什么。对话与确认状态会保存在账号本机草稿；搜索动作只调用现有资料检索服务，不会把网页内容当作指令。</p>
      <div className="conversation-card" aria-label="需求对话">
        <div className="conversation-messages" aria-live="polite">
          {!conversationMessages.length && <div className="conversation-empty">例如：我想在两周内掌握 Python 异步编程，并做出一个可以演示的小项目。</div>}
          {conversationMessages.map((message) => <div className={`conversation-message ${message.role}`} key={message.id}><span>{message.role === 'user' ? '你' : '规划助手'}</span><p>{message.text}</p></div>)}
        </div>
        {conversationProposal && <div className="conversation-proposal" aria-label="待确认的计划信息"><b>我从对话中整理出这些信息，请确认后写入草稿</b><dl>{Object.entries(conversationProposal).map(([key, value]) => <div key={key}><dt>{{goal_statement:'学习目标',expected_output:'预期成果',current_level:'当前水平',motivation:'学习动机',success_criteria:'成功标准',start_date:'开始日期',deadline:'截止日期',weekdays:'每周学习日',minutes_per_day:'每日投入',preferred_session_minutes:'单次学习时长',constraints:'时间或学习约束'}[key] || key}</dt><dd>{key === 'weekdays' ? value.map((day) => `周${['一','二','三','四','五','六','日'][day]}`).join('、') : Array.isArray(value) ? value.join('、') : ['minutes_per_day','preferred_session_minutes'].includes(key) ? `${value} 分钟` : String(value)}</dd></div>)}</dl><div className="conversation-actions"><button className="secondary" onClick={ignoreConversationProposal}>忽略</button><button className="primary" onClick={acceptConversationProposal}>确认并更新草稿</button></div></div>}
      <div className="conversation-readiness" role="status">{conversationReady ? '学习目标已由你确认，可以先选资料；成果、水平与时间偏好可稍后补充。' : '待对话确认：学习目标'}</div>
        {intakeAnalysis && <div className={`intake-summary ${intakeAnalysis.status === 'PLAN_READY' ? 'ready' : ''}`}><b>需求检查 · {intakeAnalysis.status}</b><span>{intakeAnalysis.next_question || '当前信息足够，可以检索资料。'}</span></div>}
        {searchSuggestions.length > 0 && <div className="conversation-search-suggestions" aria-label="模型建议的资料检索方向"><b>可从这些方向找资料</b>{searchSuggestions.map((item) => <button className="secondary" key={item.query} onClick={() => searchFromConversation(item.query)} disabled={Boolean(busy) || !conversationReady || proposalPending}><span>{item.query}</span>{item.reason && <small>{item.reason}</small>}</button>)}</div>}
        <textarea aria-label="需求消息" value={conversationDraft} onChange={(event) => setConversationDraft(event.target.value)} placeholder="告诉我你想学什么、已有基础和想交付的成果…" onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); recordConversation() } }} />
        <div className="conversation-actions"><button className="secondary" onClick={() => recordConversation()} disabled={Boolean(busy) || !conversationDraft.trim()}>发送给模型</button><button className="secondary" onClick={analyzeIntake} disabled={Boolean(busy)}>检查需求</button><button className="primary" onClick={searchFromConversation} disabled={Boolean(busy) || (!conversationDraft.trim() && !conversationMessages.length)}>根据对话搜索资料 <span>→</span></button></div>
      </div>
      <label>计划名称<input value={form.title} onChange={(event) => updateForm('title', event.target.value)} placeholder="例如：准备一次专业能力考核" /></label>
      <label>一句话描述<textarea value={form.description} onChange={(event) => updateForm('description', event.target.value)} placeholder="这条计划要带你完成什么变化？" /></label>
      <div className="form-actions"><button className="primary" onClick={() => advance(1)} disabled={Boolean(busy) || !conversationReady}>确认需求并开始选资料 <span>→</span></button><button className="secondary" onClick={continueManually} disabled={Boolean(busy)}>跳过对话，手动填写</button></div>
    </>
    if (step === 1) return <>
      <h2>按需求寻找并确认学习资料</h2><p className="muted">先让搜索服务返回真实候选，再勾选并读取；网页内容只作为参考资料，不会当作指令执行。你仍可导入本机文件或粘贴文字。</p>
      <div className="research-card"><label>搜索需求<input value={researchQuery} onChange={(event) => setResearchQuery(event.target.value)} placeholder={[form.title, form.description, form.goal_statement].filter((value) => value.trim()).join(' ') || '例如：Python 异步编程入门资料'} /></label><div className="import-actions"><button className="secondary" onClick={searchSources} disabled={Boolean(busy)}>按需求搜索真实资料</button><button className="primary" onClick={readSources} disabled={Boolean(busy) || (!researchSelection.length && !manualSourceUrl.trim())}>读取勾选资料</button></div><ResearchCandidates candidates={researchCandidates} selected={researchSelection} onToggle={toggleResearchCandidate} /><div className="manual-source"><input value={manualSourceUrl} onChange={(event) => setManualSourceUrl(event.target.value)} placeholder="也可以输入一个 HTTP(S) 网页链接" /><button className="secondary" onClick={readSources} disabled={Boolean(busy) || !manualSourceUrl.trim()}>读取此链接</button></div>{researchFailures.map((failure) => <div className="research-failure" key={`${failure.url}-${failure.code}`}>读取失败：{failure.url} · {failure.message}</div>)}<ResearchSources sources={researchSources} selected={form.source_ids} onToggle={toggleResearchSource} /></div>
      <div className="import-card"><div className="import-actions"><button className="secondary" onClick={importMaterials} disabled={Boolean(busy)}>从本机资料选择</button><button className="secondary" onClick={importText} disabled={Boolean(busy) || !form.sourceText.trim()}>导入粘贴文字</button></div><textarea className="source-text" value={form.sourceText} onChange={(event) => updateForm('sourceText', event.target.value)} placeholder="也可以把学习资料粘贴到这里" /></div>
      <Materials materials={materials} selected={form.material_ids} onToggle={toggleMaterial} />
      <div className="form-actions"><button className="secondary" onClick={() => setStep(0)}>返回需求对话</button><button className="primary" onClick={() => advance(2)}>确认资料，继续填写目标 <span>→</span></button></div>
    </>
    if (step === 2) return <>
      <h2>把愿望写成可验证的成果</h2><p className="muted">越具体的成果，越容易生成可以完成和检查的任务。</p>
      <label>目标陈述<input value={form.goal_statement} onChange={(event) => updateForm('goal_statement', event.target.value)} /></label>
      <label>为什么要达成<textarea value={form.motivation} onChange={(event) => updateForm('motivation', event.target.value)} /></label>
      <label>最终希望产出什么<input value={form.expected_output} onChange={(event) => updateForm('expected_output', event.target.value)} /></label>
      <label>成功标准<input value={form.success_criteria[0] || ''} onChange={(event) => updateForm('success_criteria', [event.target.value])} placeholder="例如：可以独立完成一次演示" /></label>
      <div className="form-actions"><button className="secondary" onClick={() => setStep(1)}>返回</button><button className="primary" onClick={() => advance(3)}>继续设置时间 <span>→</span></button></div>
    </>
    if (step === 3) return <>
      <h2>为计划留出真实空间</h2><p className="muted">可用日期、每天投入和现实约束会一起参与分析。</p>
      <div className="two-col"><label>开始日期<input type="date" value={form.start_date} onChange={(event) => updateForm('start_date', event.target.value)} /></label><label>截止日期<input type="date" min={form.start_date} value={form.deadline} onChange={(event) => updateForm('deadline', event.target.value)} /></label></div>
      <div className="two-col"><label>每天可投入分钟<input type="number" min="1" max="1440" value={form.minutes_per_day} onChange={(event) => updateForm('minutes_per_day', Number(event.target.value))} /></label><label>偏好的单次学习时长<input type="number" min="10" max="240" step="5" value={form.preferred_session_minutes} onChange={(event) => updateForm('preferred_session_minutes', Number(event.target.value))} /></label></div>
      <fieldset><legend>每周学习日</legend><div className="weekday-grid">{['一', '二', '三', '四', '五', '六', '日'].map((name, day) => <label key={day} className="weekday"><input type="checkbox" checked={form.weekdays.includes(day)} onChange={() => toggleWeekday(day)} />周{name}</label>)}</div></fieldset>
      <label>当前水平<select value={form.current_level} onChange={(event) => updateForm('current_level', event.target.value)}><option value="beginner">入门</option><option value="intermediate">有基础</option><option value="advanced">熟练</option></select></label>
      <label>约束或困难<textarea value={form.constraints} onChange={(event) => updateForm('constraints', event.target.value)} placeholder="例如：周三不能学习、周末只能早上…" /></label>
      <div className="form-actions"><button className="secondary" onClick={() => setStep(2)}>返回</button><button className="primary" onClick={analyze} disabled={Boolean(busy)}>运行可行性分析 <span>✦</span></button></div>
    </>
    if (step === 4) {
      const risk = assessment?.risk_level
      const recommendation = assessment?.recommended_minutes_per_day
      return <>
        <h2>先看清楚，再决定开始</h2><p className="muted">分析结果只展示规划服务实际返回的依据，不替你补造精确数字。</p>
        <div className={`feasibility ${risk || ''}`}><div className="feasibility-top"><div><span className="risk-label">风险等级</span><strong>{risk === 'high' ? '高风险' : risk === 'low' ? '低风险' : risk === 'medium' ? '中风险' : '未提供'}</strong></div><span className="assessment-mark">{assessment?.basis || '规划服务未提供依据。'}</span></div><div className="metric-row"><Metric label="预计需要" value={assessment?.estimated_total_minutes} suffix=" 分钟" /><Metric label="可用时间" value={assessment?.available_total_minutes} suffix=" 分钟" /><Metric label="可用学习日" value={assessment?.available_study_days} suffix=" 天" /></div>{Array.isArray(assessment?.risks) && assessment.risks.map((riskText) => <div className="risk-row" key={riskText}>⚠ {riskText}</div>)}</div>
        <fieldset className="feasibility-choice"><legend>每日投入选择</legend><label><input type="radio" name="feasibility" checked={feasibilityChoice === 'keep_current'} onChange={() => { setFeasibilityChoice('keep_current'); setGenerationConfirmed(false) }} /><span><b>保持当前投入</b><small>按当前 {form.minutes_per_day} 分钟 / 天生成计划。</small></span></label><label><input type="radio" name="feasibility" checked={feasibilityChoice === 'increase_daily_minutes'} disabled={recommendation == null} onChange={() => { setFeasibilityChoice('increase_daily_minutes'); setGenerationConfirmed(false) }} /><span><b>采用建议投入</b><small>{recommendation == null ? '规划服务未提供建议。' : `采用 ${recommendation} 分钟 / 天。`}</small></span></label></fieldset>
        <div className="form-actions"><button className="secondary" onClick={() => setStep(3)}>调整约束</button><button className="primary" onClick={() => advance(5)}>选择教师风格 <span>→</span></button></div>
      </>
    }
    if (step === 5) return <>
      <h2>选择教师的陪练方式</h2><p className="muted">角色只改变语气、提问方式和节奏，不改变事实与完成标准。</p>
      <div className="workflow-role-grid" aria-label="规划阶段角色">{WORKFLOW_ROLES.map((role) => <button className={form.workflow_role === role.id ? 'selected' : ''} onClick={() => updateForm('workflow_role', role.id)} key={role.id}><b>{role.name}</b><small>{role.desc}</small></button>)}</div>
      <div className="character-grid">{CHARACTERS.map((character) => <button className={form.character_id === character.id ? 'selected' : ''} onClick={() => updateForm('character_id', character.id)} key={character.id}><span className="char-icon">{character.icon}</span><b>{character.name}</b><small>{character.desc}</small>{form.character_id === character.id && <span className="check">✓</span>}</button>)}</div>
      <label>手动任务（可选）<textarea value={form.manual_tasks} onChange={(event) => updateForm('manual_tasks', event.target.value)} placeholder="每行一个任务：任务标题 | 预计分钟" /></label>
      <DraftReview draft={draftPlan} busy={busy} onReview={reviewDraft} onLock={toggleDraftStage} onConfirm={confirmDraft} />
      <section className="generation-review" aria-label="生成前需求确认"><b>生成前核对</b><div><span>学习目标</span><strong>{form.goal_statement || '尚未填写'}</strong></div><div><span>学习周期</span><strong>{form.start_date} 至 {form.deadline}</strong></div><div><span>时间偏好</span><strong>每周 {form.weekdays.map((day) => `周${['一','二','三','四','五','六','日'][day]}`).join('、') || '未选学习日'} · 每天 {feasibilityChoice === 'increase_daily_minutes' && assessment?.recommended_minutes_per_day != null ? assessment.recommended_minutes_per_day : form.minutes_per_day} 分钟 · 单次约 {form.preferred_session_minutes} 分钟</strong></div><div><span>已选资料</span><strong>{[...materials.filter((item) => form.material_ids.includes(String(item.id))).map((item) => item.name), ...researchSources.filter((item) => form.source_ids.includes(String(item.id))).map((item) => item.title || item.final_url)].filter(Boolean).join('、') || '未选择资料'}</strong></div><label><input type="checkbox" checked={generationConfirmed} onChange={(event) => setGenerationConfirmed(event.target.checked)} /><span>我已核对目标、时间偏好和资料，并确认用这些信息生成知识路线。</span></label></section>
      <div className="generate-choice"><button className="primary" onClick={() => generate('model')} disabled={Boolean(busy) || !generationConfirmed || !form.goal_statement.trim() || proposalPending}>确认并生成知识路线 <span>✦</span></button><button className="secondary" onClick={() => generate('manual')} disabled={Boolean(busy) || !generationConfirmed || !form.goal_statement.trim() || !form.manual_tasks.trim() || proposalPending}>确认并按输入任务编排 <span>→</span></button></div>
      <div className="form-actions"><button className="secondary" onClick={() => setStep(4)}>返回</button></div>
    </>
    return <PlanPreview plan={plan} onSave={savePlan} onCancel={cancelPlan} saving={busy === 'save' || busy === 'cancel'} saved={planSaved} materials={materials} selectedMaterialIds={form.material_ids} />
  }

  return <main className="planning-shell">
    <aside className="create-intro"><div className="brand-mark">✦</div><div className="eyebrow">BUILD YOUR FLOW · {String(step + 1).padStart(2, '0')}</div><h1>创建一条<br /><em>真正属于你的路线。</em></h1><p>每一步都可以回来调整。我们先把模糊愿望变成可验证的交付结果。</p><div className="stepper">{STEP_LABELS.map((label, index) => <button className={step === index ? 'current' : index < maxStep ? 'done' : ''} onClick={() => jump(index)} disabled={Boolean(busy) || index > maxStep || (index === 6 && !plan)} key={label}><span>{index < maxStep ? '✓' : index + 1}</span>{label}</button>)}</div></aside>
    <section className={`form-panel ${step === 6 ? 'plan-form-panel' : ''}`}><div className="panel-top"><span>步骤 {step + 1} / {STEP_LABELS.length}</span><PresetBar name={presetName} setName={setPresetName} presets={presets} onSave={savePreset} onLoad={loadPreset} message={presetMessage} disabled={Boolean(busy)} /></div><div className="panel-content"><fieldset className="panel-form" disabled={Boolean(busy)}>{renderStep()}</fieldset></div><div className="status-line" role="status"><span>{busy ? `正在${busy === 'assist' ? '让模型理解需求' : busy === 'generate' ? '生成计划' : busy === 'analyze' ? '分析可行性' : busy === 'search_sources' ? '搜索资料' : busy === 'fetch_sources' ? '读取资料' : busy === 'save' ? '保存计划' : busy === 'cancel' ? '取消当前操作' : '处理'}…` : status}{draftStatus && <span>{draftStatus}</span>}</span>{['assist', 'generate', 'analyze', 'search_sources', 'fetch_sources'].includes(busy) && <button className="secondary cancel-operation" onClick={cancelCurrent}>取消当前操作</button>}</div></section>
  </main>
}
