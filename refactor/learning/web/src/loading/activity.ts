/** Local UI metadata only. Never retain parameters, answers, keys or tokens. */
export type WaitActivity = {id: string; method: string; started: number; streamed: boolean; cancel?: () => void}
let snapshot: readonly WaitActivity[] = []
const listeners = new Set<() => void>()
function publish() { listeners.forEach(fn => fn()) }
export const getActivities = () => snapshot
export function subscribeActivities(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn) } }
export function beginActivity(id: string, method: string, cancel?: () => void) {
  snapshot = [...snapshot, {id, method, started: Date.now(), streamed: false, cancel}]; publish()
}
export function streamActivity(id: string, delta: string) {
  if (!delta.trim() || delta.startsWith('\u0000') || delta.startsWith('\u0001')) return
  if (!snapshot.some(a => a.id === id && !a.streamed)) return
  snapshot = snapshot.map(a => a.id === id ? {...a, streamed: true} : a); publish()
}
export function endActivity(id: string) { snapshot = snapshot.filter(a => a.id !== id); publish() }

// These operations have placeholders where their content will appear.
export const LOCAL_WAITS = new Set(['app.bootstrap', 'plans.list', 'plan.get', 'plan.current', 'plan.detail', 'plan.report',
  'plan.say', 'plan.submit', 'plan.decide', 'plan.missing', 'plan.map_delete', 'plan.map_delete_confirm', 'plan.more', 'plan.outline', 'plan.estimate', 'plan.generate', 'plan.confirm_structure', 'plan.upload',
  'plan.add_analyze', 'plan.add_preview', 'plan.add_generate', 'plan.add_apply', 'chat.list', 'chat.send', 'models.providers', 'models.list', 'models.test', 'models.save', 'usage.summary', 'usage.spend', 'selftest.list', 'selftest.import'])
export function waitLabel(method: string) {
  const exact: Record<string, string> = {'app.bootstrap': '正在准备学习空间…', 'plans.list': '正在读取我的计划…',
    'plan.detail': '正在读取计划…', 'plan.report': '正在整理学习报告…', 'models.test': '正在验证模型连接…',
    'models.balance': '正在查询厂商余额…', 'usage.spend': '正在查询用量与花费…', 'usage.summary': '正在读取模型用量…',
    'selftest.import': '正在识别资料中的题目与答案…', 'selftest.parse': '正在识别资料中的题目与答案…',
    'classroom.enter': '正在进入课堂…', 'classroom.answer': '正在核对这道题的回答…',
    'class.recall_reveal': '正在保存回答并展开参考答案…', 'class.recall_submit': '正在记录答到的要点…',
    'update.check': '正在检查是否有新版本…', 'update.install': '正在下载并准备更新…',
    'plan.export_report': '正在导出学习报告…', 'plan.export_pdf': '正在导出学习报告 PDF…',
    'plan.retry_point_links': '正在重新整理知识点关系…', 'plan.reestimate': '正在重新计算学习分钟…'}
  return exact[method] ?? (method.startsWith('models.') ? '正在处理模型设置…'
    : method.startsWith('selftest.') ? '正在读取或保存题目自测…'
    : method.startsWith('plan.') ? '正在处理这一步计划…'
    : method.startsWith('classroom.') ? '老师正在想…'
    : method.startsWith('chat.') ? '正在读取对话…'
    : method.startsWith('settings.') ? '正在读取或保存设置…' : '正在完成这一步…')
}
export function showActivity(a: WaitActivity) {
  return !LOCAL_WAITS.has(a.method) && !a.streamed && !a.method.startsWith('classroom.') && !a.method.startsWith('class.recall_')
    && !['cancel','sync.status','sync.now','app.window','classroom.leave','selftest.touch','selftest.pause',
      'teacher2d.status','speech.status','voice.status','update.status','update.automatic','update.defer'].includes(a.method)
}
