import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const localTeacherVoiceMocks = vi.hoisted(() => ({
  speak: vi.fn(),
  stop: vi.fn(),
}));

vi.mock("../features/voice/localTeacherVoice", () => ({
  speakLocalTeacherVoice: localTeacherVoiceMocks.speak,
  stopLocalTeacherVoice: localTeacherVoiceMocks.stop,
}));

import {
  ActiveTeachingChainView,
  resolveActiveTeacherPresentationState,
  teacherMessageSegments,
} from "../features/active-teaching/ActiveTeachingChainView";
import { useMentorStore } from "../stores/useMentorStore";


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

describe("teacher emphasis markup", () => {
  it("turns explicit focus markers and concise markdown bold into safe focus segments", () => {
    expect(
      teacherMessageSegments(
        "先记住[[重点:循环不变量]]，再检查 **边界条件**，普通解释保持原样。",
      ),
    ).toEqual([
      { text: "先记住", focus: false },
      { text: "循环不变量", focus: true },
      { text: "，再检查 ", focus: false },
      { text: "边界条件", focus: true },
      { text: "，普通解释保持原样。", focus: false },
    ]);
  });

  it("does not interpret arbitrary html as emphasis markup", () => {
    expect(teacherMessageSegments("<script>alert(1)</script>")).toEqual([
      { text: "<script>alert(1)</script>", focus: false },
    ]);
  });
});

const personaItems = [{
  id: "persona_active",
  name: "耐心主动教师",
  prompt: "耐心、有趣，先用生活例子",
  teaching_style: "短讲解",
  is_default: true,
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
}, {
  id: "persona_draft",
  name: "耐心但主动的教师",
  prompt: "耐心但主动的教师",
  teaching_style: "主动带学",
  is_default: false,
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:00:00Z",
}];

const canonicalMap = {
  map_id: "knowledge-map:generic-active",
  revision_id: "kmr_test_1",
  revision: 1,
  status: "active",
  nodes: [{
    id: "kp_1",
    name: "资料内知识点",
    explanation: "来自资料的可追溯解释",
    difficulty: "medium",
    layer: "material",
    status: "confirmed",
    evidence: [{
      material_id: "material_1",
      source_id: "material_1",
      source_revision_id: "source-revision-1",
      chunk_id: "chunk_1",
      char_start: 0,
      char_end: 20,
      quote: "来自用户上传资料的原文。",
    }],
  }],
  edges: [],
  candidates: [],
  warnings: [],
};

const canonicalPlanPreview = {
  schema: "mentorflow.mastery-plan-preview.v1",
  preview_id: "plan_preview_test_1",
  revision_id: "kmr_test_1",
  revision: 1,
  tasks: snapshot.plan.tasks,
  days: snapshot.plan.calendar,
  activation_blockers: [],
  extension_draft: { requires_confirmation: false },
  total_mastery_task_minutes: 40,
  daily_task_minutes_budget: 120,
};

const snapshotFetch = (value: any = snapshot) =>
  vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const endpoint = String(url);
    let payload = value;
    if (endpoint.endsWith("/teacher-personas")) payload = { items: personaItems };
    else if (endpoint.includes("/knowledge-map?revision=active")) payload = canonicalMap;
    else if (endpoint.includes("/knowledge-map/candidates?revision=active")) payload = { candidates: [] };
    else if (endpoint.endsWith("/knowledge-map/jobs") && init?.method === "POST") {
      payload = { id: "km_job_test_1", status: "ready", revision_id: "kmr_test_1" };
    } else if (endpoint.includes("/knowledge-map/jobs/")) {
      payload = { id: "km_job_test_1", status: "ready", revision_id: "kmr_test_1" };
    } else if (endpoint.endsWith("/mastery-plans/preview") && init?.method === "POST") {
      payload = canonicalPlanPreview;
    }
    return Promise.resolve({ ok: true, json: async () => payload });
  });


