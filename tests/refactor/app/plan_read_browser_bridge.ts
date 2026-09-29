const listeners = new Map<string, Set<(data: unknown) => void>>()
let fail = true
export function setFail(value: boolean) { fail = value }
export function call(method: string) {
  if (method !== 'plans.list') return Promise.resolve({})
  if (fail) return Promise.reject(new Error('离线读取失败'))
  return Promise.resolve({ plans: [{ id: 'p', title: '旧计划', goal: '学习', final_goal: '', state: 'active',
    archived: false, module_count: 1, window: null, total_minutes: 20, today: null }], study_day: '2026-09-30' })
}
export function on(event: string, fn: (data: unknown) => void) {
  const handlers = listeners.get(event) ?? new Set()
  handlers.add(fn); listeners.set(event, handlers)
  return () => handlers.delete(fn)
}
export function emit(event: string) { listeners.get(event)?.forEach((fn) => fn({})) }
