import type { Draft } from "../../../refactor/learning/web/src/pages/NewPlan"
export class BridgeFailure extends Error {}
export const settings = {version:1, theme:'sakura', font_scale:1, reduce_motion:true}
export let draft: Draft = (window as any).step64Data
const listeners = new Map<string, Set<(data: any) => void>>()
export function show(value: Draft) { draft=value; listeners.get('plan_draft')?.forEach(fn => fn(draft)) }
export function on(name: string, fn: (data: any) => void) {
  if (!listeners.has(name)) listeners.set(name,new Set())
  listeners.get(name)!.add(fn)
  return () => { listeners.get(name)?.delete(fn) }
}
export async function call(method: string) {
  if (method === 'app.bootstrap') return {account:{name:'界面测试账号',role_label:'学习者'},settings,options:{},notice:null,ready:true}
  if (method.startsWith('update.')) return {available:false,busy:false,state:'UP_TO_DATE',version:'test',current:'test'}
  if (method === 'sync.status') return {state:'synced',at:null,message:''}
  if (method.startsWith('plan.')) return {draft}
  throw new Error('Unexpected offline bridge call: '+method)
}
