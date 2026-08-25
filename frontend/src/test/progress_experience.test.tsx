import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProgressExperience } from "../features/progress/ProgressExperience";
import type {
  ConceptProgress,
  ConceptProgressDetail,
  ProgressOverview,
  TimelineEntry,
} from "../features/progress/api";


const concept = (values: Partial<ConceptProgress> & Pick<ConceptProgress, "concept_id" | "label">): ConceptProgress => ({
  concept_id: values.concept_id,
  label: values.label,
  mastery: values.mastery || {
    status: "learning",
    explanation: "仍在学习：尚未形成带证据的独立验证记录。",
    self_attested_event_count: 0,
    verified_event_count: 0,
    assessment_evidence_count: 1,
    failed_assessment_count: 1,
    misconception_count: 1,
    source_event_ids: ["event-1"],
    verification_evidence_ids: [],
  },
  review: values.review || {
    item_count: 1,
    review_count: 2,
    next_due_at: "2026-08-25T12:00:00Z",
    recent_result: "hint_dependent",
    recent_rating: "again",
    recent_reviewed_at: "2026-08-24T12:00:00Z",
    failure_streak: 2,
    hint_dependency: "recent",
    is_due: true,
    is_weak: true,
    explanation: "近期失败或提示依赖使该概念进入薄弱队列；掌握状态保持独立。",
    advanced_diagnostics: {
      difficulty: 6.2,
      stability: 1.4,
      retrievability: 0.41,
      retrievability_calculated_at: "2026-08-24T12:00:00Z",
      scheduler_version: "6.3.2",
      state_version: 3,
      memory_is_mastery: false,
    },
  },
  recent_attempts: values.recent_attempts || [{
    event_id: "attempt-1",
    occurred_at: "2026-08-24T10:00:00Z",
    result: "assessment_failed",
    evidence_ids: ["evidence-1"],
  }],
  active_misconceptions: values.active_misconceptions || ["需要区分冲突与哈希函数失效"],
  last_activity_at: "2026-08-24T12:00:00Z",
});

const hashConcept = concept({ concept_id: "hash-collision", label: "哈希冲突处理" });
const verifiedConcept = concept({
  concept_id: "binary-search-boundary",
  label: "二分查找边界",
  mastery: {
    status: "verified",
    explanation: "已有 1 条带证据的独立验证记录；复习表现不会替代这些证据。",
    self_attested_event_count: 0,
    verified_event_count: 1,
    assessment_evidence_count: 2,
    failed_assessment_count: 0,
    misconception_count: 0,
    source_event_ids: ["event-2"],
    verification_evidence_ids: ["evidence-verified"],
  },
  review: {
    ...hashConcept.review,
    recent_result: "recalled",
    recent_rating: "good",
    failure_streak: 0,
    hint_dependency: "none",
    is_weak: false,
  },
  active_misconceptions: [],
});

const categories = ["teaching", "assessment", "review", "verified", "weak"] as const;
const timeline: TimelineEntry[] = categories.map((category, index) => ({
  entry_id: `timeline-${category}`,
  concept_id: "hash-collision",
  concept_label: "哈希冲突处理",
  category,
  occurred_at: `2026-08-24T0${index + 1}:00:00Z`,
  title: `${category} 记录`,
  explanation: "这是可解释的学习证据。",
  evidence_ids: index === 3 ? ["evidence-verified"] : [],
}));

