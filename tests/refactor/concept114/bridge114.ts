import states from './states114.json'
let room = states.first
export const calls: string[] = []
export class BridgeFailure extends Error { constructor(public code: string, message: string) { super(message) } }
export const on = () => () => undefined
export async function call(method: string) {
  calls.push(method)
  if (method === 'speech.status') return { ready: false, components: [] }
  if (method === 'classroom.next') room = states.second
  if (method === 'classroom.confused') room = states.confused
  if (method === 'classroom.leave') return { accepted: true }
  return structuredClone(room)
}
