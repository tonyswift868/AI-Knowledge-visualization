import states from './states126.json'
export const data = states
export const on = () => () => undefined
export class BridgeFailure extends Error {}
export async function call(method: string) {
  if (method === 'plan.detail') return (window as any).daily126Second ? states.second : states.first
  if (method === 'classroom.gaps') return []
  return {}
}
