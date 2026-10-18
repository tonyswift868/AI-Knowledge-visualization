import type { Draft } from "../../../refactor/learning/web/src/pages/NewPlan"
export class BridgeFailure extends Error {}
export let draft: Draft
const listeners = new Set<(d: Draft) => void>()
export function show(value: Draft) { draft = value; listeners.forEach(fn => fn(draft)) }
export const calls: { method: string; params: any }[] = []
export let resolvePlanGet: ((value: {draft: Draft}) => void) | null = null
export async function call(method: string, params?: any) {
  calls.push({method, params})
  if (method === "plan.get") return await new Promise<{draft: Draft}>(resolve => { resolvePlanGet = resolve })
  return {draft}
}
export function on(name: string, fn: (d: Draft) => void) { if (name === "plan_draft") listeners.add(fn); return () => listeners.delete(fn) }
