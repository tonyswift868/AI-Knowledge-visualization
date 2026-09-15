import { productFetch } from "../../api/productCommand"
export type ActiveSnapshot = {
  workspace_id: string;
  materials: any[];
  receipt: any | null;
  knowledge_graph: { knowledge_points: any[]; edges: any[] };
  plan: any | null;
  session: any | null;
};

export type ActiveLearningTaskSummary = {
  workspace_id: string;
  title: string;
  archived: boolean;
  status: "setting_up" | "planned" | "learning";
  material_count: number;
  knowledge_point_count: number;
  completed_knowledge_point_count?: number;
  initial_learning_task_count?: number;
  completed_initial_learning_task_count?: number;
  plan_id: string | null;
  session_id: string | null;
  task_count: number;
  completed_task_count: number;
  next_task: any | null;
  today_plan?: {
    date: string;
    knowledge_point_count: number;
    task_count: number;
    planned_task_minutes: number;
    completed_task_minutes: number;
    remaining_task_minutes: number;
    tasks: any[];
    overdue_task_count: number;
    overdue_task_minutes: number;
    oldest_overdue_date: string | null;
    overdue_day_count: number;
    overdue_days: Array<{
      date: string;
      task_count: number;
      remaining_task_minutes: number;
      tasks: any[];
    }>;
    overdue_tasks: any[];
    projected_review_items: any[];
    learning_order_policy: string;
  } | null;
  learning_order_policy?: string | null;
  updated_at: string;
};

