import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActiveTeachingChainView } from "../features/active-teaching/ActiveTeachingChainView";


const snapshot = {
  workspace_id: "generic-active",
  local_knowledge_base: {
    status: "ready",
    retrieval_mode: "local_hybrid_v1",
    network_required: false,
    workspace_isolated: true,
    material_count: 1,
    chunk_count: 3,
    indexed_characters: 860,
  },
  materials: [{
    id: "material_1",
    original_filename: "uploaded-material.md",
    size_bytes: 1200,
    sha256: "a".repeat(64),
    chunk_count: 3,
    parser_name: "utf8-markdown",
    status: "ready",
  }],
  receipt: {
    learning_goal: "完全掌握上传资料",
    current_level: "beginner",
    start_date: "2026-07-25",
    deadline: "2026-10-25",
    daily_task_minutes: 120,
    unavailable_dates: [],
    special_date_minutes: {},
    preferred_learning_style: "主动讲解",
  },
  knowledge_graph: {
    assessment: {
      scope: "selected_materials",
      source: { material_count: 1, chunk_count: 3, source_characters: 860 },
      knowledge_structure: {
        knowledge_point_count: 1,
        dependency_count: 1,
        isolated_point_count: 0,
        isolated_ratio: 0,
        evidence_coverage_ratio: 1,
      },
      workload: {
        total_mastery_task_minutes: 50,
        initial_learning_task_minutes: 40,
        review_task_minutes: 10,
      },
      readiness: { status: "usable_with_review", risks: ["建议语义复核。"] },
    },
    knowledge_points: [{
      id: "kp_1",
      name: "资料内知识点",
      difficulty: "中等",
      estimated_task_minutes: 40,
      current_mastery: 0,
    }],
    edges: [{ prerequisite_id: "kp_0", dependent_id: "kp_1" }],
  },
  plan: {
    id: "plan_1",
    daily_task_minutes_budget: 120,
    feasible: 1,
    unscheduled_task_minutes: 0,
    warning: "",
    total_mastery_task_minutes: 50,
    initial_learning_task_minutes: 40,
    projected_review_task_minutes: 10,
    initial_learning_ratio: 0.8,
    projected_review_ratio: 0.2,
    expected_actual_elapsed_minutes_range: { min: 58, max: 80 },
    tasks: [{
      id: "task_1",
      task_type: "initial_learning",
      estimated_task_minutes: 40,
      completed_task_minutes: 0,
      actual_elapsed_minutes: 0,
    }],
    calendar: [{
      date: "2026-07-25",
      availability_reason: "regular",
      capacity_minutes: 120,
      planned_task_minutes: 40,
      expected_actual_elapsed_minutes_range: { min: 46, max: 64 },
      tasks: [{
        id: "task_1",
        title: "资料内知识点 · 理解",
        task_type: "initial_learning",
        estimated_task_minutes: 40,
        status: "planned",
      }],
    }, {
      date: "2026-07-26",
      availability_reason: "regular",
      study_phase: "review",
      capacity_minutes: 120,
      planned_task_minutes: 0,
      projected_review_task_minutes: 12,
      projected_review_items: [{
        knowledge_point_id: "kp_1",
        title: "资料内知识点",
        projected_review_task_minutes: 12,
      }],
      tasks: [],
    }],
  },
  session: {
    session_id: "session_1",
    actual_elapsed_minutes: 0.2,
    state: {
      current_teaching_step: "remediation_explain",
      route_index: 0,
      route_length: 1,
      today_remaining_task_minutes: 120,
      remediation_stack: [{ route_index: 0 }],
      mastery_evidence: [],
      replan_history: [],
    },
    messages: [{
      id: "message_1",
      role: "teacher",
      action: "remediation_explain",
      content: "已暂停主路线并保存位置。",
      citations: [{
        chunk_id: "chunk_1",
        quote: "来自用户上传资料的原文。",
        paragraph_start: 2,
        char_start: 20,
        char_end: 60,
        content_hash: "b".repeat(64),
      }],
    }],
  },
  completion_report: {
    status: "in_progress",
    verified_complete: false,
    time: {
      planned_task_minutes: 40,
      completed_task_minutes: 0,
      actual_elapsed_minutes: 0,
      variance_minutes: 0,
      variance_reason: "标准任务分钟与真实耗时分别记录。",
    },
    gates: {
      all_initial_tasks_completed: false,
      no_unresolved_confusions: false,
    },
    mistake_queue: [{
      submission_id: "submission_1",
      problem_title: "资料内练习",
      status: "wrong_answer",
      passed_count: 2,
      total_count: 4,
      resolved: false,
    }],
  },
};


