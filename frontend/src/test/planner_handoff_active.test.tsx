import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../features/voice/localTeacherVoice", () => ({
  speakLocalTeacherVoice: vi.fn(),
  stopLocalTeacherVoice: vi.fn(),
}));

import { ActiveTeachingChainView } from "../features/active-teaching/ActiveTeachingChainView";

const emptyGraph = { knowledge_points: [], edges: [], learning_resources: [], warnings: [] };
const map = {
  map_id: "knowledge-map:planner",
  revision_id: "kmr_planner",
  revision: 1,
  status: "active",
  nodes: [{
    id: "planner-point",
    name: "选中资料知识点",
    explanation: "来自选中资料",
    difficulty: "medium",
    layer: "material",
    status: "confirmed",
    evidence: [{ source_id: "planner-material", chunk_id: "chunk-1", quote: "资料证据" }],
  }],
  edges: [],
  candidates: [],
  warnings: [],
};
const preview = {
  preview_id: "preview_planner",
  revision_id: "kmr_planner",
  revision: 1,
  tasks: [{ id: "planner-task", task_type: "initial_learning", knowledge_point_id: "planner-point", estimated_task_minutes: 30, completed_task_minutes: 0, actual_elapsed_minutes: 0 }],
  days: [{ date: "2026-08-15", study_phase: "initial_learning", capacity_minutes: 120, tasks: [] }],
  activation_blockers: [],
  extension_draft: { requires_confirmation: false },
  daily_task_minutes_budget: 120,
};

const snapshotFor = (workspaceId: string, created: boolean, planned: boolean) => ({
  workspace_id: workspaceId,
  local_knowledge_base: { status: created ? "ready" : "waiting", material_count: created ? 1 : 0, chunk_count: created ? 1 : 0 },
  materials: created ? [{ id: "planner-material", original_filename: "选中资料.txt", status: "ready", chunk_count: 1, sha256: "a".repeat(64), size_bytes: 12 }] : [],
  receipt: created ? {
    learning_goal: "完成互联网面试准备",
    current_level: "beginner",
    start_date: "2026-08-15",
    deadline: "2026-09-05",
    review_rounds: 2,
    study_weekdays: [0, 1, 2, 3, 4, 5, 6],
    preferred_learning_style: "主动讲解",
    material_ids: ["planner-material"],
    discover_external_resources: false,
  } : null,
  knowledge_graph: planned ? { ...emptyGraph, knowledge_points: [{ id: "planner-point", name: "选中资料知识点", difficulty: "中等", estimated_task_minutes: 30, current_mastery: 0 }], extraction: { mode: "provider_grounded" } } : emptyGraph,
  plan: planned ? { id: "plan_planner", daily_task_minutes_budget: 120, feasible: true, unscheduled_task_minutes: 0, tasks: preview.tasks, calendar: preview.days } : null,
  session: null,
});

