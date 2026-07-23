export type ImportedSource = {
  id: string
  title: string
  status?: string
  chunk_count?: number
  parse_error?: string
}

export type ImportReceipt = {
  imported?: Array<{ source?: ImportedSource; relative_path?: string } | ImportedSource>
  failed?: Array<{ relative_path?: string; error?: string }>
  duplicates?: Array<{ relative_path?: string; reason?: string }>
  ignored?: Array<{ relative_path?: string; reason?: string }>
}

async function read<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body?.error?.message || `资料读取失败（HTTP ${response.status}）`)
  return body as T
}

export const learningFlowApi = {
  sources: async (workspaceId: string, signal?: AbortSignal): Promise<ImportedSource[]> => {
    return fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/sources`, { signal })
      .then(response => read<ImportedSource[]>(response))
  },
}

export type LearningFlowApi = typeof learningFlowApi
