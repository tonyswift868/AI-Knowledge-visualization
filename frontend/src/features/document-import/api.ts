import type {
  DocumentImportBatchResult,
  DocumentImportClient,
  DocumentImportProgress,
  DocumentImportSelection,
} from './types'

async function responseJson<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = payload?.error?.message || payload?.detail?.message || '文档导入请求失败'
    throw new Error(message)
  }
  return payload as T
}

export function createDocumentImportClient(
  basePath = '/api/document-import',
): DocumentImportClient {
  const json = <T>(path: string, init?: RequestInit) => fetch(`${basePath}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  }).then(responseJson<T>)

  return {
    async select(files: File[]): Promise<DocumentImportSelection> {
      const body = new FormData()
      files.forEach(file => body.append('files', file))
      return fetch(`${basePath}/selections`, { method: 'POST', body })
        .then(responseJson<DocumentImportSelection>)
    },
    preflight: selectionId => json<DocumentImportSelection>(
      `/selections/${encodeURIComponent(selectionId)}/preflight`,
      { method: 'POST', body: '{}' },
    ),
    startBatch: (selectionId, selectedFileIds) => json<DocumentImportProgress>(
      `/selections/${encodeURIComponent(selectionId)}/batches`,
      {
        method: 'POST',
        body: JSON.stringify({ selected_file_ids: selectedFileIds }),
      },
    ),
    progress: batchId => json<DocumentImportProgress>(
      `/batches/${encodeURIComponent(batchId)}/progress`,
    ),
    result: batchId => json<DocumentImportBatchResult | null>(
      `/batches/${encodeURIComponent(batchId)}/result`,
    ),
    cancel: batchId => json<DocumentImportProgress>(
      `/batches/${encodeURIComponent(batchId)}/cancel`,
      { method: 'POST', body: '{}' },
    ),
    resume: batchId => json<DocumentImportProgress>(
      `/batches/${encodeURIComponent(batchId)}/resume`,
      { method: 'POST', body: '{}' },
    ),
  }
}