const overview: ProgressOverview = {
  schema_version: "mentorflow.progress-read.v1",
  student_id: "DEMO-student-progress",
  session_id: "DEMO-session-progress",
  generated_at: "2026-08-25T12:00:00Z",
  demo: true,
  demo_label: "DEMO 学生 · 数据仅用于产品演示",
  mastery_semantics: "learning_self_attested_verified_from_student_evidence",
  memory_semantics: "fsrs_memory_state_not_mastery",
  privacy_scope: "learning_evidence_only",
  timeline_categories: [...categories],
  summary: {
    concept_count: 2,
    learning_count: 1,
    self_attested_count: 0,
    verified_count: 1,
    due_count: 2,
    weak_count: 1,
    recent_attempt_count: 2,
  },
  concepts: [hashConcept, verifiedConcept],
  due_queue: [{ item_id: "review-hash", concept_id: "hash-collision", concept_label: "哈希冲突处理", due_at: "2026-08-25T12:00:00Z", failure_streak: 2, hint_dependency: "recent", reason: "复习已到期" }],
  weak_queue: [{ item_id: "review-hash", concept_id: "hash-collision", concept_label: "哈希冲突处理", due_at: "2026-08-25T12:00:00Z", failure_streak: 2, hint_dependency: "recent", reason: "近期失败或提示依赖" }],
  recent_activity: timeline,
};

const detail: ConceptProgressDetail = {
  schema_version: "mentorflow.progress-read.v1",
  student_id: overview.student_id,
  session_id: overview.session_id,
  generated_at: overview.generated_at,
  demo: true,
  concept: hashConcept,
  evidence_timeline: timeline,
};

afterEach(cleanup);

describe("ProgressExperience", () => {
  it("separates evidence mastery from memory diagnostics without a mastery percentage", async () => {
    const api = {
      overview: vi.fn().mockResolvedValue(overview),
      concept: vi.fn().mockResolvedValue(detail),
    };
    render(<ProgressExperience studentId={overview.student_id} sessionId={overview.session_id} demo api={api} />);

    await waitFor(() => expect(screen.getByText("DEMO 学生 · 数据仅用于产品演示")).toBeInTheDocument());
    expect(screen.getAllByText("学习中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("独立验证").length).toBeGreaterThan(0);
    expect(screen.getByText(/记忆调度与掌握等级分开显示/)).toBeInTheDocument();
    expect(screen.queryByText(/掌握.*%/)).not.toBeInTheDocument();
    expect(screen.getByText("高级记忆诊断").parentElement).not.toHaveAttribute("open");

    fireEvent.click(screen.getByText("高级记忆诊断"));
    expect(screen.getByText(/Retrievability 仅为记忆提取估计/)).toBeVisible();
  });

  it("shows all evidence timeline categories and loads concept detail on selection", async () => {
    const binaryDetail: ConceptProgressDetail = { ...detail, concept: verifiedConcept, evidence_timeline: [] };
    const api = {
      overview: vi.fn().mockResolvedValue(overview),
      concept: vi.fn().mockImplementation(async (_student: string, _session: string, conceptId: string) => conceptId === "hash-collision" ? detail : binaryDetail),
    };
    render(<ProgressExperience studentId={overview.student_id} sessionId={overview.session_id} demo api={api} />);

    const timelineRegion = await screen.findByRole("region", { name: "概念证据时间线" });
    await waitFor(() => expect(within(timelineRegion).getByText("教学")).toBeInTheDocument());
    for (const label of ["教学", "测评", "复习", "验证", "薄弱"]) {
      expect(within(timelineRegion).getByText(label)).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole("button", { name: /二分查找边界/ }));
    await waitFor(() => expect(api.concept).toHaveBeenLastCalledWith(
      overview.student_id,
      overview.session_id,
      "binary-search-boundary",
      true,
    ));
    await waitFor(() => expect(screen.getByText(/已有 1 条带证据的独立验证记录/)).toBeInTheDocument());
  });

  it("states the privacy boundary and provides due and weak queues", async () => {
    const api = {
      overview: vi.fn().mockResolvedValue(overview),
      concept: vi.fn().mockResolvedValue(detail),
    };
    render(<ProgressExperience studentId={overview.student_id} sessionId={overview.session_id} demo api={api} />);

    await screen.findByText("到期 / 薄弱");
    expect(screen.getByText("2 / 1")).toBeInTheDocument();
    expect(screen.getByText("这里只显示教学所需的学习证据，不创建人格或心理画像。")).toBeInTheDocument();
  });
});
