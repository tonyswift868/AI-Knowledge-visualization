import type { Draft } from "../../../refactor/learning/web/src/pages/NewPlan"

declare global { interface Window { graph198Data: any } }

export class BridgeFailure extends Error {}
export const requests: { method: string; params?: any }[] = []
const listeners = new Map<string, Set<(value: any) => void>>()

export async function call(method: string, params?: any): Promise<any> {
  requests.push({ method, params })
  if (method === "plan.current" || method === "plan.get") return { draft: window.graph198Data.drafts[params?.phase ?? window.graph198Data.defaultPhase] }
  if (method === "plan.detail") return window.graph198Data.detail
  if (method === "classroom.gaps") return []
  if (method === "settings.teacher_presets") return { presets: [] }
  return { draft: window.graph198Data.drafts[window.graph198Data.defaultPhase] }
}

export function on(name: string, callback: (value: any) => void) {
  const bucket = listeners.get(name) ?? new Set<(value: any) => void>()
  bucket.add(callback)
  listeners.set(name, bucket)
  return () => bucket.delete(callback)
}

export function showDraft(draft: Draft) {
  window.graph198Data.drafts[draft.phase] = draft
  listeners.get("plan_draft")?.forEach(callback => callback(draft))
}