describe("planner handoff task creation", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("imports the selected URL into a new workspace and keeps the existing task", async () => {
    const oldWorkspaceId = "leetcode-old-active";
    const today = new Date();
    const isoDate = (value: Date) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
    const expectedStart = isoDate(today);
    const expectedDeadline = isoDate(new Date(today.getTime() + 21 * 86400000));
    localStorage.setItem("mentorflow_active_teaching_workspace_v1", oldWorkspaceId);
    localStorage.setItem("mentorflow_planner_handoff_v1", JSON.stringify({
      goal: "完成互联网面试准备",
      resource: { url: "https://example.test/interview", title: "面试资料" },
      created_at: "2026-08-15T00:00:00Z",
    }));
    let created = false;
    let planned = false;
    let newWorkspaceId = "";
    const calls: Array<{ endpoint: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const endpoint = String(url);
      calls.push({ endpoint, init });
      const workspaceMatch = endpoint.match(/\/workspaces\/([^/]+)/);
      const workspaceId = workspaceMatch ? decodeURIComponent(workspaceMatch[1]) : "";
      let payload: any = workspaceId ? snapshotFor(workspaceId, created, planned) : {};
      if (endpoint.includes("/workspaces?") || endpoint.includes("/workspaces?include_archived")) {
        payload = {
          items: [
            { workspace_id: oldWorkspaceId, title: "LeetCode 旧任务", archived: false, status: "learning", task_count: 1, completed_task_count: 0, knowledge_point_count: 1, completed_knowledge_point_count: 0 },
            ...(newWorkspaceId ? [{ workspace_id: newWorkspaceId, title: "完成互联网面试准备", archived: false, status: "planned", task_count: 1, completed_task_count: 0, knowledge_point_count: 1, completed_knowledge_point_count: 0 }] : []),
          ],
        };
      } else if (endpoint.endsWith("/teacher-personas")) {
        payload = { items: [] };
      } else if (endpoint.endsWith("/material-capabilities")) {
        payload = { capabilities: [] };
      } else if (endpoint.includes("/knowledge-map?revision=active")) {
        payload = planned ? map : { ...map, nodes: [] };
      } else if (endpoint.includes("/knowledge-map/candidates?revision=active")) {
        payload = { candidates: [] };
      } else if (endpoint.endsWith("/problems")) {
        payload = { items: [] };
      } else if (endpoint.endsWith("/materials/url") && init?.method === "POST") {
        newWorkspaceId = workspaceId;
        created = true;
        payload = { id: "planner-material", status: "ready", chunk_count: 1 };
      } else if (endpoint.endsWith("/receipt") && init?.method === "PUT") {
        payload = { ok: true };
      } else if (endpoint.endsWith("/knowledge-map/jobs") && init?.method === "POST") {
        payload = { id: "planner-job", status: "ready", revision_id: "kmr_planner" };
      } else if (endpoint.includes("/knowledge-map/jobs/")) {
        payload = { id: "planner-job", status: "ready", revision_id: "kmr_planner" };
      } else if (endpoint.endsWith("/mastery-plans/preview") && init?.method === "POST") {
        planned = true;
        payload = preview;
      }
      return { ok: true, json: async () => payload };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ActiveTeachingChainView workspaceId="general-learning" createRequest={1} />);
    await screen.findByTestId("planner-resource-handoff");

    const start = screen.getByTestId("active-start-date") as HTMLInputElement;
    const deadline = screen.getByTestId("active-deadline") as HTMLInputElement;
    expect(start.value).toBe(expectedStart);
    expect(new Date(deadline.value).getTime() - new Date(start.value).getTime()).toBe(21 * 86400000);
    expect(screen.queryByTestId("active-material-file")).not.toBeInTheDocument();
    expect(screen.queryByTestId("active-foundation")).not.toBeInTheDocument();
    expect(screen.queryByTestId("active-review-rounds")).not.toBeInTheDocument();
    expect(screen.queryByTestId("active-discover-external-resources")).not.toBeInTheDocument();

    fireEvent.change(screen.getByTestId("active-goal"), { target: { value: "完成互联网面试准备" } });
    fireEvent.click(screen.getByTestId("active-upload"));

    await waitFor(() => expect(calls.some((item) => item.endpoint.endsWith("/mastery-plans/preview"))).toBe(true));
    const urlCall = calls.find((item) => item.endpoint.endsWith("/materials/url"));
    expect(urlCall).toBeDefined();
    expect(newWorkspaceId).not.toBe(oldWorkspaceId);
    expect(newWorkspaceId).toMatch(/^learning-task-/);
    expect(JSON.parse(String(calls.find((item) => item.endpoint.endsWith("/receipt"))?.init?.body))).toEqual(expect.objectContaining({
      learning_goal: "完成互联网面试准备",
      deadline: expectedDeadline,
      material_ids: ["planner-material"],
      discover_external_resources: false,
    }));
    await waitFor(() => expect(screen.getByTestId(`active-task-switch-${oldWorkspaceId}`)).toBeInTheDocument());
    expect(screen.getByTestId(`active-task-switch-${newWorkspaceId}`)).toBeInTheDocument();
  });
});
