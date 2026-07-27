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
    review_rounds: 2,
    unavailable_dates: [],
    special_date_minutes: {},
    preferred_learning_style: "主动讲解",
    allocation: {
      recommended_minutes_per_day: 120,
      initial_learning_minutes: 40,
      one_review_round_minutes: 10,
      projected_review_minutes: 20,
    },
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
    total_mastery_task_minutes: 60,
    initial_learning_task_minutes: 40,
    one_review_round_task_minutes: 10,
    projected_review_task_minutes: 20,
    review_rounds: 2,
    initial_learning_ratio: 0.6667,
    projected_review_ratio: 0.3333,
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
      study_phase: "initial_learning",
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
    localStorage.clear();
  });

  it("renders generic traceability, separated time fields, and persisted remediation state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
    render(<ActiveTeachingChainView workspaceId="generic" />);

    await waitFor(() => expect(screen.getByTestId("active-material-count")).toHaveTextContent("资料 1 份"));
    expect(screen.getByTestId("active-local-knowledge-base")).toHaveTextContent("当前资料库：已索引，可检索");
    expect(screen.getByTestId("active-local-knowledge-base")).toHaveTextContent("3 个可引用分块");
    expect(screen.getByTestId("active-local-knowledge-base")).toHaveTextContent(
      "不代表资料包含完整答案",
    );
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
    expect(screen.getByTestId("active-generate-plan")).toBeDisabled();
    expect(screen.getByTestId("active-generate-plan")).toHaveTextContent(
      "完整掌握计划已生成",
    );
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent(
      "每天约 120 标准任务分钟：首学 1 次 + 完整复习 2 轮",
    );
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent(
      "首学约 40 分钟",
    );
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent(
      "每轮完整复习约 10 分钟",
    );
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent(
      "不再强制按日期比例切开",
    );
    expect(screen.getByTestId("active-review-rounds")).toHaveValue("2");
    expect(screen.getByTestId("active-generate-graph")).toBeDisabled();
    expect(screen.getByTestId("active-generate-graph")).toHaveTextContent(
      "知识地图已生成",
    );
    expect(screen.getByTestId("active-save-receipt")).toBeDisabled();
    expect(screen.getByTestId("active-save-receipt")).toHaveTextContent(
      "当前设置未变化",
    );
    expect(screen.getByText(/完全掌握预计 60 任务分钟/)).toBeInTheDocument();
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
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent("系统按总任务量平均分配");
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent("完整复习 2 轮");
    fireEvent.change(screen.getByTestId("active-teacher-persona"), {
      target: { value: "耐心、有趣，先用生活例子" },
    });
    fireEvent.change(screen.getByTestId("active-review-rounds"), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByTestId("active-save-receipt"));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/receipt"));
      expect(call).toBeTruthy();
      const body = JSON.parse(call![1].body);
      expect(body.target_level).toBe("complete_mastery");
      expect(body.allocation_mode).toBe("system");
      expect(body.review_rounds).toBe(3);
      expect(body.initial_learning_period_ratio).toBeUndefined();
      expect(body.teacher_persona).toBe("耐心、有趣，先用生活例子");
      expect(body.daily_task_minutes).toBeUndefined();
      expect(body.material_ids).toEqual(["material_1"]);
      expect(JSON.stringify(body)).not.toMatch(/leetcode|hot150|面试经典/iu);
      expect(
        fetchMock.mock.calls.some(
          ([url]) => String(url).endsWith("/knowledge-map/generate"),
        ),
      ).toBe(false);
      expect(
        fetchMock.mock.calls.some(
          ([url]) => String(url).endsWith("/mastery-plans/generate"),
        ),
      ).toBe(true);
    });
  });

  it("auto-saves and restores every editable task draft field per workspace", async () => {
    const draftSnapshot = { ...snapshot, workspace_id: "draft-case-active" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => draftSnapshot });
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<ActiveTeachingChainView workspaceId="draft-case" />);
    await screen.findByTestId("active-draft-status");

    fireEvent.change(screen.getByTestId("active-goal"), {
      target: { value: "刷新后仍保留的学习目标" },
    });
    fireEvent.change(screen.getByTestId("active-teacher-persona"), {
      target: { value: "耐心但主动的教师" },
    });
    fireEvent.change(screen.getByTestId("active-learner-interests"), {
      target: { value: "游戏、做饭" },
    });

    await waitFor(() => {
      const raw = localStorage.getItem(
        "mentorflow_active_task_draft_v1:draft-case-active",
      );
      expect(raw).toContain("刷新后仍保留的学习目标");
      expect(raw).toContain("耐心但主动的教师");
      expect(raw).toContain("游戏、做饭");
    });

    view.unmount();
    render(<ActiveTeachingChainView workspaceId="draft-case" />);
    expect(await screen.findByDisplayValue("刷新后仍保留的学习目标")).toBeInTheDocument();
    expect(screen.getByDisplayValue("耐心但主动的教师")).toBeInTheDocument();
    expect(screen.getByDisplayValue("游戏、做饭")).toBeInTheDocument();
  });

  it("hydrates server conditions before creating a draft for a switched task", async () => {
    const hydratedSnapshot = {
      ...snapshot,
      workspace_id: "server-hydration-active",
      receipt: {
        ...snapshot.receipt,
        current_level: "zero_foundation",
        deadline: "2026-12-31",
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => hydratedSnapshot,
    }));
    render(<ActiveTeachingChainView workspaceId="server-hydration" />);

    expect(await screen.findByTestId("active-foundation")).toHaveValue("zero_foundation");
    expect(screen.getByTestId("active-deadline")).toHaveValue("2026-12-31");
    await waitFor(() => {
      const raw = localStorage.getItem(
        "mentorflow_active_task_draft_v1:server-hydration-active",
      );
      expect(raw).toContain('"deadline":"2026-12-31"');
      expect(raw).toContain('"level":"zero_foundation"');
    });
  });

  it("replans without rebuilding the knowledge map when foundation changes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    const foundation = await screen.findByTestId("active-foundation");
    fireEvent.change(foundation, { target: { value: "zero_foundation" } });
    expect(screen.getByTestId("active-save-receipt")).toHaveTextContent(
      "保存更改并重新排程",
    );
    fireEvent.click(screen.getByTestId("active-save-receipt"));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.endsWith("/receipt"))).toBe(true);
      expect(urls.some((url) => url.endsWith("/knowledge-map/generate"))).toBe(false);
      expect(urls.some((url) => url.endsWith("/mastery-plans/generate"))).toBe(true);
    });
  });

  it("refreshes persisted state even when replanning fails after the receipt was saved", async () => {
    let snapshotReads = 0;
    const savedAfterFailure = {
      ...snapshot,
      receipt: { ...snapshot.receipt, deadline: "2026-11-01" },
    };
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith("/snapshot")) {
        snapshotReads += 1;
        return {
          ok: true,
          json: async () => snapshotReads === 1 ? snapshot : savedAfterFailure,
        };
      }
      if (url.endsWith("/mastery-plans/generate")) {
        return {
          ok: false,
          json: async () => ({ error: { message: "重新排程失败" } }),
        };
      }
      return { ok: true, json: async () => snapshot };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    const deadlineInput = await screen.findByTestId("active-deadline");
    fireEvent.change(deadlineInput, { target: { value: "2026-11-01" } });
    fireEvent.click(screen.getByTestId("active-save-receipt"));

    await waitFor(() => {
      expect(screen.getByText("重新排程失败")).toBeInTheDocument();
      expect(snapshotReads).toBeGreaterThanOrEqual(2);
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

  it("continues task creation from an already indexed material without another file selection", async () => {
    const indexedOnlySnapshot = {
      ...snapshot,
      receipt: null,
      knowledge_graph: { knowledge_points: [], edges: [] },
      plan: null,
      session: null,
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => indexedOnlySnapshot,
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const continueButton = await screen.findByRole("button", {
      name: "使用已保存资料继续创建任务",
    });
    expect(continueButton).toBeEnabled();
    expect(screen.queryByTestId("active-save-receipt")).not.toBeInTheDocument();
    expect(screen.getByTestId("active-local-knowledge-base")).toHaveTextContent(
      "资料已经保存，但任务尚未建立",
    );
    fireEvent.click(continueButton);

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.endsWith("/receipt"))).toBe(true);
      expect(urls.some((url) => url.endsWith("/knowledge-map/generate"))).toBe(true);
      expect(urls.some((url) => url.endsWith("/mastery-plans/generate"))).toBe(true);
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).endsWith("/materials") && init?.method === "POST",
        ),
      ).toBe(false);
      const receiptCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/receipt"));
      expect(JSON.parse(receiptCall![1].body).material_ids).toEqual(["material_1"]);
    });
  });

  it("enables plan generation only when a knowledge map exists without a plan", async () => {
    const mapWithoutPlan = {
      ...snapshot,
      plan: null,
      session: null,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mapWithoutPlan,
    }));
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const button = await screen.findByTestId("active-generate-plan");
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent("生成完整掌握计划");
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
