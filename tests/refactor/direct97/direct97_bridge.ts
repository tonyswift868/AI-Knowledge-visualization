import states from './states.json'
export const calls: { method: string; params: Record<string, unknown> }[] = []
let room = states.entry
let index = 0
export function show(stage: 'entry' | 'original' | 'choice' | 'graded') { room = states[stage] }
export function diagnosticRoom() {
  const value = room as { step?: unknown; direct_review?: unknown }
  return { step: value?.step ?? null, direct_review: structuredClone(value?.direct_review ?? null) }
}
export class BridgeFailure extends Error { constructor(public code: string, message: string) { super(message) } }
export const on = () => () => undefined
export async function call(method: string, params: Record<string, unknown> = {}) {
  calls.push({ method, params })
  if (method === 'speech.status') return { ready: false, components: [], download: null }
  if (method === 'classroom.leave') return { accepted: true }
  if (method === 'classroom.test') { index = 0; room = states.prompts[0] }
  if (method === 'classroom.reply') room = states.answers[index]
  if (method === 'classroom.direct_next') { index++; room = states.prompts[index] }
  if (method === 'classroom.direct_choice') room = params.skip ? states.skipped : states.entry
  return structuredClone(room)
}