describe("ActiveTeachingChainView", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders generic traceability, separated time fields, and persisted remediation state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
    render(<ActiveTeachingChainView workspaceId="generic" />);

    await waitFor(() => expect(screen.getByTestId("active-material-count")).toHaveTextContent("资料 1 份"));
    expect(screen.getByTestId("active-local-knowledge-base")).toHaveTextContent("当前任务 RAG：可用");
    expect(screen.getByTestId("active-local-knowledge-base")).toHaveTextContent("3 个可引用分块");
    expect(screen.getByText(/预留复习 12 分钟/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /建立一次任务/ })).toBeInTheDocument();
    expect(screen.getByTestId("active-point-count")).toHaveTextContent("1");
    expect(screen.getByTestId("active-edge-count")).toHaveTextContent("1");
    expect(screen.getByTestId("active-knowledge-map")).toHaveTextContent("资料内知识点");
    expect(screen.getByTestId("active-material-assessment")).toHaveTextContent(
      "通用资料评估",
    );
    expect(screen.getByTestId("active-material-assessment")).toHaveTextContent(
      "证据覆盖 100%",
    );
    expect(screen.getByTestId("active-plan-budget")).toHaveTextContent("120");
    expect(screen.getByText(/完全掌握预计 50 任务分钟/)).toBeInTheDocument();
    expect(screen.getAllByText(/完成任务分钟 0/).length).toBeGreaterThan(0);
    expect(screen.getByText(/实际耗时分钟 0.000/)).toBeInTheDocument();
    expect(screen.getByTestId("active-expected-elapsed-range")).toHaveTextContent(
      "58–80",
    );
    expect(screen.getByTestId("active-plan-calendar")).toHaveTextContent(
      "资料内知识点 · 理解",
    );
    expect(screen.getByTestId("active-session-state")).toHaveTextContent("补救栈 1");
    expect(screen.getByTestId("active-latest-teacher")).toHaveTextContent("字符 20-60");
    expect(screen.getByTestId("active-completion-report")).toHaveTextContent(
      "仍在学习或验证中",
    );
    expect(screen.getByTestId("active-mistake-queue")).toHaveTextContent("待重做");
  });

  it("sends an explicit understood signal to the persisted state machine", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    await screen.findByTestId("active-understood");
    fireEvent.click(screen.getByTestId("active-understood"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/sessions/session_1/advance",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"understood":true'),
        }),
      ),
    );
  });

  it("submits generic learning conditions without any course-specific fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    await screen.findByTestId("active-save-receipt");
    expect(screen.queryByTestId("active-daily-minutes")).not.toBeInTheDocument();
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent("系统分配每日投入");
    fireEvent.change(screen.getByTestId("active-teacher-persona"), {
      target: { value: "耐心、有趣，先用生活例子" },
    });
    fireEvent.click(screen.getByTestId("active-save-receipt"));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/receipt"));
      expect(call).toBeTruthy();
      const body = JSON.parse(call![1].body);
      expect(body.target_level).toBe("complete_mastery");
      expect(body.allocation_mode).toBe("system");
      expect(body.initial_learning_period_ratio).toBe(0.5);
      expect(body.teacher_persona).toBe("耐心、有趣，先用生活例子");
      expect(body.daily_task_minutes).toBeUndefined();
      expect(body.material_ids).toEqual(["material_1"]);
      expect(JSON.stringify(body)).not.toMatch(/leetcode|hot150|面试经典/iu);
    });
  });

  it("turns one uploaded arbitrary file into receipt, knowledge map, and plan automatically", async () => {
    const emptySnapshot = {
      ...snapshot,
      materials: [],
      receipt: null,
      knowledge_graph: { knowledge_points: [], edges: [] },
      plan: null,
      session: null,
    };
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      let payload: any = emptySnapshot;
      if (String(url).endsWith("/materials") && init?.method === "POST") {
        payload = { id: "uploaded_generic_1", status: "ready" };
      }
      return Promise.resolve({ ok: true, json: async () => payload });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    const input = await screen.findByTestId("active-material-file");
    fireEvent.change(input, {
      target: {
        files: [new File(["# 任意主题\n正文"], "arbitrary.md", { type: "text/markdown" })],
      },
    });
    fireEvent.click(screen.getByTestId("active-upload"));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.endsWith("/materials"))).toBe(true);
      expect(urls.some((url) => url.endsWith("/receipt"))).toBe(true);
      expect(urls.some((url) => url.endsWith("/knowledge-map/generate"))).toBe(true);
      expect(urls.some((url) => url.endsWith("/mastery-plans/generate"))).toBe(true);
      const receiptCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/receipt"));
      expect(JSON.parse(receiptCall![1].body).material_ids).toEqual(["uploaded_generic_1"]);
    });
  });

  it("switches durable tasks and enters a clicked calendar task", async () => {
    const index = {
      items: [{
        workspace_id: "generic-active",
        title: "掌握任意上传资料",
        archived: false,
        status: "learning",
        material_count: 1,
        knowledge_point_count: 1,
        plan_id: "plan_1",
        session_id: "session_1",
        task_count: 1,
        completed_task_count: 0,
        next_task: snapshot.plan.calendar[0].tasks[0],
        updated_at: "2026-07-25T00:00:00Z",
      }],
    };
    const focused = {
      ...snapshot.session,
      state: {
        ...snapshot.session.state,
        active_task_id: "task_1",
        autonomy: { awaiting_learner: true, reason: "calendar_task_selected" },
      },
    };
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const value = String(url);
      let payload: any = snapshot;
      if (value === "/api/active-teaching/workspaces?include_archived=false") payload = index;
      if (value.endsWith("/sessions/start") && init?.method === "POST") payload = snapshot.session;
      if (value.endsWith("/focus") && init?.method === "POST") payload = focused;
      return Promise.resolve({ ok: true, json: async () => payload });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    expect(await screen.findByTestId("active-task-hub")).toHaveTextContent("掌握任意上传资料");
    fireEvent.click(await screen.findByTestId("active-calendar-task-task_1"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/sessions/session_1/focus",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ task_id: "task_1" }),
        }),
      ),
    );
  });

  it("archives a learning task from the task rail without deleting it", async () => {
    const index = {
      items: [{
        workspace_id: "generic-active",
        title: "可整理的学习任务",
        archived: false,
        status: "planned",
        material_count: 1,
        knowledge_point_count: 1,
        plan_id: "plan_1",
        session_id: null,
        task_count: 1,
        completed_task_count: 0,
        next_task: snapshot.plan.calendar[0].tasks[0],
        updated_at: "2026-07-25T00:00:00Z",
      }],
    };
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const value = String(url);
      const payload = value.includes("/workspaces?") ? index : snapshot;
      return Promise.resolve({ ok: true, json: async () => payload });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    fireEvent.click(await screen.findByTestId("active-task-archive-generic-active"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/workspaces/generic-active",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ archived: true }),
        }),
      ),
    );
  });

  it("restores a verified code draft and runs the real practice endpoint", async () => {
    const problem = {
      id: "problem_1",
      title: "实现求和",
      language: "python",
      test_summary: { sample: 1, boundary: 2, hidden: 1 },
    };
    const accepted = {
      status: "accepted",
      passed_count: 4,
      total_count: 4,
      hidden_passed_count: 1,
      hidden_total_count: 1,
      duration_ms: 2,
      results: [
        { ordinal: 0, kind: "sample", passed: true },
        { ordinal: 3, kind: "hidden", passed: true },
      ],
    };
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      let payload: any = snapshot;
      if (String(url).endsWith("/problems")) {
        payload = { items: [problem] };
      } else if (String(url).endsWith("/practice")) {
        payload = {
          draft: { code: "def solve(values):\n    return sum(values)", revision: 2 },
          submissions: [],
        };
      } else if (String(url).endsWith("/run") && init?.method === "POST") {
        payload = accepted;
      }
      return Promise.resolve({ ok: true, json: async () => payload });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const editor = await screen.findByTestId("active-code-editor");
    await waitFor(() =>
      expect(editor).toHaveValue("def solve(values):\n    return sum(values)"),
    );
    fireEvent.click(screen.getByTestId("active-run-code"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/sessions/session_1/problems/problem_1/run",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(await screen.findByTestId("active-code-result")).toHaveTextContent(
      "通过 4/4",
    );
  });
});
