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

export async function sendLearningRunMessage(runId: string, message: string, providerSessionId?: string) {
  const response = await fetch(`/api/learning-runs/${runId}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message, ...(providerSessionId ? { provider_session_id: providerSessionId } : {}), idempotency_key: `tutor-${runId}-${Date.now()}` }) })
  const data = await response.json(); if (!response.ok) throw new Error(data.error?.message || '导师回复失败'); return data
}

export async function submitQuickQuiz(runId: string, quizId: string, optionId: string) {
  const response = await fetch(`/api/learning-runs/${runId}/quick-quiz`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quiz_id: quizId, option_id: optionId }) })
  const data = await response.json(); if (!response.ok) throw new Error(data.error?.message || '小测提交失败'); return data
}
