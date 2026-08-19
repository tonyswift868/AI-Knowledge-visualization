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
    return await parseResponse(await fetch(`/api/active-teaching${path}`, {
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