export type ActiveTeacherPersona = {
  id: string;
  name: string;
  prompt: string;
  teaching_style: string;
  visual_profile_id: string;
  visual_profile_name: string;
  visual_manifest_url: string;
  usage_count: number;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

export type ActiveTeacherVisualProfile = {
  id: string;
  name: string;
  description: string;
  manifest_url: string;
  interactive: boolean;
};

export type FocusMonitoringPrivacy = {
  processing: "browser_local";
  frames_stored: false;
  video_uploaded: false;
  audio_captured: false;
  event_cap: number;
};

export type FocusMonitoringSession = {
  id: string;
  teaching_session_id: string;
  workspace_id: string;
  plan_id: string;
  task_id: string;
  status: "active" | "completed";
  detector_kind: string;
  camera_enabled: boolean;
  started_at: string;
  ended_at: string | null;
  total_seconds: number;
  focused_seconds: number;
  distracted_seconds: number;
  focus_rate: number;
  distraction_count: number;
  event_count: number;
  end_reason: string;
  summary: string;
  privacy: FocusMonitoringPrivacy;
};

/**
 * Conservative fallback matching the current conversion registry. At runtime
 * the server capability report is preferred; this keeps the first render
 * usable while that report loads without admitting media formats.
 */
const FALLBACK_MATERIAL_EXTENSIONS = new Set([
  ".txt", ".rtf", ".log", ".ini", ".cfg", ".json", ".jsonl", ".yaml", ".yml", ".xml", ".csv", ".tsv",
  ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".kts", ".sql", ".sh", ".ps1", ".bat", ".css", ".toml",
  ".md", ".markdown", ".html", ".htm", ".pdf", ".docx", ".pptx", ".xlsx", ".epub",
  ".png", ".jpg", ".jpeg", ".webp", ".zip",
]);
const FALLBACK_MATERIAL_MIMES = new Set([
  "text/plain", "text/markdown", "text/html", "application/pdf", "application/rtf", "application/json",
  "application/xml", "text/xml", "text/csv", "text/tab-separated-values", "application/yaml", "application/x-yaml", "text/x-python", "text/x-script", "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/epub+zip", "application/zip",
  "image/png", "image/jpeg", "image/webp",
]);
const EXCLUDED_MEDIA_RE = /(?:video|audio|subtitle|transcript|caption|sensevoice|asr)/i;

export const DOCUMENT_MATERIAL_ACCEPT = [
  ...Array.from(FALLBACK_MATERIAL_EXTENSIONS),
  ...Array.from(FALLBACK_MATERIAL_MIMES),
].join(",");

function capabilityEntries(payload: any): any[] {
  const entries = payload?.registry?.capabilities || payload?.capabilities;
  return Array.isArray(entries) ? entries : [];
}

function capabilityAllowsMaterial(capability: any): boolean {
  return capability?.available !== false
    && capability?.rejected !== true
    && !EXCLUDED_MEDIA_RE.test(String(capability?.adapter || ""))
    && !EXCLUDED_MEDIA_RE.test(String(capability?.reason || ""))
    && ![...(Array.isArray(capability?.suffixes) ? capability.suffixes : []), ...(Array.isArray(capability?.mime_types) ? capability.mime_types : [])]
      .some((value: any) => EXCLUDED_MEDIA_RE.test(String(value || "")));
}

/** Build a browser accept string from the same non-media capability contract. */
export function materialAcceptFromCapabilities(payload: any): string | undefined {
  const entries = capabilityEntries(payload);
  if (!entries.length) return DOCUMENT_MATERIAL_ACCEPT;
  const accepted = new Set<string>();
  entries.filter(capabilityAllowsMaterial).forEach((capability: any) => {
    for (const suffix of Array.isArray(capability?.suffixes) ? capability.suffixes : []) {
      const value = String(suffix || "").trim().toLowerCase();
      if (value && !EXCLUDED_MEDIA_RE.test(value)) accepted.add(value.startsWith(".") ? value : `.${value}`);
    }
    for (const mime of Array.isArray(capability?.mime_types) ? capability.mime_types : []) {
      const value = String(mime || "").trim().toLowerCase();
      if (value && !EXCLUDED_MEDIA_RE.test(value)) accepted.add(value);
    }
  });
  return accepted.size ? Array.from(accepted).join(",") : undefined;
}

/** Runtime counterpart to the browser's non-media capability filter. */
export function isSupportedDocumentFile(
  file: Pick<File, "name" | "type"> | null | undefined,
  capabilitiesPayload?: any,
): boolean {
  if (!file) return false;
  const name = String(file.name || "").trim().toLowerCase();
  const mime = String(file.type || "").trim().toLowerCase();
  const extension = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
  if (/^(?:video|audio)\//i.test(mime) || EXCLUDED_MEDIA_RE.test(extension) || EXCLUDED_MEDIA_RE.test(mime)) return false;
  const capabilities = capabilityEntries(capabilitiesPayload).filter(capabilityAllowsMaterial);
  if (capabilityEntries(capabilitiesPayload).length) {
    if (!capabilities.length) return false;
    return capabilities.some((capability: any) => {
      const suffixes = (Array.isArray(capability?.suffixes) ? capability.suffixes : []).map((value: any) => String(value || "").trim().toLowerCase());
      const mimeTypes = (Array.isArray(capability?.mime_types) ? capability.mime_types : []).map((value: any) => String(value || "").trim().toLowerCase());
      return (extension && suffixes.includes(extension)) || (mime && mimeTypes.includes(mime));
    });
  }
  return FALLBACK_MATERIAL_EXTENSIONS.has(extension) || FALLBACK_MATERIAL_MIMES.has(mime);
}

export type VideoCollectionCoverage = {
  denominator: number;
  ready: number;
  needs_review: number;
  failed: number;
  skipped: number;
  processing: number;
  unprocessed: number;
  coverage_ratio: number;
  complete: boolean;
  warning?: string;
};

export type VideoCollection = {
  id: string;
  collection_id: string;
  title: string;
  canonical_url: string;
  provider: string;
  status: string;
  reported_count: number | null;
  enumerated_count: number;
  selected_count: number;
  manifest_revision: number;
};

export type VideoCollectionEpisode = {
  id: string;
  episode_id: string;
  title: string;
  ordinal: number;
  duration_seconds: number;
  selected: boolean;
  status: string;
  failure?: { code?: string; retryable?: boolean };
  checkpoint?: Record<string, unknown>;
};

export type VideoCollectionJob = {
  id: string;
  job_id: string;
  collection_id: string;
  job_type: "enumerate" | "transcribe" | "summarize" | string;
  status: string;
  progress: Record<string, any>;
  result: Record<string, any>;
  failure: { code?: string; retryable?: boolean };
};

export type VideoManifestPage = {
  collection_id: string;
  manifest_revision: number;
  page_size: number;
  page_index: number;
  page: number;
  previous_cursor: string | null;
  next_cursor: string | null;
  has_more: boolean;
  total_count: number;
  items: VideoCollectionEpisode[];
};

export type VideoCollectionDetail = {
  schema: string;
  collection: VideoCollection;
  coverage: VideoCollectionCoverage;
  jobs: VideoCollectionJob[];
};

async function parseResponse(response: Response) {
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(
      data.error?.message || data.detail?.error?.message || data.detail?.message || "请求失败",
    ) as Error & { code?: string; status?: number };
    error.code = data.error?.code || data.detail?.error?.code;
    error.status = response.status;
    throw error;
  }
  return data;
}

export async function activeRequest(
  path: string,
  init?: RequestInit,
  timeoutMs = 0,
) {
  const headers = init?.body instanceof FormData
    ? init.headers
    : { "Content-Type": "application/json", ...(init?.headers || {}) };
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const timeout = controller
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : null;
  try {
    return await parseResponse(await productFetch(`/api/active-teaching${path}`, {
      ...init,
      headers,
      signal: init?.signal || controller?.signal,
    }));
  } catch (error: any) {
    if (controller?.signal.aborted) {
      throw new Error("真实 AI 分析超过等待上限，页面已解除锁定；资料和已保存条件不会丢失，请稍后重试。");
    }
    throw error;
  } finally {
    if (timeout !== null) window.clearTimeout(timeout);
  }
}

export async function activeSnapshot(workspaceId: string): Promise<ActiveSnapshot> {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/snapshot`);
}

export async function activeLearningTasks(
  includeArchived = false,
  day = "",
): Promise<ActiveLearningTaskSummary[]> {
  const dayQuery = day ? `&day=${encodeURIComponent(day)}` : "";
  const payload = await activeRequest(
    `/workspaces?include_archived=${includeArchived}${dayQuery}`,
  );
  return payload.items || [];
}

export async function activeTeacherPersonas(): Promise<ActiveTeacherPersona[]> {
  const payload = await activeRequest("/teacher-personas");
  return payload.items || [];
}

export async function activeTeacherVisualProfiles(): Promise<ActiveTeacherVisualProfile[]> {
  const payload = await activeRequest("/teacher-visual-profiles");
  return payload.items || [];
}

export async function startFocusMonitoring(
  teachingSessionId: string,
): Promise<FocusMonitoringSession> {
  return activeRequest(
    `/sessions/${encodeURIComponent(teachingSessionId)}/focus-monitoring/start`,
    {
      method: "POST",
      body: JSON.stringify({
        detector_kind: "mediapipe_face_detector",
        camera_enabled: true,
      }),
    },
  );
}

export async function recordFocusMonitoringEvent(
  focusId: string,
  event: {
    client_event_id: string;
    event_type: "attention_lost" | "attention_restored";
    reason: "face_absent" | "page_hidden" | "window_blurred" | "attention_restored";
  },
): Promise<{ accepted: boolean; reason?: string; recorded_at?: string }> {
  return activeRequest(
    `/focus-monitoring/${encodeURIComponent(focusId)}/events`,
    {
      method: "POST",
      body: JSON.stringify(event),
    },
  );
}

export async function finishFocusMonitoring(
  focusId: string,
  reason:
    | "user_finished"
    | "lesson_finished"
    | "page_closed"
    | "component_unmounted"
    | "camera_ended" = "user_finished",
): Promise<FocusMonitoringSession> {
  return activeRequest(
    `/focus-monitoring/${encodeURIComponent(focusId)}/finish`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    },
  );
}

export async function focusMonitoringHistory(
  teachingSessionId: string,
  limit = 5,
): Promise<FocusMonitoringSession[]> {
  const payload = await activeRequest(
    `/sessions/${encodeURIComponent(teachingSessionId)}/focus-monitoring?limit=${limit}`,
  );
  return payload.items || [];
}

export async function saveActiveTeacherPersona(
  input: Pick<ActiveTeacherPersona, "name" | "prompt" | "teaching_style" | "visual_profile_id"> & {
    id?: string;
    is_default?: boolean;
  },
): Promise<ActiveTeacherPersona> {
  return activeRequest(
    input.id
      ? `/teacher-personas/${encodeURIComponent(input.id)}`
      : "/teacher-personas",
    {
      method: input.id ? "PATCH" : "POST",
      body: JSON.stringify(input),
    },
  );
}

export async function deleteActiveTeacherPersona(id: string): Promise<{
  deleted_id: string;
  reassigned_task_count: number;
  replacement: ActiveTeacherPersona;
}> {
  return activeRequest(`/teacher-personas/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function applyActiveTeacherPersona(
  workspaceId: string,
  teacherPersonaId: string,
): Promise<{
  workspace_id: string;
  persona: ActiveTeacherPersona;
  updated_plan_count: number;
  updated_session_count: number;
}> {
  return activeRequest(
    `/workspaces/${encodeURIComponent(workspaceId)}/teacher-persona`,
    {
      method: "PATCH",
      body: JSON.stringify({ teacher_persona_id: teacherPersonaId }),
    },
  );
}

export async function setActiveLearningTaskArchived(workspaceId: string, archived: boolean) {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: "PATCH",
    body: JSON.stringify({ archived }),
  });
}

