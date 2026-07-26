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
  status: "setting_up" | "planned" | "learning";
  material_count: number;
  knowledge_point_count: number;
  plan_id: string | null;
  session_id: string | null;
  task_count: number;
  completed_task_count: number;
  next_task: any | null;
  updated_at: string;
};

async function parseResponse(response: Response) {
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error?.message || data.detail?.error?.message || data.detail?.message || "请求失败");
  }
  return data;
}

export async function activeRequest(path: string, init?: RequestInit) {
  const headers = init?.body instanceof FormData
    ? init.headers
    : { "Content-Type": "application/json", ...(init?.headers || {}) };
  return parseResponse(await fetch(`/api/active-teaching${path}`, { ...init, headers }));
}

export async function activeSnapshot(workspaceId: string): Promise<ActiveSnapshot> {
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/snapshot`);
}

export async function activeLearningTasks(): Promise<ActiveLearningTaskSummary[]> {
  const payload = await activeRequest("/workspaces");
  return payload.items || [];
}

export async function uploadActiveMaterial(workspaceId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return activeRequest(`/workspaces/${encodeURIComponent(workspaceId)}/materials`, {
    method: "POST",
    body: form,
  });
}
