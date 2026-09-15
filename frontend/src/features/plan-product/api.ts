import { apiRequest } from "../../api/client"
import type {
  DailyResponse,
  DemoResponse,
  PlanCreationInput,
  PlanProductClient,
  PreviewResponse,
  PublishedResponse,
  ShiftPreviewResponse,
  Weekday,
} from "./types"

const todayIso = () => new Date().toISOString().slice(0, 10)

export const planProductApi: PlanProductClient = {
  demo: () => apiRequest<DemoResponse>("/plan-product/demo"),
  createPreview: value => apiRequest<PreviewResponse>("/plan-product/previews", {
    method: "POST",
    body: JSON.stringify(value),
  }),
  publish: previewId => apiRequest<PublishedResponse>(`/plan-product/previews/${previewId}/publish`, {
    method: "POST",
    body: JSON.stringify({
      explicitly_confirmed_by_user: true,
      published_on: todayIso(),
    }),
  }),
  getPublished: publicationId => apiRequest<PublishedResponse>(`/plan-product/published/${publicationId}`),
  daily: (publicationId, today) => apiRequest<DailyResponse>(
    `/plan-product/published/${publicationId}/daily${today ? `?today=${encodeURIComponent(today)}` : ""}`,
  ),
  complete: (publicationId, taskId, completedOn) => apiRequest(
    `/plan-product/published/${publicationId}/tasks/${taskId}/complete`,
    {
      method: "POST",
      body: JSON.stringify({
        explicitly_completed_by_user: true,
        completed_on: completedOn || todayIso(),
      }),
    },
  ),
  previewShift: (publicationId, value: {
    calendar_days_delta: number
    weekly_learning_days: Weekday[]
    unavailable_dates: string[]
  }) => apiRequest<ShiftPreviewResponse>(`/plan-product/published/${publicationId}/shifts/preview`, {
    method: "POST",
    body: JSON.stringify(value),
  }),
  confirmShift: (publicationId, previewId) => apiRequest<{ current_schedule: PublishedResponse["current_schedule"] }>(
    `/plan-product/published/${publicationId}/shifts/${previewId}/confirm`,
    {
      method: "POST",
      body: JSON.stringify({
        explicitly_confirmed_by_user: true,
      }),
    },
  ),
  regenerate: (publicationId, creation) => apiRequest<PreviewResponse>(
    `/plan-product/published/${publicationId}/regenerate`,
    {
      method: "POST",
      body: JSON.stringify({
        explicitly_requested_by_user: true,
        creation,
      }),
    },
  ),
}
