const TEACHER_VOICE_API = "/api/teacher-voice"

export type VoicePackLifecycle =
  | "not_installed"
  | "installing"
  | "verifying"
  | "installed"
  | "ready"
  | "error"

export type VoiceFallbackPolicy = "off" | "system_voice"

export type TeacherVoicePack = {
  packId: string
  version: string
  displayName: string
  state: "not_installed" | "installed" | "active" | "failed"
  lifecycle: VoicePackLifecycle
  sizeBytes: number
  defaultPack: boolean
  releaseChannel: "stable" | "prerelease"
  usageScope: "public" | "local_private"
  styles: string[]
  runtimeAdapter: string
}

export type TeacherVoiceRuntime = {
  state: string
  startsModel: boolean
  adapter: string
}

export type TeacherVoiceJob = {
  jobId: string
  action: string
  packId: string
  version: string
  backendStatus: string
  lifecycle: VoicePackLifecycle
  createdAt: string
  updatedAt: string
  history: Array<{ status: string; at: string }>
  error: string
}

export type TeacherVoiceStatus = {
  schema: string
  fallbackPolicy: VoiceFallbackPolicy
  activePack: TeacherVoicePack | null
  runtime: TeacherVoiceRuntime
  packs: TeacherVoicePack[]
}

export type TeacherVoiceSettings = {
  fallbackPolicy: VoiceFallbackPolicy
}

export type TeacherVoiceMutationResult = {
  job: TeacherVoiceJob | null
  pack: TeacherVoicePack | null
  packId: string
  version: string
  status: string
}

type JsonObject = Record<string, unknown>

export class VoicePackApiError extends Error {
  code: string
  status: number

  constructor(message: string, code = "VOICE_PACK_REQUEST_FAILED", status = 0) {
    super(message)
    this.name = "VoicePackApiError"
    this.code = code
    this.status = status
  }
}

function objectValue(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : {}
}

function stringValue(source: JsonObject, ...keys: string[]): string {
  for (const key of keys) {
    const value = source[key]
    if (typeof value === "string") return value
    if (typeof value === "number" && Number.isFinite(value)) return String(value)
  }
  return ""
}

function numberValue(source: JsonObject, ...keys: string[]): number {
  for (const key of keys) {
    const value = source[key]
    if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, value)
    if (typeof value === "string") {
      const parsed = Number(value)
      if (Number.isFinite(parsed)) return Math.max(0, parsed)
    }
  }
  return 0
}

function booleanValue(source: JsonObject, ...keys: string[]): boolean {
  for (const key of keys) {
    const value = source[key]
    if (typeof value === "boolean") return value
    if (value === 1 || value === "1" || value === "true") return true
  }
  return false
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : []
}

function normalizeFallbackPolicy(value: unknown): VoiceFallbackPolicy {
  return value === "system_voice" ? "system_voice" : "off"
}

function lifecycleFromStatus(value: unknown): VoicePackLifecycle {
  const status = String(value || "").toLowerCase()
  if (["staging", "downloading", "installing"].includes(status)) return "installing"
  if (["verified", "verifying", "checking", "validating"].includes(status)) return "verifying"
  if (status === "installed") return "installed"
  if (["active", "ready", "available"].includes(status)) return "ready"
  if (["failed", "error", "corrupt"].includes(status)) return "error"
  return "not_installed"
}

function normalizePack(value: unknown): TeacherVoicePack {
  const raw = objectValue(value)
  const rawState = stringValue(raw, "state", "status")
  const state: TeacherVoicePack["state"] = ["installed", "active", "failed"].includes(rawState)
    ? rawState as TeacherVoicePack["state"]
    : "not_installed"
  const version = stringValue(raw, "version", "latest_version")
  return {
    packId: stringValue(raw, "pack_id", "packId", "id"),
    version,
    displayName: stringValue(raw, "display_name", "displayName", "name") || "原创教师声包",
    state,
    lifecycle: lifecycleFromStatus(state),
    sizeBytes: numberValue(raw, "archive_bytes", "size_bytes", "sizeBytes"),
    defaultPack: booleanValue(raw, "default", "is_default", "defaultPack"),
    releaseChannel: stringValue(raw, "release_channel", "releaseChannel") === "prerelease"
      ? "prerelease"
      : "stable",
    usageScope: stringValue(raw, "usage_scope", "usageScope") === "local_private"
      ? "local_private"
      : "public",
    styles: stringArray(raw.styles),
    runtimeAdapter: stringValue(raw, "runtime_adapter", "runtimeAdapter"),
  }
}

