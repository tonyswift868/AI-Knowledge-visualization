type Room = Record<string, any>

const merchantLine = '商家维度：商家后台查看订单列表、按状态筛选，并导出最近一段时间的订单。'
const longExplanation = Array.from({ length: 12 }, (_, i) =>
  `分片键说明第 ${i + 1} 段：分片键需要结合主要查询路径和数据分布来选择。还要评估数据是否稳定、扩容成本，以及查询会涉及多少分片。`
).join('\n\n') + `\n\n${merchantLine}`

const question = {
  id: 'sharding-question', kind: 'recall', purpose: 'check', number: 36, total: 111,
  stem: '说明分片键的特点。', options: [], origin: 'bank',
  source: { title: '分片键原文', url: null, locator: '第 4 节' },
  revealed: true, answer: '分片键应结合查询路径选择。',
  reference: longExplanation, key_points: ['结合主要查询路径', '兼顾数据分布'], ticked: [0],
}
const base: Room = {
  plan: { id: '212-plan', goal: '数据库分片', state: 'checking' },
  module: { id: 'module-1', title: '分片键', number: 1, total: 4, state: 'passed', navigation: [] },
  points: [{ id: 'point-1', title: '分片键', minutes: 20, taught: true, state: 'green', current: true }],
  step: 'check', question, messages: [], can_skip: false, red: [], more_modules: false,
  check: { passed: 35, total: 111 }, today: null, explanation: null,
}
const verdictMessage = { id: 'verdict-1', role: 'assistant', kind: 'verdict', content: '✗ 这题没答对。', sources: [] }
const sourceMessage = { id: 'answer-source-1', role: 'assistant', kind: 'answer_source', content: longExplanation, sources: [] }
let room: Room = structuredClone(base)
let nextId = 0
export const calls: { method: string; params: Record<string, unknown> }[] = []
export const submissions: { correct: unknown; ticked: unknown }[] = []
export const longText = longExplanation
const listeners = new Map<string, Set<(data: any) => void>>()

export function show(kind: 'check' | 'module' | 'practice' | 'classroom') {
  room = structuredClone(base)
  room.plan.state = kind === 'check' ? 'checking' : 'active'
  room.step = kind === 'classroom' ? 'teaching' : kind === 'module' ? 'testing' : kind === 'practice' ? 'practice' : 'checking'
  room.module.state = kind === 'module' ? 'active' : 'passed'
  room.check = kind === 'check' ? { passed: 35, total: 111 } : null
  room.question.purpose = kind === 'check' ? 'check' : kind === 'module' ? 'module_test' : kind === 'practice' ? 'practice' : 'quick_check'
  room.question.number = kind === 'check' ? 36 : 1
  room.messages = []
  room.explanation = null
  nextId = 0
}

export function showUnrevealed(kind: 'check' | 'module' | 'practice' | 'classroom') {
  show(kind)
  room.question.revealed = false
  room.question.answer = ''
  room.question.ticked = []
}

export function showPersisted(kind: 'check' | 'module' | 'practice' | 'classroom' = 'check', correct = false) {
  room = structuredClone(base)
  room.plan.state = kind === 'check' ? 'checking' : 'active'
  room.step = kind === 'classroom' ? 'teaching' : kind === 'module' ? 'testing' : kind === 'practice' ? 'practice' : 'checking'
  room.module.state = kind === 'module' ? 'active' : 'passed'
  room.check = kind === 'check' ? { passed: 35, total: 111 } : null
  room.question.purpose = kind === 'check' ? 'check' : kind === 'module' ? 'module_test' : kind === 'practice' ? 'practice' : 'quick_check'
  room.question = { ...structuredClone(question), id: 'next-question', number: 37, revealed: false, answer: '', key_points: [] }
  if (correct) {
    room.question.revealed = true
    room.question.answer = '分片键应结合查询路径与数据分布。'
    room.question.reference = longExplanation
    room.question.key_points = ['结合主要查询路径', '兼顾数据分布']
    room.question.ticked = [0, 1]
  }
  room.messages = correct ? [{ ...verdictMessage, content: '✓ 这题答对了。' }] : [verdictMessage, sourceMessage]
  if (correct) room.messages[0].kind = 'verdict'
  room.explanation = { text: longExplanation, source: { title: '分片键原文', url: null, locator: '第 4 节' },
    label: '资料解析', open: true, message_id: verdictMessage.id }
}

export function diagnostic() {
  return { room: structuredClone(room), calls: structuredClone(calls), submissions: structuredClone(submissions), longText }
}

export class BridgeFailure extends Error {
  constructor(public code: string, message: string) { super(message) }
}

export function on(name: string, fn: (data: any) => void) {
  const group = listeners.get(name) ?? new Set<(data: any) => void>()
  group.add(fn); listeners.set(name, group)
  return () => group.delete(fn)
}

export async function call(method: string, params: Record<string, unknown> = {}) {
  calls.push({ method, params })
  if (method === 'speech.status') return { ready: false, components: [], download: null }
  if (method === 'classroom.leave') return { accepted: true }
  if (method === 'classroom.enter' || method === 'classroom.state') return structuredClone(room)
  if (method === 'class.recall_reveal') {
    room.question.revealed = true
    room.question.answer = String(params.answer ?? '')
    return { reference: longExplanation, key_points: question.key_points }
  }
  if (method === 'class.recall_draft') {
    room.question.ticked = Array.isArray(params.ticked) ? params.ticked as number[] : []
    return {}
  }
  if (method === 'class.recall_submit') {
    submissions.push({ correct: params.correct, ticked: params.ticked })
    room.messages = [verdictMessage, sourceMessage]
    room.explanation = { text: longExplanation, source: { title: '分片键原文', url: null, locator: '第 4 节' }, label: '资料解析', open: true, message_id: verdictMessage.id }
    return { correct: params.correct, missed: params.correct ? [] : [1] }
  }
  if (method === 'class.recall_draft') return {}
  if (method === 'classroom.why') {
    room.explanation.open = true
    return structuredClone(room)
  }
  if (method === 'classroom.next' || method === 'classroom.answer' || method === 'classroom.reply') {
    nextId++
    room.messages.push({ id: `reply-${nextId}`, role: 'assistant', kind: 'step_reply', content: '继续结合原文看这个概念。', sources: [] })
    return structuredClone(room)
  }
  if (method === 'classroom.draft_set') return { saved: true }
  return structuredClone(room)
}