describe("ActiveTeachingChainView", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
    useMentorStore.getState().setProvider("fake");
    useMentorStore.getState().setCredentialRef("");
    useMentorStore.getState().setProviderSession({
      providerSessionId: "",
      provider: "fake",
      verified: false,
    });
    localTeacherVoiceMocks.speak.mockReset();
    localTeacherVoiceMocks.stop.mockReset();
  });

  it("drives distinct teacher poses from real teaching actions", () => {
    expect(resolveActiveTeacherPresentationState(true, "explain", {})).toBe("thinking");
    expect(resolveActiveTeacherPresentationState(false, "daily_briefing", {})).toBe("greeting");
    expect(resolveActiveTeacherPresentationState(false, "guided_practice", {})).toBe("questioning");
    expect(resolveActiveTeacherPresentationState(false, "grounded_answer", {})).toBe("talking");
    expect(resolveActiveTeacherPresentationState(false, "remediation_example", {})).toBe("encouraging");
    expect(resolveActiveTeacherPresentationState(false, "source_conflict", {})).toBe("confused");
    expect(resolveActiveTeacherPresentationState(false, "advance_route", {})).toBe("praising");
    expect(resolveActiveTeacherPresentationState(false, "complete_route", {})).toBe("ending");
    expect(resolveActiveTeacherPresentationState(false, "", {})).toBe("idle");
  });

  it("uses teaching-answer semantics when the provider action is generic", () => {
    expect(resolveActiveTeacherPresentationState(false, "grounded_answer", {}, "做得很好，这个判断完全正确。"))
      .toBe("praising");
    expect(resolveActiveTeacherPresentationState(false, "grounded_answer", {}, "没关系，已经很接近了，我们再试一次。"))
      .toBe("encouraging");
    expect(resolveActiveTeacherPresentationState(false, "grounded_answer", {}, "现在请你想一想：为什么这里需要先排序？"))
      .toBe("questioning");
    expect(resolveActiveTeacherPresentationState(false, "model_general_answer", {}, "例如，我们可以分为三步来理解。"))
      .toBe("explaining");
    expect(resolveActiveTeacherPresentationState(false, "grounded_answer", {}, "现有资料不足，暂时无法确认这个结论。"))
      .toBe("confused");
    expect(resolveActiveTeacherPresentationState(false, "grounded_answer", {}, "这是基于当前资料得到的回答。"))
      .toBe("talking");
  });

  it("keeps explicit workflow actions ahead of incidental wording", () => {
    expect(resolveActiveTeacherPresentationState(false, "complete_route", {}, "你能总结一下吗？"))
      .toBe("ending");
    expect(resolveActiveTeacherPresentationState(false, "praise", {}, "为什么这个结果成立？"))
      .toBe("praising");
  });

  it("passes generic provider answer semantics through to the rendered teacher", async () => {
    const praisingSnapshot = structuredClone(snapshot);
    praisingSnapshot.session.state.current_teaching_step = "grounded_answer";
    praisingSnapshot.session.messages = [{
      id: "generic-answer-praise",
      role: "teacher",
      action: "grounded_answer",
      content: "做得很好，这个判断完全正确。",
      citations: [],
    }];
    vi.stubGlobal("fetch", snapshotFetch(praisingSnapshot));

    render(<ActiveTeachingChainView workspaceId="generic" />);

    expect(await screen.findByTestId("teacher-companion-portrait"))
      .toHaveAttribute("data-teacher-state", "praising");
  });

  it("shows only the current knowledge-point conversation after a new briefing", async () => {
    const currentTurnSnapshot = structuredClone(snapshot);
    currentTurnSnapshot.session.state.current_teaching_step = "daily_briefing";
    currentTurnSnapshot.session.messages = [
      {
        id: "old-briefing",
        role: "teacher",
        action: "daily_briefing",
        content: "旧知识点开场，不应混入当前课堂。",
        citations: [],
      },
      {
        id: "old-answer",
        role: "user",
        action: "feedback",
        content: "这是旧知识点的回答。",
        citations: [],
      },
      {
        id: "current-briefing",
        role: "teacher",
        action: "daily_briefing",
        content: "当前知识点只显示这一轮开场。",
        citations: [],
      },
    ];
    vi.stubGlobal("fetch", snapshotFetch(currentTurnSnapshot));
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const history = await screen.findByTestId("active-conversation-history");
    expect(history).toHaveTextContent("当前知识点只显示这一轮开场");
    expect(history).not.toHaveTextContent("旧知识点开场");
    expect(history).not.toHaveTextContent("这是旧知识点的回答");
  });

  it("reads a teacher reply on demand and stops it when leaving the lesson", async () => {
    localTeacherVoiceMocks.speak.mockImplementation(async (_text, options) => {
      options?.onStart?.();
      return { ok: true };
    });
    vi.stubGlobal("fetch", snapshotFetch());
    render(<ActiveTeachingChainView workspaceId="generic" />);

    fireEvent.click(await screen.findByTestId("active-continue-lesson"));
    const speakButton = await screen.findByTestId("active-teacher-speak-message_1");
    localTeacherVoiceMocks.stop.mockClear();
    fireEvent.click(speakButton);

    await waitFor(() => {
      expect(localTeacherVoiceMocks.speak).toHaveBeenCalledWith(
        "已暂停主路线并保存位置。",
        expect.objectContaining({
          replyKind: "teaching.correct",
          onStart: expect.any(Function),
          onEnd: expect.any(Function),
          onError: expect.any(Function),
        }),
      );
      expect(speakButton).toHaveTextContent("停止朗读");
    });

    fireEvent.click(screen.getByTestId("active-leave-lesson"));
    await waitFor(() => expect(localTeacherVoiceMocks.stop).toHaveBeenCalled());
    expect(speakButton).toHaveTextContent("听语音");
  });

  it("exposes loading voice preparation as a pressed stop control", async () => {
    let resolveSpeech!: (result: { ok: boolean; reason?: string }) => void;
    localTeacherVoiceMocks.speak.mockImplementation((_text, options) => new Promise(resolve => {
      resolveSpeech = resolve;
      options?.onPreparing?.("starting");
    }));
    vi.stubGlobal("fetch", snapshotFetch());
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const speakButton = await screen.findByTestId("active-teacher-speak-message_1");
    localTeacherVoiceMocks.stop.mockClear();
    fireEvent.click(speakButton);

    await waitFor(() => {
      expect(speakButton).toHaveAttribute("aria-label", "停止朗读这条教师回复");
      expect(speakButton).toHaveAttribute("aria-pressed", "true");
      expect(speakButton).toHaveTextContent("取消模型启动");
    });

    fireEvent.click(speakButton);
    await waitFor(() => {
      expect(localTeacherVoiceMocks.stop).toHaveBeenCalled();
      expect(speakButton).toHaveAttribute("aria-label", "朗读这条教师回复");
      expect(speakButton).toHaveAttribute("aria-pressed", "false");
    });
    resolveSpeech({ ok: false, reason: "cancelled" });
  });

  it("shows a truthful failure and stops voice when the view unmounts", async () => {
    localTeacherVoiceMocks.speak.mockResolvedValue({
      ok: false,
      reason: "GPT-SoVITS 尚未就绪",
    });
    vi.stubGlobal("fetch", snapshotFetch());
    const view = render(<ActiveTeachingChainView workspaceId="generic" />);

    const speakButton = await screen.findByTestId("active-teacher-speak-message_1");
    fireEvent.click(speakButton);
    expect(await screen.findByTestId("active-teacher-speak-error-message_1"))
      .toHaveTextContent("教师语音暂不可用：GPT-SoVITS 尚未就绪 请稍后重试。");
    expect(speakButton).toHaveTextContent("重试朗读");

    localTeacherVoiceMocks.stop.mockClear();
    view.unmount();
    expect(localTeacherVoiceMocks.stop).toHaveBeenCalled();
  });

  it("opens a source-matched official practice resource in a new tab", async () => {
    const resourceSnapshot = structuredClone(snapshot);
    (resourceSnapshot.session.state as Record<string, unknown>).practice_resources = [{
      id: "leetcode-merge",
      kind: "official_practice",
      title: "合并两个有序数组 · 力扣官方真题",
      description: "在官方判题环境完成当前知识点练习",
      url: "https://leetcode.cn/problems/merge-sorted-array/",
      host: "leetcode.cn",
      provider: "力扣",
      source: "gemini_google_search_grounding",
      authority: "official",
      open_mode: "new_tab",
    }];
    vi.stubGlobal("fetch", snapshotFetch(resourceSnapshot));
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const resources = await screen.findByTestId("active-practice-resources");
    expect(resources).toHaveTextContent("去原站完成，再把结果带回来一起分析");
    const link = screen.getByTestId(
      "active-practice-resource-leetcode-merge",
    );
    expect(link).toHaveAttribute(
      "href",
      "https://leetcode.cn/problems/merge-sorted-array/",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("官方原题");
  });

  it("offers resource discovery inside the current lesson without rebuilding the plan", async () => {
    useMentorStore.getState().setProvider("openai-compatible");
    useMentorStore.getState().setProviderSession({
      providerSessionId: "provider_session_1",
      provider: "openai-compatible",
      verified: true,
    });
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const value = String(url);
      if (value.endsWith("/resource-discovery/start") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: "running",
            run_id: "resource_run_1",
            point_count: 1,
            searched_point_count: 0,
            resource_count: 0,
            warnings: [],
          }),
        });
      }
      if (value.includes("/resource-discovery/status")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: "completed",
            run_id: "resource_run_1",
            point_count: 1,
            searched_point_count: 1,
            resource_count: 1,
            warnings: [],
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: async () => value.endsWith("/teacher-personas")
          ? { items: personaItems }
          : snapshot,
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const action = await screen.findByTestId("active-classroom-resource-action");
    const button = screen.getByRole("button", { name: "为本任务寻找通用链接" });
    expect(action).toBeInTheDocument();
    fireEvent.click(button);

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) => String(url).endsWith("/resource-discovery/start")
          && init?.method === "POST",
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String(call![1].body))).toMatchObject({
        provider_session_id: "provider_session_1",
      });
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/resource-discovery/status"))).toBe(true),
    );
  });

  it("uploads a screenshot and sends its session-scoped attachment id", async () => {
    const attachment = {
      id: "attachment_1",
      original_filename: "leetcode-result.png",
      media_type: "image/png",
      size_bytes: 12,
      width: 32,
      height: 24,
      status: "ready",
      analysis_mode: "pending",
      analysis_text: "",
      file_url: "/api/active-teaching/attachments/attachment_1/file",
      created_at: "2026-07-28T00:00:00Z",
    };
    const fetchMock = vi.fn().mockImplementation(
      (url: string, init?: RequestInit) => {
        const value = String(url);
        let payload: any = snapshot;
        if (value.endsWith("/teacher-personas")) {
          payload = { items: personaItems };
        } else if (
          value.endsWith("/sessions/session_1/attachments")
          && init?.method === "POST"
        ) {
          payload = attachment;
        } else if (
          value.endsWith("/sessions/session_1/advance")
          && init?.method === "POST"
        ) {
          payload = snapshot.session;
        }
        return Promise.resolve({ ok: true, json: async () => payload });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const screenshot = new File(
      [new Uint8Array([137, 80, 78, 71])],
      "leetcode-result.png",
      { type: "image/png" },
    );
    fireEvent.change(await screen.findByTestId("active-teacher-attachment"), {
      target: { files: [screenshot] },
    });
    expect(await screen.findByTestId("active-attachment-tray")).toHaveTextContent(
      "leetcode-result.png",
    );
    fireEvent.click(screen.getByTestId("active-send-teacher"));

    await waitFor(() => {
      const uploadCall = fetchMock.mock.calls.find(
        ([url]) => String(url).endsWith("/sessions/session_1/attachments"),
      );
      expect(uploadCall?.[1]?.body).toBeInstanceOf(FormData);
      const advanceCall = fetchMock.mock.calls.find(
        ([url]) => String(url).endsWith("/sessions/session_1/advance"),
      );
      expect(JSON.parse(String(advanceCall?.[1]?.body))).toMatchObject({
        message: "",
        attachment_ids: ["attachment_1"],
      });
    });
  });

  it("accepts screenshots from clipboard items and files dropped anywhere in the dialogue", async () => {
    let uploadedCount = 0;
    const fetchMock = vi.fn().mockImplementation(
      (url: string, init?: RequestInit) => {
        const value = String(url);
        let payload: any = snapshot;
        if (value.endsWith("/teacher-personas")) {
          payload = { items: personaItems };
        } else if (
          value.endsWith("/sessions/session_1/attachments")
          && init?.method === "POST"
        ) {
          uploadedCount += 1;
          const uploadedFile = (init.body as FormData).get("file") as File;
          payload = {
            id: `attachment_${uploadedCount}`,
            original_filename: uploadedFile.name,
            media_type: uploadedFile.type,
            kind: "image",
            previewable: true,
            size_bytes: uploadedFile.size,
            width: 32,
            height: 24,
            status: "ready",
            analysis_mode: "pending",
            analysis_text: "",
            file_url: `/api/active-teaching/attachments/attachment_${uploadedCount}/file`,
            created_at: "2026-07-28T00:00:00Z",
          };
        }
        return Promise.resolve({ ok: true, json: async () => payload });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const pastedScreenshot = new File(
      [new Uint8Array([137, 80, 78, 71])],
      "clipboard-shot.png",
      { type: "image/png" },
    );
    const input = await screen.findByTestId("active-teacher-input");
    fireEvent.paste(input, {
      clipboardData: {
        files: [],
        items: [{
          kind: "file",
          type: "image/png",
          getAsFile: () => pastedScreenshot,
        }],
        types: ["Files"],
      },
    });
    expect(await screen.findByTestId("active-attachment-tray")).toHaveTextContent(
      "clipboard-shot.png",
    );

    const droppedScreenshot = new File(
      [new Uint8Array([137, 80, 78, 71, 13])],
      "dropped-shot.png",
      { type: "image/png" },
    );
    fireEvent.drop(screen.getByTestId("active-classroom-dialogue"), {
      dataTransfer: {
        files: [droppedScreenshot],
        items: [],
        types: ["Files"],
        dropEffect: "none",
      },
    });
    await waitFor(() => {
      expect(screen.getByTestId("active-attachment-tray")).toHaveTextContent(
        "dropped-shot.png",
      );
    });
    expect(uploadedCount).toBe(2);
  });

  it("records once, stops once, and inserts the local transcript without sending", async () => {
    class FakeMediaRecorder {
      static isTypeSupported() {
        return true;
      }

      state: RecordingState = "inactive";
      mimeType = "audio/webm;codecs=opus";
      private listeners = new Map<string, Array<(event: any) => void>>();

      constructor(_stream: MediaStream, _options?: MediaRecorderOptions) {}

      addEventListener(type: string, listener: (event: any) => void) {
        this.listeners.set(type, [...(this.listeners.get(type) || []), listener]);
      }

      start() {
        this.state = "recording";
      }

      requestData() {}

      stop() {
        this.state = "inactive";
        for (const listener of this.listeners.get("dataavailable") || []) {
          listener({ data: new Blob(["voice"], { type: this.mimeType }) });
        }
        for (const listener of this.listeners.get("stop") || []) listener({});
      }
    }

    const stopTrack = vi.fn();
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: stopTrack }],
        }),
      },
    });
    let resolveTranscript!: (value: any) => void;
    const transcriptResponse = new Promise<any>((resolve) => {
      resolveTranscript = resolve;
    });
    const fetchMock = vi.fn().mockImplementation(
      (url: string, init?: RequestInit) => {
        const value = String(url);
        let payload: any = snapshot;
        if (value.endsWith("/teacher-personas")) {
          payload = { items: personaItems };
        } else if (value.endsWith("/voice/warm")) {
          payload = { available: true, ready: true, status: "ready" };
        } else if (
          value.includes("/sessions/session_1/voice/transcribe")
          && init?.method === "POST"
        ) {
          return transcriptResponse;
        }
        return Promise.resolve({ ok: true, json: async () => payload });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const voiceButton = await screen.findByTestId("active-teacher-voice");
    expect(voiceButton).toHaveTextContent("语音输入");
    expect(voiceButton).toHaveAttribute(
      "aria-label",
      "开始语音输入（录音转文字）",
    );
    fireEvent.click(voiceButton);
    expect(await screen.findByText(/■ 0s/)).toBeInTheDocument();
    expect(voiceButton).toHaveAttribute(
      "aria-label",
      "停止语音输入并转成文字",
    );
    fireEvent.click(voiceButton);
    await waitFor(() => {
      expect(voiceButton).toHaveAttribute("title", "正在将录音转成文字");
      expect(voiceButton).toHaveAttribute(
        "aria-label",
        "正在将语音输入转成文字",
      );
    });
    await act(async () => {
      resolveTranscript({
        ok: true,
        json: async () => ({
          ok: true,
          text: "我想先看一个更简单的例子",
          engine: "test-sensevoice",
        }),
      });
      await transcriptResponse;
    });

    await waitFor(() => {
      expect(screen.getByTestId("active-teacher-input")).toHaveValue(
        "我想先看一个更简单的例子",
      );
      expect(screen.getByTestId("active-teacher-voice-status")).toHaveTextContent(
        "已转成文字",
      );
    });
    expect(stopTrack).toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(
      ([url]) => String(url).includes("/voice/transcribe"),
    )).toBe(true);
    expect(fetchMock.mock.calls.some(
      ([url]) => String(url).includes("/sessions/session_1/advance"),
    )).toBe(false);
  });

  it("offers to re-answer an old evidence-gated reply with the layered policy", async () => {
    const legacySnapshot = structuredClone(snapshot);
    legacySnapshot.session.messages = [
      {
        id: "briefing",
        role: "teacher",
        action: "daily_briefing",
        content: "先了解你的基础。",
        citations: [],
      },
      {
        id: "learner-coaching-request",
        role: "user",
        action: "feedback",
        content: "Java 忘光了，你能带着我完成任务吗？",
        citations: [],
      },
      {
        id: "legacy-insufficient",
        role: "teacher",
        action: "insufficient_evidence",
        content: "当前资料没有足够证据。",
        citations: [],
      },
    ];
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/sessions/session_1/advance") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...legacySnapshot.session,
            messages: [
              ...legacySnapshot.session.messages,
              {
                id: "corrected",
                role: "teacher",
                action: "learning_coach_response",
                content: "可以，我会一步一步带着你完成。",
                citations: [],
              },
            ],
          }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => legacySnapshot });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    fireEvent.click(
      await screen.findByTestId("active-reanswer-with-layered-knowledge"),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/sessions/session_1/advance",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("你能带着我完成任务吗"),
        }),
      ),
    );
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
    expect(screen.getByRole("heading", { name: /今天按计划学习/ })).toBeInTheDocument();
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

  it("defaults a planned task to a compact daily dashboard and preserves lesson switching", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => snapshot,
    }));
    render(<ActiveTeachingChainView workspaceId="generic" />);

    const dashboard = await screen.findByTestId("active-today-dashboard");
    expect(dashboard).toHaveTextContent("今天有 1 个学习计划");
    expect(
      screen.queryByTestId("active-today-tasks-generic-active"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("active-daily-plan-toggle-generic-active"));
    expect(await screen.findByTestId("active-today-tasks-generic-active")).toHaveTextContent(
      "资料内知识点",
    );
    expect(screen.getByTestId("active-daily-plan-generic-active")).toHaveTextContent(
      "学习顺序：前置依赖优先",
    );
    expect(screen.getByTestId("active-plan-details")).toHaveClass("active-chain__is-hidden");
    expect(screen.getByTestId("active-completion-report")).toHaveClass(
      "active-chain__is-hidden",
    );
    expect(document.getElementById("active-teacher-workbench")).toHaveClass(
      "active-chain__is-hidden",
    );

    fireEvent.click(screen.getByTestId("active-toggle-plan-details"));
    expect(screen.getByTestId("active-plan-details")).not.toHaveClass(
      "active-chain__is-hidden",
    );
    expect(screen.getByTestId("active-completion-report")).not.toHaveClass(
      "active-chain__is-hidden",
    );
    fireEvent.click(screen.getByTestId("active-toggle-plan-details"));
    expect(screen.getByTestId("active-plan-details")).toHaveClass("active-chain__is-hidden");

    fireEvent.click(screen.getByTestId("active-continue-lesson"));
    expect(document.getElementById("active-teacher-workbench")).not.toHaveClass(
      "active-chain__is-hidden",
    );
    fireEvent.click(screen.getByTestId("active-leave-lesson"));
    expect(document.getElementById("active-teacher-workbench")).toHaveClass(
      "active-chain__is-hidden",
    );
  });

  it("shows every active plan as an expandable ordered daily section", async () => {
    const firstTodayTask = {
      ...snapshot.plan.calendar[0].tasks[0],
      knowledge_point_id: "kp_1",
      knowledge_point_name: "资料内知识点",
      point_sequence: 1,
      difficulty: "easy",
      prerequisite_names: [],
    };
    const secondTodayTask = {
      id: "task_2",
      title: "第二计划基础概念 · 理解",
      knowledge_point_id: "kp_2",
      knowledge_point_name: "第二计划基础概念",
      task_type: "initial_learning",
      learning_phase: "understand",
      point_sequence: 1,
      difficulty: "简单",
      prerequisite_names: [],
      estimated_task_minutes: 25,
      completed_task_minutes: 0,
      status: "planned",
    };
    const index = {
      items: [{
        workspace_id: "generic-active",
        title: "计划一",
        archived: false,
        status: "learning",
        material_count: 1,
        knowledge_point_count: 1,
        plan_id: "plan_1",
        session_id: "session_1",
        task_count: 1,
        completed_task_count: 0,
        next_task: firstTodayTask,
        today_plan: {
          date: "2026-07-27",
          remaining_task_minutes: 40,
          tasks: [firstTodayTask],
          overdue_task_count: 1,
          overdue_task_minutes: 15,
          oldest_overdue_date: "2026-07-26",
          overdue_day_count: 1,
          overdue_days: [{
            date: "2026-07-26",
            task_count: 1,
            remaining_task_minutes: 15,
            tasks: [{
              ...firstTodayTask,
              id: "task_overdue",
              title: "昨日基础 · 理解",
              knowledge_point_id: "kp_overdue",
              knowledge_point_name: "昨日基础",
              day_date: "2026-07-26",
              debt_date: "2026-07-26",
              estimated_task_minutes: 15,
            }],
          }],
          overdue_tasks: [{
            ...firstTodayTask,
            id: "task_overdue",
            title: "昨日基础 · 理解",
            knowledge_point_id: "kp_overdue",
            knowledge_point_name: "昨日基础",
            day_date: "2026-07-26",
            estimated_task_minutes: 15,
          }],
          projected_review_items: [],
        },
        updated_at: "2026-07-27T00:00:00Z",
      }, {
        workspace_id: "second-plan",
        title: "计划二",
        archived: false,
        status: "planned",
        material_count: 1,
        knowledge_point_count: 1,
        plan_id: "plan_2",
        session_id: null,
        task_count: 1,
        completed_task_count: 0,
        next_task: secondTodayTask,
        today_plan: {
          date: "2026-07-27",
          remaining_task_minutes: 25,
          tasks: [secondTodayTask],
          projected_review_items: [],
        },
        updated_at: "2026-07-27T00:00:00Z",
      }],
    };
    const secondSession = {
      ...snapshot.session,
      session_id: "session_2",
      state: {
        ...snapshot.session.state,
        active_task_id: "task_2",
        current_knowledge_point_id: "kp_1",
        autonomy: { awaiting_learner: true },
      },
    };
    const secondSnapshot = {
      ...snapshot,
      workspace_id: "second-plan",
      receipt: { ...snapshot.receipt, learning_goal: "计划二" },
      plan: { ...snapshot.plan, id: "plan_2" },
      session: secondSession,
    };
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const value = String(url);
      let payload: any = snapshot;
      if (value === "/api/active-teaching/workspaces?include_archived=false") {
        payload = index;
      } else if (value.includes("/workspaces/second-plan/snapshot")) {
        payload = secondSnapshot;
      } else if (
        value.includes("/workspaces/second-plan/sessions/start")
        && init?.method === "POST"
      ) {
        payload = secondSession;
      }
      return Promise.resolve({ ok: true, json: async () => payload });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    expect(await screen.findByTestId("active-today-dashboard")).toHaveTextContent(
      "今天有 2 个学习计划",
    );
    expect(screen.getByTestId("active-daily-plan-generic-active")).toHaveTextContent(
      "计划一",
    );
    expect(screen.getByTestId("active-overdue-summary")).toHaveTextContent(
      "历史欠账不会消失",
    );
    fireEvent.click(screen.getByTestId("active-daily-plan-toggle-generic-active"));
    expect(
      await screen.findByTestId("active-overdue-generic-active"),
    ).toHaveTextContent("2026-07-26");
    fireEvent.click(screen.getByTestId("active-rollover-overdue-generic-active"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/sessions/session_1/replan",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"trigger":"manual_debt_rollover"'),
        }),
      ),
    );
    expect(screen.getByTestId("active-daily-plan-second-plan")).toHaveTextContent(
      "计划二",
    );
    expect(
      screen.queryByTestId("active-today-tasks-second-plan"),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("active-daily-plan-toggle-second-plan"));
    expect(
      await screen.findByTestId("active-today-tasks-second-plan"),
    ).toHaveTextContent("01");
    expect(screen.getByTestId("active-today-tasks-second-plan")).toHaveTextContent(
      "第二计划基础概念",
    );

    fireEvent.click(screen.getByTestId("active-today-task-second-plan-kp_2"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/workspaces/second-plan/sessions/start",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() =>
      expect(document.getElementById("active-teacher-workbench")).not.toHaveClass(
        "active-chain__is-hidden",
      ),
    );
    expect(screen.getByTestId("active-current-knowledge-point")).toHaveTextContent(
      "资料内知识点",
    );
  });

  it("requires an actual learner answer before requesting an understanding judgment", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    expect(await screen.findByTestId("active-understood")).toBeDisabled();
    fireEvent.change(screen.getByTestId("active-teacher-input"), {
      target: { value: "我会先记录已经看过的元素，再检查目标是否出现。" },
    });
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
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/active-teaching/sessions/session_1/advance",
      expect.objectContaining({
        body: expect.stringContaining("我会先记录已经看过的元素"),
      }),
    );
  });

  it("lets the learner complete the current knowledge point without extra evidence", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    const forceComplete = await screen.findByTestId("active-force-complete");
    fireEvent.change(screen.getByTestId("active-teacher-input"), {
      target: { value: "这段文字只留在输入框，不应作为完成消息发送。" },
    });
    expect(forceComplete).toBeEnabled();
    fireEvent.click(forceComplete);
    await waitFor(() => {
      const forceCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/sessions/session_1/advance")
          && init?.method === "POST"
          && String(init?.body).includes('"force_complete":true'),
      );
      expect(forceCall).toBeTruthy();
      expect(JSON.parse(String(forceCall?.[1]?.body))).toEqual({
        force_complete: true,
      });
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/sessions/session_1/advance",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"force_complete":true'),
        }),
      );
    });
    expect(screen.queryByTestId("active-pending-user-message")).not.toBeInTheDocument();
    expect(screen.getByTestId("active-teacher-input")).toHaveValue(
      "这段文字只留在输入框，不应作为完成消息发送。",
    );
  });

  it("shows the teacher and the learner message immediately while a reply is pending", async () => {
    let resolveAdvance: ((value: any) => void) | undefined;
    const advanceResponse = new Promise((resolve) => {
      resolveAdvance = resolve;
    });
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/sessions/session_1/advance") && init?.method === "POST") {
        return advanceResponse;
      }
      return Promise.resolve({ ok: true, json: async () => snapshot });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    expect(await screen.findByTestId("teacher-companion-portrait")).toBeInTheDocument();
    const input = screen.getByTestId("active-teacher-input");
    fireEvent.change(input, { target: { value: "这里我没听懂，能换个例子吗？" } });
    fireEvent.click(screen.getByTestId("active-send-teacher"));

    const pending = await screen.findByTestId("active-pending-user-message");
    expect(pending).toHaveTextContent("这里我没听懂，能换个例子吗？");
    expect(pending).toHaveTextContent("教师正在思考");
    expect(input).toHaveValue("");

    resolveAdvance?.({ ok: true, json: async () => snapshot.session });
    await waitFor(() =>
      expect(screen.queryByTestId("active-pending-user-message")).not.toBeInTheDocument(),
    );
  });

  it("keeps a failed learner message visible and provides a working retry", async () => {
    let advanceAttempts = 0;
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/sessions/session_1/advance") && init?.method === "POST") {
        advanceAttempts += 1;
        if (advanceAttempts === 1) {
          return Promise.resolve({
            ok: false,
            status: 503,
            json: async () => ({
              error: { code: "PROVIDER_UPSTREAM_ERROR", message: "模型暂时不可用" },
            }),
          });
        }
        return Promise.resolve({ ok: true, json: async () => snapshot.session });
      }
      return Promise.resolve({ ok: true, json: async () => snapshot });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    fireEvent.change(await screen.findByTestId("active-teacher-input"), {
      target: { value: "请带着我慢慢写这个代码" },
    });
    fireEvent.click(screen.getByTestId("active-send-teacher"));
    expect(await screen.findByTestId("active-retry-user-message")).toBeInTheDocument();
    expect(screen.getByTestId("active-pending-user-message")).toHaveTextContent(
      "模型暂时不可用",
    );

    fireEvent.click(screen.getByTestId("active-retry-user-message"));
    await waitFor(() =>
      expect(screen.queryByTestId("active-pending-user-message")).not.toBeInTheDocument(),
    );
    expect(advanceAttempts).toBe(2);
  });

  it("reconnects a lost provider session and retries the learner message once", async () => {
    useMentorStore.getState().setProvider("openai-compatible");
    useMentorStore.getState().setBaseUrl("https://mock.provider/v1");
    useMentorStore.getState().setModel("mock-model");
    useMentorStore.getState().setCredentialRef("mfcred_saved_reference");
    useMentorStore.getState().setProviderSession({
      providerSessionId: "ps_lost",
      provider: "openai-compatible",
      verified: true,
    });
    let advanceAttempts = 0;
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const value = String(url);
      if (value.endsWith("/sessions/session_1/advance") && init?.method === "POST") {
        advanceAttempts += 1;
        if (advanceAttempts === 1) {
          return Promise.resolve({
            ok: false,
            status: 428,
            json: async () => ({
              error: { code: "PROVIDER_SESSION_REQUIRED", message: "请先验证 Provider 连接" },
            }),
          });
        }
        return Promise.resolve({ ok: true, json: async () => snapshot.session });
      }
      if (value === "/api/provider-sessions/ps_lost") {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: async () => ({
            error: { code: "PROVIDER_SESSION_NOT_FOUND", message: "会话不存在" },
          }),
        });
      }
      if (value === "/api/provider-sessions" && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ provider_session_id: "ps_restored" }),
        });
      }
      if (value === "/api/provider-sessions/ps_restored/test") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            provider_session_id: "ps_restored",
            verified: true,
            expires_at: "2030-01-01T00:00:00Z",
          }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => snapshot });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);

    fireEvent.change(await screen.findByTestId("active-teacher-input"), {
      target: { value: "B，请你带着我慢慢学会写这个代码" },
    });
    fireEvent.click(screen.getByTestId("active-send-teacher"));

    await waitFor(() => expect(advanceAttempts).toBe(2));
    expect(useMentorStore.getState().providerSessionId).toBe("ps_restored");
    const advanceBodies = fetchMock.mock.calls
      .filter(([url]) => String(url).endsWith("/sessions/session_1/advance"))
      .map(([, init]) => JSON.parse(String(init?.body || "{}")));
    expect(advanceBodies.map((body) => body.provider_session_id)).toEqual([
      "ps_lost",
      "ps_restored",
    ]);
  });

  it("submits generic learning conditions without any course-specific fields", async () => {
    const fetchMock = snapshotFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    await screen.findByTestId("active-save-receipt");
    expect(screen.getByTestId("active-discover-external-resources")).toBeDisabled();
    expect(screen.queryByTestId("active-daily-minutes")).not.toBeInTheDocument();
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent("系统按总任务量平均分配");
    expect(screen.getByTestId("active-system-allocation")).toHaveTextContent("完整复习 2 轮");
    fireEvent.change(screen.getByTestId("active-teacher-persona"), {
      target: { value: "persona_active" },
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
      expect(body.teacher_persona_id).toBe("persona_active");
      expect(body.daily_task_minutes).toBeUndefined();
      expect(body.material_ids).toEqual(["material_1"]);
      expect(JSON.stringify(body)).not.toMatch(/leetcode|hot150|面试经典/iu);
      expect(
        fetchMock.mock.calls.some(
          ([url]) => String(url).endsWith("/knowledge-map/jobs"),
        ),
      ).toBe(false);
      expect(
        fetchMock.mock.calls.some(
          ([url]) => String(url).endsWith("/mastery-plans/preview"),
        ),
      ).toBe(true);
    });
  });

  it("shows the knowledge-point ordinal instead of the phase-task route index", async () => {
    const points = Array.from({ length: 150 }, (_, index) => ({
      id: `kp_${index + 1}`,
      name: `知识点 ${index + 1}`,
      difficulty: "中等",
      estimated_task_minutes: 20,
      current_mastery: 0,
    }));
    const phaseTasks = [
      ...["understand", "recall", "guided_practice", "independent_application"].map(
        (phase, index) => ({
          id: `task_1_${index}`,
          knowledge_point_id: "kp_1",
          point_sequence: 1,
          task_type: "initial_learning",
          learning_phase: phase,
          status: "planned",
          estimated_task_minutes: 5,
        }),
      ),
      {
        id: "task_2_0",
        knowledge_point_id: "kp_2",
        point_sequence: 2,
        task_type: "initial_learning",
        learning_phase: "understand",
        status: "planned",
        estimated_task_minutes: 5,
      },
    ];
    const ordinalSnapshot = {
      ...snapshot,
      knowledge_graph: { ...snapshot.knowledge_graph, knowledge_points: points },
      plan: {
        ...snapshot.plan,
        tasks: phaseTasks,
        calendar: [{ ...snapshot.plan.calendar[0], tasks: phaseTasks }],
      },
      session: {
        ...snapshot.session,
        state: {
          ...snapshot.session.state,
          current_knowledge_point_id: "kp_2",
          route_index: 4,
          route_length: 614,
        },
      },
    };
    vi.stubGlobal("fetch", snapshotFetch(ordinalSnapshot));
    render(<ActiveTeachingChainView workspaceId="generic" />);
    expect(await screen.findByTestId("active-current-knowledge-point")).toHaveTextContent(
      "第 2 / 150 个知识点",
    );
    expect(screen.getByTestId("active-current-knowledge-point")).not.toHaveTextContent(
      "5 / 614",
    );
  });

  it("auto-saves and restores every editable task draft field per workspace", async () => {
    const draftSnapshot = { ...snapshot, workspace_id: "draft-case-active" };
    const fetchMock = snapshotFetch(draftSnapshot);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<ActiveTeachingChainView workspaceId="draft-case" />);
    await screen.findByTestId("active-draft-status");

    fireEvent.change(screen.getByTestId("active-goal"), {
      target: { value: "刷新后仍保留的学习目标" },
    });
    fireEvent.change(screen.getByTestId("active-teacher-persona"), {
      target: { value: "persona_draft" },
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
    expect(screen.getByTestId("active-teacher-persona")).toHaveValue("persona_draft");
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
    const fetchMock = snapshotFetch();
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
      expect(urls.some((url) => url.endsWith("/knowledge-map/jobs"))).toBe(false);
      expect(urls.some((url) => url.endsWith("/mastery-plans/preview"))).toBe(true);
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
      if (url.endsWith("/mastery-plans/preview")) {
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
      const endpoint = String(url);
      let payload: any = emptySnapshot;
      if (endpoint.endsWith("/materials") && init?.method === "POST") {
        payload = { id: "uploaded_generic_1", status: "ready" };
      } else if (endpoint.endsWith("/knowledge-map/jobs") && init?.method === "POST") {
        payload = { id: "km_job_upload", status: "ready", revision_id: "kmr_test_1" };
      } else if (endpoint.includes("/knowledge-map?revision=active")) {
        payload = canonicalMap;
      } else if (endpoint.includes("/knowledge-map/candidates?revision=active")) {
        payload = { candidates: [] };
      } else if (endpoint.endsWith("/mastery-plans/preview") && init?.method === "POST") {
        payload = canonicalPlanPreview;
      }
      return Promise.resolve({ ok: true, json: async () => payload });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" />);
    const input = await screen.findByTestId("active-material-file");
    const resourceToggle = screen.getByTestId("active-discover-external-resources");
    expect(resourceToggle).toBeChecked();
    fireEvent.click(resourceToggle);
    expect(resourceToggle).not.toBeChecked();
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
      expect(urls.some((url) => url.endsWith("/knowledge-map/jobs"))).toBe(true);
      expect(urls.some((url) => url.endsWith("/mastery-plans/preview"))).toBe(true);
      const receiptCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/receipt"));
      expect(JSON.parse(receiptCall![1].body).material_ids).toEqual(["uploaded_generic_1"]);
      expect(JSON.parse(receiptCall![1].body).discover_external_resources).toBe(false);
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
    const fetchMock = snapshotFetch(indexedOnlySnapshot);
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
      expect(urls.some((url) => url.endsWith("/knowledge-map/jobs"))).toBe(true);
      expect(urls.some((url) => url.endsWith("/mastery-plans/preview"))).toBe(true);
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
    fireEvent.click(screen.getByTestId("active-daily-plan-toggle-generic-active"));
    fireEvent.click(await screen.findByTestId("active-today-task-generic-active-task_1"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/active-teaching/sessions/session_1/focus",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ task_id: "task_1" }),
        }),
      ),
    );
    expect(document.getElementById("active-teacher-workbench")).not.toHaveClass(
      "active-chain__is-hidden",
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
    const dialog = await screen.findByRole("dialog", { name: /归档“可整理的学习任务”/ });
    expect(dialog).toHaveTextContent("不会删除");
    expect(dialog).toHaveTextContent("可恢复");
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/active-teaching/workspaces/generic-active",
      expect.objectContaining({ method: "PATCH" }),
    );
    fireEvent.click(screen.getByTestId("active-confirm-archive"));
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

  it("asks before extracting a selected video and only plans from explicitly confirmed materials", async () => {
    localStorage.setItem("mentorflow_planner_handoff_v1", JSON.stringify({
      goal: "理解视频课程并完成练习",
      resource: { title: "视频课程", url: "https://www.bilibili.com/video/BVTEST123" },
      created_at: "2026-08-18T00:00:00Z",
    }));
    const emptySnapshot = {
      ...snapshot,
      materials: [],
      receipt: null,
      knowledge_graph: { knowledge_points: [], edges: [] },
      plan: null,
      session: null,
    };
    let videoIngestCalls = 0;
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const endpoint = String(url);
      let payload: any = emptySnapshot;
      if (endpoint.endsWith("/materials/url") && init?.method === "POST") {
        payload = { status: "awaiting_supplementary_choice", ingested: false };
      } else if (endpoint.endsWith("/materials/video/search") && init?.method === "POST") {
        payload = {
          status: "supplementary_candidates",
          supplementary_search: {
            candidates: [{ id: "notes-1", title: "配套讲义", url: "https://notes.example/course", snippet: "课程公式与例题" }],
          },
        };
      } else if (endpoint.endsWith("/materials/video/ingest") && init?.method === "POST") {
        videoIngestCalls += 1;
        const body = JSON.parse(String(init.body || "{}"));
        payload = body.confirm_transcript
          ? { status: "ingested", ingested: true, material_ids: ["video-transcript-1", "notes-material-1"] }
          : {
              status: "transcript_preview",
              requires_transcript_confirmation: true,
              transcript: {
                title: "视频课程",
                subtitle: {
                  language: "zh-Hans",
                  automatic: true,
                  segments: [{ start_ms: 0, end_ms: 5000, text: "字幕第一段" }],
                  quality: { state: "ready", segment_count: 1 },
                },
              },
            };
      } else if (endpoint.endsWith("/knowledge-map/jobs") && init?.method === "POST") {
        payload = { id: "km-job-video", status: "ready", revision_id: "kmr_test_1" };
      } else if (endpoint.includes("/knowledge-map?revision=active")) {
        payload = canonicalMap;
      } else if (endpoint.includes("/knowledge-map/candidates?revision=active")) {
        payload = { candidates: [] };
      } else if (endpoint.endsWith("/mastery-plans/preview") && init?.method === "POST") {
        payload = canonicalPlanPreview;
      }
      return Promise.resolve({ ok: true, json: async () => payload });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ActiveTeachingChainView workspaceId="generic" createRequest={1} compactPlannerFlow />);

    fireEvent.click(await screen.findByRole("button", { name: "开始整理已选资料并生成计划" }));
    expect(await screen.findByTestId("video-supplementary-choice")).toBeInTheDocument();
    expect(videoIngestCalls).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "愿意，先帮我找资料" }));
    const candidate = await screen.findByText("配套讲义");
    fireEvent.click(candidate.closest("label")!);
    fireEvent.click(screen.getByRole("button", { name: "使用已选 1 份资料并提取字幕" }));

    expect(await screen.findByTestId("video-transcript-review")).toHaveTextContent("自动字幕，可能存在偏差");
    fireEvent.click(screen.getByRole("button", { name: "字幕可以使用，生成知识地图和计划" }));

    await waitFor(() => {
      const receiptCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/receipt"));
      expect(receiptCall).toBeTruthy();
      expect(JSON.parse(String(receiptCall![1].body)).material_ids).toEqual(["video-transcript-1", "notes-material-1"]);
    });
    const ingestBodies = fetchMock.mock.calls
      .filter(([url]) => String(url).endsWith("/materials/video/ingest"))
      .map(([, init]) => JSON.parse(String(init.body || "{}")));
    expect(ingestBodies[0].selected_candidate_urls).toEqual(["https://notes.example/course"]);
    expect(ingestBodies[1]).toMatchObject({
      find_additional_materials: true,
      confirm_transcript: true,
      selected_candidate_urls: ["https://notes.example/course"],
    });
  });
});