function normalizeJob(value: unknown): TeacherVoiceJob | null {
  const raw = objectValue(value)
  const jobId = stringValue(raw, "job_id", "jobId", "id")
  if (!jobId) return null
  const error = objectValue(raw.error)
  const backendStatus = stringValue(raw, "status", "state")
  return {
    jobId,
    action: stringValue(raw, "action"),
    packId: stringValue(raw, "pack_id", "packId"),
    version: stringValue(raw, "version"),
    backendStatus,
    lifecycle: lifecycleFromStatus(backendStatus),
    createdAt: stringValue(raw, "created_at", "createdAt"),
    updatedAt: stringValue(raw, "updated_at", "updatedAt"),
    history: Array.isArray(raw.history)
      ? raw.history.map(item => {
        const entry = objectValue(item)
        return { status: stringValue(entry, "status", "state"), at: stringValue(entry, "at", "created_at") }
      })
      : [],
    error: stringValue(error, "message") || (typeof raw.error === "string" ? raw.error : ""),
  }
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json")
  const response = await fetch(`${TEACHER_VOICE_API}${path}`, {
    credentials: "same-origin",
    ...init,
    headers,
  })
  let payload: unknown = null
  const contentType = response.headers?.get?.("content-type") || ""
  try {
    payload = contentType.includes("json")
      ? await response.json()
      : await response.text()
  } catch {
    payload = null
  }
  if (!response.ok) {
    const raw = objectValue(payload)
    const error = objectValue(raw.error)
    throw new VoicePackApiError(
      stringValue(error, "message") || (typeof payload === "string" && payload.trim()) || "教师声包请求失败",
      stringValue(error, "code") || "VOICE_PACK_REQUEST_FAILED",
      response.status,
    )
  }
  return payload
}

export async function getTeacherVoiceStatus(): Promise<TeacherVoiceStatus> {
  const raw = objectValue(await requestJson("/status"))
  const activePackRaw = raw.active_pack ?? raw.activePack
  const packs = Array.isArray(raw.packs) ? raw.packs.map(normalizePack) : []
  const runtime = objectValue(raw.runtime)
  return {
    schema: stringValue(raw, "schema"),
    fallbackPolicy: normalizeFallbackPolicy(raw.fallback_policy ?? raw.fallbackPolicy),
    activePack: activePackRaw ? normalizePack(activePackRaw) : null,
    runtime: {
      state: stringValue(runtime, "state", "status"),
      startsModel: booleanValue(runtime, "starts_model", "startsModel"),
      adapter: stringValue(runtime, "adapter", "runtime_adapter"),
    },
    packs,
  }
}

export async function listTeacherVoicePacks(): Promise<TeacherVoicePack[]> {
  const raw = objectValue(await requestJson("/packs"))
  return Array.isArray(raw.packs) ? raw.packs.map(normalizePack) : []
}

export async function getTeacherVoiceJob(jobId: string, signal?: AbortSignal): Promise<TeacherVoiceJob> {
  const job = normalizeJob(await requestJson(`/jobs/${encodeURIComponent(jobId)}`, { signal }))
  if (!job) throw new VoicePackApiError("教师声包任务响应无效", "VOICE_PACK_JOB_INVALID")
  return job
}

function normalizeMutation(value: unknown): TeacherVoiceMutationResult {
  const raw = objectValue(value)
  return {
    job: normalizeJob(raw.job),
    pack: raw.pack ? normalizePack(raw.pack) : null,
    packId: stringValue(raw, "pack_id", "packId"),
    version: stringValue(raw, "version"),
    status: stringValue(raw, "status", "state"),
  }
}

export async function installTeacherVoicePack(packId: string, version?: string): Promise<TeacherVoiceMutationResult> {
  return normalizeMutation(await requestJson(`/packs/${encodeURIComponent(packId)}/install`, {
    method: "POST",
    body: JSON.stringify(version ? { version } : {}),
  }))
}

export async function activateTeacherVoicePack(packId: string, version?: string): Promise<TeacherVoiceMutationResult> {
  return normalizeMutation(await requestJson(`/packs/${encodeURIComponent(packId)}/activate`, {
    method: "POST",
    body: JSON.stringify(version ? { version } : {}),
  }))
}

export async function getTeacherVoiceSettings(): Promise<TeacherVoiceSettings> {
  const raw = objectValue(await requestJson("/settings"))
  return { fallbackPolicy: normalizeFallbackPolicy(raw.fallback_policy ?? raw.fallbackPolicy) }
}

export async function updateTeacherVoiceSettings(
  fallbackPolicy: VoiceFallbackPolicy,
): Promise<TeacherVoiceSettings> {
  const raw = objectValue(await requestJson("/settings", {
    method: "PATCH",
    body: JSON.stringify({ fallback_policy: fallbackPolicy }),
  }))
  return { fallbackPolicy: normalizeFallbackPolicy(raw.fallback_policy ?? raw.fallbackPolicy) }
}

export async function deleteTeacherVoicePack(
  packId: string,
  version: string,
): Promise<TeacherVoiceMutationResult> {
  return normalizeMutation(await requestJson(
    `/packs/${encodeURIComponent(packId)}/versions/${encodeURIComponent(version)}`,
    { method: "DELETE" },
  ))
}
