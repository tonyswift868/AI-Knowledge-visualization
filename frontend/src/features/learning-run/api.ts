export async function createLearningRun(taskId: string) {
  const response = await fetch(`/api/tasks/${taskId}/learning-runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  const data = await response.json(); if (!response.ok) throw new Error(data.error?.message || '学习阶段启动失败'); return data
}
export async function submitLearningRun(runId: string, body: Record<string, unknown>) {
  const response = await fetch(`/api/learning-runs/${runId}/submit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await response.json(); if (!response.ok) throw new Error(data.error?.message || '学习阶段提交失败'); return data
}
export async function requestLearningHint(runId: string) {
  const response = await fetch(`/api/learning-runs/${runId}/request-hint`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  const data = await response.json(); if (!response.ok) throw new Error(data.error?.message || '提示请求失败'); return data
}
