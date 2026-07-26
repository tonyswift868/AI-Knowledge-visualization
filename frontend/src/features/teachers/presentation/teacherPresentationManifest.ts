import type { Teacher2DManifest, TeacherPresentationState } from "./teacherPresentationTypes"

export const DEFAULT_TEACHER_MANIFEST_URL = "/teacher2d/dragonbones/shizuku/manifest.json"

const states: readonly TeacherPresentationState[] = [
  "loading", "idle", "greeting", "thinking", "talking", "explaining", "questioning",
  "praising", "encouraging", "confused", "ending", "error", "hidden",
]

export function validateTeacher2DManifest(value: unknown): Teacher2DManifest {
  const candidate = value as Partial<Teacher2DManifest> | null
  if (!candidate || typeof candidate !== "object") throw new Error("2D 教师清单不是对象")
  const requiredStrings: Array<keyof Teacher2DManifest> = ["id", "displayName", "renderer", "skeleton", "armature", "defaultAnimation"]
  for (const key of requiredStrings) {
    if (typeof candidate[key] !== "string" || !candidate[key]) throw new Error(`2D 教师清单缺少 ${String(key)}`)
  }
  if (candidate.renderer !== "dragonbones2d") throw new Error(`当前适配器不支持 ${candidate.renderer}`)
  if (!Array.isArray(candidate.textures) || candidate.textures.length === 0 || candidate.textures.some(url => typeof url !== "string" || !url)) {
    throw new Error("2D 教师清单缺少纹理页")
  }
  if (candidate.animationAliases && typeof candidate.animationAliases !== "object") throw new Error("animationAliases 必须是对象")
  return candidate as Teacher2DManifest
}

export async function loadTeacher2DManifest(url = DEFAULT_TEACHER_MANIFEST_URL): Promise<Teacher2DManifest> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`2D 教师清单加载失败：HTTP ${response.status}`)
  return validateTeacher2DManifest(await response.json())
}

export function resolveTeacherAnimation(manifest: Teacher2DManifest, state: TeacherPresentationState, available: readonly string[]): string {
  const requested = manifest.animationAliases?.[state]
  if (requested && available.includes(requested)) return requested
  if (state === "idle" && available.includes(manifest.defaultAnimation)) return manifest.defaultAnimation
  if (available.includes("idle_00")) return "idle_00"
  return available[0] || manifest.defaultAnimation
}

export function semanticStates(): readonly TeacherPresentationState[] {
  return states
}
