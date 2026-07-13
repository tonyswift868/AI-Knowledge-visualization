import { apiRequest, uploadFiles } from '../../api/client'

export const sourceApi = {
  list: (workspaceId: string) => apiRequest<any[]>(`/workspaces/${workspaceId}/sources`),
  remove: (sourceId: string) => apiRequest(`/sources/${sourceId}`, { method: 'DELETE' }),
  upload: (workspaceId: string, files: File[]) => uploadFiles(`/workspaces/${workspaceId}/sources/upload`, files),
  archivePreview: (workspaceId: string, file: File) => { const form = new FormData(); form.append('file', file); return fetch(`/api/workspaces/${workspaceId}/sources/archive/preview`, { method: 'POST', body: form }).then(response => response.json()) },
  archiveImport: (workspaceId: string, payload: { preview_id: string; selected_file_ids: string[]; trust_level?: string }) => apiRequest(`/workspaces/${workspaceId}/sources/archive/import`, { method: 'POST', body: JSON.stringify(payload) }),
}
