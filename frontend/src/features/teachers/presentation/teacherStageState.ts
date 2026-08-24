import type { TeacherPresentationState } from "./teacherPresentationTypes"

export const TEACHER_STAGE_IDLE_REACTION_MS = 75_000

export const TEACHER_STAGE_STATES: ReadonlySet<TeacherPresentationState> = new Set([
  "loading", "idle", "greeting", "thinking", "talking", "explaining", "questioning",
  "praising", "encouraging", "confused", "shy", "playful_annoyed", "ending", "error", "hidden",
])

export function readTeacherStageState(search: string): TeacherPresentationState {
  const value = new URLSearchParams(search).get("state") as TeacherPresentationState | null
  return value && TEACHER_STAGE_STATES.has(value) ? value : "idle"
}

export function teacherStageIdleReaction(
  state: TeacherPresentationState,
  elapsedMs: number,
): TeacherPresentationState {
  if (state === "questioning" && elapsedMs >= TEACHER_STAGE_IDLE_REACTION_MS) return "playful_annoyed"
  return state
}