export async function uploadActiveMaterial(workspaceId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/materials`, {
    method: "POST",
    body: form,
  });
}

export async function uploadActiveUrlMaterial(
  workspaceId: string,
  resource: {
    url: string;
    title?: string;
    find_additional_materials?: boolean;
    selected_candidate_ids?: string[];
    selected_candidate_urls?: string[];
    confirm_transcript?: boolean;
    confirm_supplement_only?: boolean;
  },
) {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/materials/url`, {
    method: "POST",
    body: JSON.stringify(resource),
  }, 60_000);
}

export async function searchVideoSupplementaryMaterials(
  workspaceId: string,
  resource: { url: string; title?: string; query?: string },
) {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/materials/video/search`, {
    method: "POST",
    body: JSON.stringify({
      ...resource,
      find_additional_materials: true,
    }),
  }, 60_000);
}

export async function ingestVideoMaterial(
  workspaceId: string,
  resource: {
    url: string;
    title?: string;
    find_additional_materials: boolean;
    selected_candidate_ids?: string[];
    selected_candidate_urls?: string[];
    confirm_transcript?: boolean;
    confirm_supplement_only?: boolean;
  },
) {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/materials/video/ingest`, {
    method: "POST",
    body: JSON.stringify(resource),
  }, 90_000);
}

