import { request } from "../../providerConnection";

export type MasteryStatus = "learning" | "self_attested" | "verified";
export type TimelineCategory = "teaching" | "assessment" | "review" | "verified" | "weak";

export type RecentAttempt = {
  event_id: string;
  occurred_at: string;
  result: string;
  evidence_ids: string[];
};

export type MemoryDiagnostics = {
  difficulty: number | null;
  stability: number | null;
  retrievability: number | null;
  retrievability_calculated_at: string | null;
  scheduler_version: string | null;
  state_version: number | null;
  memory_is_mastery: false;
};

export type ConceptProgress = {
  concept_id: string;
  label: string;
  mastery: {
    status: MasteryStatus;
    explanation: string;
    self_attested_event_count: number;
    verified_event_count: number;
    assessment_evidence_count: number;
    failed_assessment_count: number;
    misconception_count: number;
    source_event_ids: string[];
    verification_evidence_ids: string[];
  };
  review: {
    item_count: number;
    review_count: number;
    next_due_at: string | null;
    recent_result: string | null;
    recent_rating: string | null;
    recent_reviewed_at: string | null;
    failure_streak: number;
    hint_dependency: "none" | "recent" | "persistent";
    is_due: boolean;
    is_weak: boolean;
    explanation: string;
    advanced_diagnostics: MemoryDiagnostics;
  };
  recent_attempts: RecentAttempt[];
  active_misconceptions: string[];
  last_activity_at: string | null;
};

export type TimelineEntry = {
  entry_id: string;
  concept_id: string;
  concept_label: string;
  category: TimelineCategory;
  occurred_at: string;
  title: string;
  explanation: string;
  evidence_ids: string[];
};

export type ProgressOverview = {
  schema_version: "mentorflow.progress-read.v1";
  student_id: string;
  session_id: string;
  generated_at: string;
  demo: boolean;
  demo_label: string | null;
  mastery_semantics: "learning_self_attested_verified_from_student_evidence";
  memory_semantics: "fsrs_memory_state_not_mastery";
  privacy_scope: "learning_evidence_only";
  timeline_categories: TimelineCategory[];
  summary: {
    concept_count: number;
    learning_count: number;
    self_attested_count: number;
    verified_count: number;
    due_count: number;
    weak_count: number;
    recent_attempt_count: number;
  };
  concepts: ConceptProgress[];
  due_queue: Array<{
    item_id: string;
    concept_id: string;
    concept_label: string;
    due_at: string | null;
    failure_streak: number;
    hint_dependency: string;
    reason: string;
  }>;
  weak_queue: Array<{
    item_id: string;
    concept_id: string;
    concept_label: string;
    due_at: string | null;
    failure_streak: number;
    hint_dependency: string;
    reason: string;
  }>;
  recent_activity: TimelineEntry[];
};

export type ConceptProgressDetail = {
  schema_version: "mentorflow.progress-read.v1";
  student_id: string;
  session_id: string;
  generated_at: string;
  demo: boolean;
  concept: ConceptProgress;
  evidence_timeline: TimelineEntry[];
};

export type ProgressExperienceApi = {
  overview(studentId: string, sessionId: string, demo: boolean): Promise<ProgressOverview>;
  concept(studentId: string, sessionId: string, conceptId: string, demo: boolean): Promise<ConceptProgressDetail>;
};

export const progressExperienceApi: ProgressExperienceApi = {
  overview(studentId, sessionId, demo) {
    return request<ProgressOverview>(
      demo
        ? "/progress/demo"
        : `/progress/students/${encodeURIComponent(studentId)}/sessions/${encodeURIComponent(sessionId)}`,
    );
  },
  concept(studentId, sessionId, conceptId, demo) {
    return request<ConceptProgressDetail>(
      demo
        ? `/progress/demo/concepts/${encodeURIComponent(conceptId)}`
        : `/progress/students/${encodeURIComponent(studentId)}/sessions/${encodeURIComponent(sessionId)}/concepts/${encodeURIComponent(conceptId)}`,
    );
  },
};
