import type { Teacher2DManifest, TeacherPresentationState } from "./teacherPresentationTypes"

/** Fresh installations use the original, distributable teacher rather than a developer's private preset. */
export const DEFAULT_TEACHER_MANIFEST_URL = "/teacher2d/original-ai-teacher-blonde-fang/manifest.json"
/** Private local model used only when an existing persona explicitly selects it. */
export const PRIVATE_LOCAL_TEACHER_MANIFEST_URL = "/teacher2d/tsubaki-live2d/manifest.json"
/** Offline-safe layered teacher used when an explicitly selected dynamic model is unavailable. */
export const FALLBACK_TEACHER_MANIFEST_URL = DEFAULT_TEACHER_MANIFEST_URL

const states: readonly TeacherPresentationState[] = [
  "loading", "idle", "greeting", "thinking", "talking", "explaining", "questioning",
  "praising", "encouraging", "confused", "shy", "playful_annoyed", "ending", "error", "hidden",
]

export function validateTeacher2DManifest(value: unknown): Teacher2DManifest {
  const candidate = value as Partial<Teacher2DManifest> | null
  if (!candidate || typeof candidate !== "object") throw new Error("2D 教师清单不是对象")
  const requiredStrings: Array<keyof Teacher2DManifest> = ["id", "displayName", "renderer", "skeleton", "armature", "defaultAnimation"]
  for (const key of requiredStrings) {
    if (typeof candidate[key] !== "string" || !candidate[key]) throw new Error(`2D 教师清单缺少 ${String(key)}`)
  }
  if (candidate.renderer !== "dragonbones2d" && candidate.renderer !== "image2d" && candidate.renderer !== "layered2d" && candidate.renderer !== "live2d2d") throw new Error(`当前适配器不支持 ${candidate.renderer}`)
  if (!Array.isArray(candidate.textures) || candidate.textures.length === 0 || candidate.textures.some(url => typeof url !== "string" || !url)) {
    throw new Error("2D 教师清单缺少纹理页")
  }
  if (candidate.animationAliases && typeof candidate.animationAliases !== "object") throw new Error("animationAliases 必须是对象")
  if (candidate.renderer === "image2d") {
    if (!candidate.imageStates || typeof candidate.imageStates.teaching !== "string" || typeof candidate.imageStates.swimsuit !== "string") {
      throw new Error("image2d 教师清单缺少 teaching/swimsuit 双形态")
    }
  }
  if (candidate.renderer === "layered2d" && (typeof candidate.skeleton !== "string" || !candidate.skeleton.endsWith(".json"))) {
    throw new Error("layered2d 教师清单缺少分层骨架 JSON")
  }
  if (candidate.renderer === "live2d2d") {
    if (typeof candidate.skeleton !== "string" || !candidate.skeleton.endsWith(".model3.json")) throw new Error("live2d2d 教师清单缺少 .model3.json")
    if (candidate.live2d && typeof candidate.live2d !== "object") throw new Error("live2d 配置必须是对象")
    if (candidate.live2d?.coreScript !== undefined && (typeof candidate.live2d.coreScript !== "string" || !candidate.live2d.coreScript)) {
      throw new Error("live2d coreScript 必须是非空字符串")
    }
    if (candidate.live2d?.expressions && typeof candidate.live2d.expressions !== "object") throw new Error("live2d expressions 必须是对象")
    if (candidate.live2d?.parameters && typeof candidate.live2d.parameters !== "object") throw new Error("live2d parameters 必须是对象")
  }
  return candidate as Teacher2DManifest
}

export async function loadTeacher2DManifest(url = DEFAULT_TEACHER_MANIFEST_URL): Promise<Teacher2DManifest> {
  const response = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  })
  if (!response.ok) throw new Error(`2D 教师清单加载失败：HTTP ${response.status}`)
  const contentType = response.headers.get("content-type")?.toLowerCase() || ""
  if (!contentType.includes("json")) {
    throw new Error("2D 教师清单被错误路由；请刷新页面重试")
  }
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