export async function createVideoCollection(
  workspaceId: string,
  input: {
    source_url: string;
    user_confirmed_retrieval: true;
    max_episodes?: number;
    page_size?: number;
    requested_languages?: string[];
    allow_automatic?: boolean;
    idempotency_key?: string;
  },
): Promise<{ schema: string; collection: VideoCollection; job: VideoCollectionJob }> {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function readVideoCollection(
  workspaceId: string,
  collectionId: string,
): Promise<VideoCollectionDetail> {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}`);
}

export async function readVideoCollectionManifest(
  workspaceId: string,
  collectionId: string,
  cursor?: string | null,
  limit = 50,
): Promise<VideoManifestPage> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) query.set("cursor", cursor);
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}/manifest?${query.toString()}`);
}

export async function readVideoCollectionEpisode(
  workspaceId: string,
  collectionId: string,
  episodeId: string,
  segmentOffset = 0,
  segmentLimit = 8,
) {
  const query = new URLSearchParams({ segment_offset: String(segmentOffset), segment_limit: String(segmentLimit) });
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}/episodes/${encodeURIComponent(episodeId)}?${query.toString()}`);
}

export async function startVideoCollectionTranscript(
  workspaceId: string,
  collectionId: string,
  input: {
    user_confirmed_retrieval: true;
    requested_languages?: string[];
    allow_automatic?: boolean;
    max_parallel?: 1 | 2;
    idempotency_key?: string;
  },
): Promise<{ collection_id: string; job: VideoCollectionJob }> {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}/jobs/transcribe`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function startVideoCollectionSummary(
  workspaceId: string,
  collectionId: string,
  input: {
    user_confirmed_summary: true;
    provider_session_id: string;
    batch_size?: number;
    idempotency_key?: string;
  },
): Promise<{ collection_id: string; job: VideoCollectionJob }> {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}/jobs/summarize`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function readVideoCollectionJob(
  workspaceId: string,
  collectionId: string,
  jobId: string,
): Promise<VideoCollectionJob> {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}/jobs/${encodeURIComponent(jobId)}`);
}

export async function controlVideoCollectionJob(
  workspaceId: string,
  collectionId: string,
  jobId: string,
  action: "pause" | "cancel" | "resume" | "retry",
  providerSessionId = "",
): Promise<VideoCollectionJob> {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}/jobs/${encodeURIComponent(jobId)}/${action}`, {
    method: "POST",
    ...(action === "resume" || action === "retry"
      ? { body: JSON.stringify({ provider_session_id: providerSessionId }) }
      : {}),
  });
}

export async function readVideoCollectionSummaries(
  workspaceId: string,
  collectionId: string,
  scopeType: "episode" | "batch" | "collection" = "collection",
) {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}/summaries?scope_type=${scopeType}&limit=50`);
}

export async function activateVideoCollectionMaterial(
  workspaceId: string,
  collectionId: string,
  role: "anchor" | "support",
) {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/video-collections/${encodeURIComponent(collectionId)}/activate-material`, {
    method: "POST",
    body: JSON.stringify({ user_confirmed_activation: true, role }),
  });
}

export async function uploadActiveTeachingAttachment(
  sessionId: string,
  file: File,
) {
  const form = new FormData();
  form.append("file", file);
  return activeRequest(
    `/sessions/${encodeURIComponent(sessionId)}/attachments`,
    {
      method: "POST",
      body: form,
    },
  );
}

export async function warmActiveTeachingVoice() {
  return activeRequest("/voice/warm", { method: "POST" }, 5_000);
}

export async function transcribeActiveTeachingVoice(
  sessionId: string,
  blob: Blob,
  filename: string,
) {
  const form = new FormData();
  form.append("file", blob, filename);
  return activeRequest(
    `/sessions/${encodeURIComponent(sessionId)}/voice/transcribe?language=auto`,
    {
      method: "POST",
      body: form,
    },
    90_000,
  ) as Promise<{
    ok: boolean;
    text: string;
    engine: string;
  }>;
}
