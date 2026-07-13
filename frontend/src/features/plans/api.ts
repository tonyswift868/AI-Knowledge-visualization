import { apiRequest } from '../../api/client'

export const planApi = {
  get: (planId: string) => apiRequest<any>(`/plans/${planId}`),
  replan: (planId: string, changes: Record<string, unknown>) => apiRequest<any>(`/plans/${planId}/replan`, { method: 'POST', body: JSON.stringify(changes) }),
}
