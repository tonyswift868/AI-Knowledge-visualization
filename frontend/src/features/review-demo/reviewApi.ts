export type ReviewOutcome = 'recalled' | 'recalled_with_difficulty' | 'partial' | 'failed' | 'hint_dependent'

export type ReviewQuestion = {
  item_id: string
  concept_id: string
  concept_label: string
  prompt: string
  occurrence_number: number
  critical: boolean
  required: boolean
  weak_repeat: boolean
  repeat_message: string | null
  active_hint: string | null
}

export type ReviewResult = {
  item_id: string
  concept_id: string
  concept_label: string
  outcome: ReviewOutcome
  label: string
  tone: 'success' | 'caution' | 'partial' | 'error' | 'hint'
  retrieval_score: number
  feedback: string
  expected_answer: string
  explanation: string
  provider_calls: number
}

export type ReviewRoundResult = {
  round_number: number
  passed: boolean
  status_label: string
  retrieval_score: number
  critical_concepts: Array<{ concept_id: string; label: string; status: string }>
  remaining_weak_items: number
  failed_gates: string[]
}

export type ReviewCampaignView = {
  schema_version: 'review-product-v1.v1'
  campaign_id: string
  mode: 'demo'
  demo: true
  demo_label: string
  state: string
  round: { current: number; fixed_total: number; completed: number; display: string; fsrs_controls_round_count: false }
  fixture: { concept_count: number; question_count: number; round_count: number }
  question: ReviewQuestion | null
  last_result: ReviewResult | null
  round_result: ReviewRoundResult | null
  metrics: {
    retrieval_score: number
    evaluated_items: number
    target_items: number
    weak_count: number
    remaining_weak_items: number
    pass_status: 'pending' | 'passed' | 'not_passed'
    critical_concepts: Array<{ concept_id: string; label: string; status: string }>
  }
  cost: { provider_calls: number; local_only_operations: number; mode: 'local_only' }
  campaign_result: null | {
    completed_rounds: number
    passed_rounds: number
    all_rounds_passed: boolean
    retrieval_score: number
    critical_pass: boolean
    remaining_weak_items: number
  }
  resume: { recoverable: boolean; storage_key: string }
  hint?: { hint_id: string; text: string; source: string; provider_calls: number }
  explanation?: { text: string; source: string; provider_calls: number; explicit_request: boolean }
}

const REVIEW_API_BASE = (import.meta.env.VITE_REVIEW_API_BASE as string | undefined)
  ?? 'http://127.0.0.1:8001/api/review'

async function reviewRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${REVIEW_API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data?.error?.message ?? '复习服务暂时不可用')
  }
  return data as T
}

export const reviewApi = {
  create: () => reviewRequest<ReviewCampaignView>('/campaigns', { method: 'POST', body: JSON.stringify({ mode: 'demo' }) }),
  get: (campaignId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}`),
  startRound: (campaignId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}/rounds/start`, { method: 'POST' }),
  nextQuestion: (campaignId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}/questions/next`, { method: 'POST' }),
  submit: (campaignId: string, answer: string, submissionId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}/answers`, {
    method: 'POST',
    body: JSON.stringify({ answer, submission_id: submissionId }),
  }),
  hint: (campaignId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}/hints`, { method: 'POST' }),
  explain: (campaignId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}/explanations`, {
    method: 'POST',
    body: JSON.stringify({ question: '为什么？' }),
  }),
  pause: (campaignId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}/pause`, { method: 'POST' }),
  resume: (campaignId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}/resume`, { method: 'POST' }),
  finish: (campaignId: string) => reviewRequest<ReviewCampaignView>(`/campaigns/${campaignId}/finish`, { method: 'POST' }),
}

export const REVIEW_CAMPAIGN_STORAGE_KEY = 'mentorflow_review_campaign_v1'
