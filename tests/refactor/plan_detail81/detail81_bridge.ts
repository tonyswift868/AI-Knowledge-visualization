import detail from './detail81_data.json'
import * as native from '../../../refactor/learning/web/src/bridge'
const connected = (window as any).detail81Native ? native.connect() : null
export const requests: {method: string; params?: any}[] = []
export const control = { timeout: false, preset: {id: 'a', name: '温柔老师'} }
export class BridgeFailure extends Error {}
export async function call<T = any>(method: string, params?: any): Promise<T> {
  requests.push({method, params})
  if (method === 'plan.detail') {
    if (control.timeout) return new Promise(() => {})
    if ((window as any).detail81Throw) return {...detail, plan: {...detail.plan, title: {broken: true}}} as T
    const result = connected ? (await connected, await native.call('plan.detail',params)) : detail
    return {...result, teacher_preset: control.preset} as T
  }
  if (method === 'app.bootstrap') return {ready: true, account: {name: '隔离测试'}, settings: {theme: 'sakura', reduce_motion: true}, options: {}} as T
  if (method === 'settings.teacher_presets') return {presets: [{id: 'a', name: '温柔老师'}, {id: 'b', name: '严谨老师'}], default_id: 'a'} as T
  if (method === 'plan.set_teacher_preset') { control.preset = {id: params.preset_id, name: '严谨老师'}; return {teacher_preset: control.preset} as T }
  if (method === 'classroom.gaps') return [] as T
  if (method === 'plans.list') return {plans: [ {...detail.plan, archived: false, ...((window as any).detail81ThrowHome ? {title:{broken:true},today:{today_target:0,today_confirmed:0,today_tentative:0,debt_minutes:0,ahead_minutes:0}} : {})} ]} as T
  if (method.startsWith('update.')) return {available: false, busy: false, state: 'UNPACKAGED'} as T
  if (method === 'teacher.assets' || method === 'voice.status') return {available: false} as T
  return {} as T
}
export function on() { return () => {} }
