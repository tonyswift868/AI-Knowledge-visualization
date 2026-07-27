import { useEffect, useMemo, useRef, useState } from "react";
import {
  activeLearningTasks,
  activeRequest,
  activeSnapshot,
  setActiveLearningTaskArchived,
  type ActiveLearningTaskSummary,
  uploadActiveMaterial,
} from "./api";
import { useMentorStore } from "../../stores/useMentorStore";
import "./active-teaching.css";

const isoAfterDays = (days: number) => {
  const value = new Date(Date.now() + days * 86400000);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
};
const ACTIVE_WORKSPACE_KEY = "mentorflow_active_teaching_workspace_v1";
const ACTIVE_DRAFT_PREFIX = "mentorflow_active_task_draft_v1:";

type ActiveTaskDraft = {
  goal?: string;
  level?: string;
  startDate?: string;
  deadline?: string;
  studyWeekdays?: number[];
  unavailableDates?: string;
  shortDate?: string;
  shortMinutes?: number;
  notes?: string;
  learningStyle?: string;
  teacherPersona?: string;
  learnerInterests?: string;
  message?: string;
  reviewAnswer?: string;
  code?: string;
  updatedAt?: string;
};

const draftStorageKey = (workspaceId: string) =>
  `${ACTIVE_DRAFT_PREFIX}${workspaceId}`;

const readTaskDraft = (workspaceId: string): ActiveTaskDraft | null => {
  try {
    const raw = localStorage.getItem(draftStorageKey(workspaceId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
};

export function ActiveTeachingChainView({
  workspaceId,
  createRequest = 0,
  onCreateRequestConsumed,
}: {
  workspaceId: string;
  createRequest?: number;
  onCreateRequestConsumed?: () => void;
}) {
  const providerSessionId = useMentorStore((state) => state.providerSessionId);
  const providerVerified = useMentorStore((state) => state.verified);
  const providerId = useMentorStore((state) => state.provider);
  const realProviderSessionId =
    providerVerified && providerId !== "fake" ? providerSessionId : null;
  const [activeWorkspaceId, setActiveWorkspaceId] = useState(() => {
    try {
      return localStorage.getItem(ACTIVE_WORKSPACE_KEY) || `${workspaceId || "general-learning"}-active`;
    } catch {
      return `${workspaceId || "general-learning"}-active`;
    }
  });
  const [snapshot, setSnapshot] = useState<any>(null);
  const [learningTasks, setLearningTasks] = useState<ActiveLearningTaskSummary[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [creatingNewTask, setCreatingNewTask] = useState(() =>
    Boolean(readTaskDraft(activeWorkspaceId)),
  );
  const [file, setFile] = useState<File | null>(null);
  const [goal, setGoal] = useState("完全掌握上传资料，并能用可验证的方式独立应用");
  const [level, setLevel] = useState("beginner");
  const [startDate, setStartDate] = useState(isoAfterDays(0));
  const [deadline, setDeadline] = useState(isoAfterDays(90));
  const [studyWeekdays, setStudyWeekdays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]);
  const [unavailableDates, setUnavailableDates] = useState("");
  const [shortDate, setShortDate] = useState("");
  const [shortMinutes, setShortMinutes] = useState(0);
  const [notes, setNotes] = useState("");
  const [learningStyle, setLearningStyle] = useState("主动讲解、示例、理解检查、练习与复习");
  const [teacherPersona, setTeacherPersona] = useState("");
  const [learnerInterests, setLearnerInterests] = useState("");
  const [message, setMessage] = useState("继续主动讲解");
  const [reviewAnswer, setReviewAnswer] = useState("");
  const [reviewAsOf, setReviewAsOf] = useState(isoAfterDays(0));
  const [problems, setProblems] = useState<any[]>([]);
  const [problemId, setProblemId] = useState("");
  const [code, setCode] = useState("def solve(values):\n    # 在这里完成实现\n    return 0");
  const [draftRevision, setDraftRevision] = useState<number | null>(null);
  const [codeResult, setCodeResult] = useState<any>(null);
  const [hint, setHint] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [draftLoadedWorkspace, setDraftLoadedWorkspace] = useState<string | null>(null);
  const [draftSavedAt, setDraftSavedAt] = useState("");
  const refreshSequence = useRef(0);

  const refresh = async () => {
    const requestSequence = ++refreshSequence.current;
    const [next, taskItems] = await Promise.all([
      activeSnapshot(activeWorkspaceId),
      activeLearningTasks(showArchived),
    ]);
    let problemItems: any[] = [];
    try {
      const problemPayload = await activeRequest(
        `/workspaces/${encodeURIComponent(activeWorkspaceId)}/problems`,
      );
      problemItems = problemPayload.items || [];
    } catch {
      problemItems = [];
    }
    if (requestSequence !== refreshSequence.current) return;
    setLearningTasks(taskItems);
    if (
      !next.receipt
      && !(next.materials || []).length
      && taskItems.length
      && !taskItems.some((item) => item.workspace_id === activeWorkspaceId)
      && !creatingNewTask
    ) {
      setActiveWorkspaceId(taskItems[0].workspace_id);
      return;
    }
    setSnapshot(next);
    if (next.receipt || (next.materials || []).length) setCreatingNewTask(false);
    setProblems(problemItems);
    setProblemId((current) =>
      problemItems.some((item: any) => item.id === current)
        ? current
        : problemItems[0]?.id || "",
    );
    if (next.receipt && !readTaskDraft(activeWorkspaceId)) {
      setGoal(next.receipt.learning_goal);
      setLevel(next.receipt.current_level);
      setStartDate(next.receipt.start_date || isoAfterDays(0));
      setDeadline(next.receipt.deadline);
      setStudyWeekdays(next.receipt.study_weekdays || [0, 1, 2, 3, 4, 5, 6]);
      setUnavailableDates((next.receipt.unavailable_dates || []).join(","));
      const special = Object.entries(next.receipt.special_date_minutes || {})[0] as [string, any] | undefined;
      if (special) {
        setShortDate(special[0]);
        setShortMinutes(Number(special[1]));
      }
      setNotes(next.receipt.notes || "");
      setLearningStyle(next.receipt.preferred_learning_style);
      setTeacherPersona(next.receipt.teacher_persona || "");
      setLearnerInterests(next.receipt.learner_interests || "");
    }
  };

  useEffect(() => {
    setDraftLoadedWorkspace(null);
    setDraftSavedAt("");
    const draft = readTaskDraft(activeWorkspaceId);
    if (draft) {
      if (typeof draft.goal === "string") setGoal(draft.goal);
      if (typeof draft.level === "string") setLevel(draft.level);
      if (typeof draft.startDate === "string") setStartDate(draft.startDate);
      if (typeof draft.deadline === "string") setDeadline(draft.deadline);
      if (Array.isArray(draft.studyWeekdays)) setStudyWeekdays(draft.studyWeekdays);
      if (typeof draft.unavailableDates === "string") setUnavailableDates(draft.unavailableDates);
      if (typeof draft.shortDate === "string") setShortDate(draft.shortDate);
      if (typeof draft.shortMinutes === "number") setShortMinutes(draft.shortMinutes);
      if (typeof draft.notes === "string") setNotes(draft.notes);
      if (typeof draft.learningStyle === "string") setLearningStyle(draft.learningStyle);
      if (typeof draft.teacherPersona === "string") setTeacherPersona(draft.teacherPersona);
      if (typeof draft.learnerInterests === "string") setLearnerInterests(draft.learnerInterests);
      if (typeof draft.message === "string") setMessage(draft.message);
      if (typeof draft.reviewAnswer === "string") setReviewAnswer(draft.reviewAnswer);
      if (typeof draft.code === "string") setCode(draft.code);
      if (typeof draft.updatedAt === "string") setDraftSavedAt(draft.updatedAt);
    }
    setDraftLoadedWorkspace(activeWorkspaceId);
  }, [activeWorkspaceId]);

  useEffect(() => {
    try {
      localStorage.setItem(ACTIVE_WORKSPACE_KEY, activeWorkspaceId);
    } catch {
      // Private browsing may disable local storage; server state remains durable.
    }
    refresh().catch(() => setSnapshot(null));
  }, [activeWorkspaceId, showArchived]);

  useEffect(() => {
    if (
      draftLoadedWorkspace !== activeWorkspaceId
      || snapshot?.workspace_id !== activeWorkspaceId
    ) return;
    const timer = window.setTimeout(() => {
      const updatedAt = new Date().toISOString();
      const draft: ActiveTaskDraft = {
        goal,
        level,
        startDate,
        deadline,
        studyWeekdays,
        unavailableDates,
        shortDate,
        shortMinutes,
        notes,
        learningStyle,
        teacherPersona,
        learnerInterests,
        message,
        reviewAnswer,
        code,
        updatedAt,
      };
      try {
        localStorage.setItem(draftStorageKey(activeWorkspaceId), JSON.stringify(draft));
        setDraftSavedAt(updatedAt);
      } catch {
        setError("浏览器阻止了本机草稿保存，请检查站点存储权限。");
      }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [
    activeWorkspaceId,
    code,
    deadline,
    draftLoadedWorkspace,
    goal,
    learnerInterests,
    learningStyle,
    level,
    message,
    notes,
    reviewAnswer,
    shortDate,
    shortMinutes,
    snapshot?.workspace_id,
    startDate,
    studyWeekdays,
    teacherPersona,
    unavailableDates,
  ]);

  const run = async (label: string, operation: () => Promise<any>) => {
    setBusy(label);
    setError("");
    try {
      await operation();
      await refresh();
    } catch (reason: any) {
      setError(reason?.message || "操作失败");
    } finally {
      setBusy("");
    }
  };

  const materials = snapshot?.materials || [];
  const readyChunkCount = materials
    .filter((item: any) => item.status === "ready")
    .reduce((total: number, item: any) => total + Number(item.chunk_count || 0), 0);
  const localKnowledgeBase = snapshot?.local_knowledge_base;
  const graph = snapshot?.knowledge_graph || { knowledge_points: [], edges: [] };
  const plan = snapshot?.plan;
  const initialPhaseDays = (plan?.calendar || []).filter(
    (day: any) => day.study_phase === "initial_learning",
  );
  const reviewPhaseDays = (plan?.calendar || []).filter(
    (day: any) => day.study_phase === "review",
  );
  const phaseRange = (days: any[]) => {
    if (!days.length) return "等待排程";
    const short = (value: string) => value.slice(5).replace("-", "/");
    return `${short(days[0].date)}–${short(days[days.length - 1].date)}，共 ${days.length} 天`;
  };
  const session = snapshot?.session;
  const completionReport = snapshot?.completion_report;
  useEffect(() => {
    if (!session?.session_id || !problemId) return;
    activeRequest(
      `/sessions/${encodeURIComponent(session.session_id)}/problems/${encodeURIComponent(problemId)}/practice`,
    ).then((payload) => {
      if (payload.draft) {
        setCode(payload.draft.code);
        setDraftRevision(payload.draft.revision);
      }
      setCodeResult(payload.submissions?.at(-1) || null);
    }).catch(() => {
      setDraftRevision(null);
      setCodeResult(null);
    });
  }, [session?.session_id, problemId]);
  const latestTeacher = useMemo(
    () => [...(session?.messages || [])].reverse().find((item: any) => item.role === "teacher"),
    [session],
  );
  const activeTaskSummary = learningTasks.find(
    (item) => item.workspace_id === activeWorkspaceId,
  );

  const receiptPayload = (materialIds?: string[]) => ({
    learning_goal: goal,
    current_level: level,
    current_foundation: level,
    target_level: "complete_mastery",
    start_date: startDate,
    deadline,
    study_weekdays: studyWeekdays,
    fixed_rest_weekdays: [0, 1, 2, 3, 4, 5, 6].filter((day) => !studyWeekdays.includes(day)),
    unavailable_dates: unavailableDates.split(",").map((item) => item.trim()).filter(Boolean),
    special_date_minutes: shortDate ? { [shortDate]: shortMinutes } : {},
    allocation_mode: "system",
    initial_learning_period_ratio: 0.5,
    preferred_learning_style: learningStyle,
    teacher_persona: teacherPersona,
    learner_interests: learnerInterests,
    notes,
    allow_schedule_suggestions: true,
    material_ids: materialIds || materials.filter((item: any) => item.status === "ready").map((item: any) => item.id),
  });
  const readyMaterialIds = materials
    .filter((item: any) => item.status === "ready")
    .map((item: any) => item.id)
    .sort();
  const savedReceipt = snapshot?.receipt;
  const currentConditions = receiptPayload(readyMaterialIds);
  const comparableReceipt = (value: any) => JSON.stringify({
    learning_goal: value?.learning_goal || "",
    current_level: value?.current_level || value?.current_foundation || "",
    start_date: value?.start_date || "",
    deadline: value?.deadline || "",
    study_weekdays: [...(value?.study_weekdays || [0, 1, 2, 3, 4, 5, 6])].sort(),
    unavailable_dates: [...(value?.unavailable_dates || [])].sort(),
    special_date_minutes: value?.special_date_minutes || {},
    preferred_learning_style: value?.preferred_learning_style || "",
    teacher_persona: value?.teacher_persona || "",
    learner_interests: value?.learner_interests || "",
    notes: value?.notes || "",
    material_ids: [...(value?.material_ids || readyMaterialIds)].sort(),
  });
  const taskConditionsChanged = Boolean(savedReceipt)
    && comparableReceipt(currentConditions) !== comparableReceipt(savedReceipt);
  const knowledgeInputsChanged = Boolean(file)
    || Boolean(savedReceipt && (
      currentConditions.current_level
        !== (savedReceipt.current_level || savedReceipt.current_foundation)
      || JSON.stringify(readyMaterialIds)
        !== JSON.stringify([...(savedReceipt.material_ids || readyMaterialIds)].sort())
    ));
  const planNeedsRestoration = Boolean(savedReceipt)
    && graph.knowledge_points.length > 0
    && !plan;
  const taskNeedsRecalculation = taskConditionsChanged || planNeedsRestoration;

  const saveReceipt = (materialIds?: string[]) =>
    activeRequest(`/workspaces/${encodeURIComponent(activeWorkspaceId)}/receipt`, {
      method: "PUT",
      body: JSON.stringify(receiptPayload(materialIds)),
    });

  const advance = async (text = message, understood = false) => {
    if (!session?.session_id) throw new Error("请先启动 AI 教师");
    await activeRequest(`/sessions/${encodeURIComponent(session.session_id)}/advance`, {
      method: "POST",
      body: JSON.stringify({
        message: text,
        understood,
        provider_session_id: realProviderSessionId,
      }),
    });
    setMessage("");
  };

  const generateKnowledgeMap = () =>
    activeRequest(`/workspaces/${encodeURIComponent(activeWorkspaceId)}/knowledge-map/generate`, {
      method: "POST",
      body: JSON.stringify({
        provider_session_id: realProviderSessionId,
      }),
    }, 210_000);

  const generateMasteryPlan = () =>
    activeRequest(`/workspaces/${encodeURIComponent(activeWorkspaceId)}/mastery-plans/generate`, {
      method: "POST",
    });

  const createOrCompleteTask = async () => {
    const uploaded = file
      ? await uploadActiveMaterial(activeWorkspaceId, file)
      : null;
    const ids = Array.from(new Set([
      ...materials.filter((item: any) => item.status === "ready").map((item: any) => item.id),
      ...(uploaded ? [uploaded.id] : []),
    ]));
    if (!ids.length) throw new Error("请先选择并上传一份学习资料");
    await saveReceipt(ids);
    await generateKnowledgeMap();
    await generateMasteryPlan();
    setFile(null);
  };

  const startTeacherLedLesson = async () => {
    let current = await activeRequest(
      `/workspaces/${encodeURIComponent(activeWorkspaceId)}/sessions/start`,
      {
        method: "POST",
        body: JSON.stringify({ provider_session_id: realProviderSessionId }),
      },
    );
    for (let step = 0; step < 4 && !current.state?.autonomy?.awaiting_learner; step += 1) {
      current = await activeRequest(
        `/sessions/${encodeURIComponent(current.session_id)}/teach-next`,
        {
          method: "POST",
          body: JSON.stringify({ provider_session_id: realProviderSessionId }),
        },
      );
    }
    return current;
  };

  const createNewTask = () => {
    const suffix =
      globalThis.crypto?.randomUUID?.().slice(0, 8)
      || `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    setSnapshot(null);
    setFile(null);
    setGoal("完全掌握上传资料，并能用可验证的方式独立应用");
    setCreatingNewTask(true);
    setActiveWorkspaceId(`learning-task-${suffix}`);
  };

  useEffect(() => {
    if (!createRequest) return;
    createNewTask();
    onCreateRequestConsumed?.();
  }, [createRequest]);

  const toggleTaskArchive = async (item: ActiveLearningTaskSummary) => {
    const archived = !item.archived;
    setBusy(archived ? "归档学习任务" : "恢复学习任务");
    setError("");
    try {
      await setActiveLearningTaskArchived(item.workspace_id, archived);
      if (archived && item.workspace_id === activeWorkspaceId) {
        const fallback = learningTasks.find(
          (candidate) => candidate.workspace_id !== item.workspace_id && !candidate.archived,
        );
        if (fallback) {
          setActiveWorkspaceId(fallback.workspace_id);
        } else {
          createNewTask();
        }
      } else {
        await refresh();
      }
    } catch (reason: any) {
      setError(reason?.message || "任务整理失败");
    } finally {
      setBusy("");
    }
  };

  const enterCalendarTask = async (task: any) => {
    let current = await activeRequest(
      `/workspaces/${encodeURIComponent(activeWorkspaceId)}/sessions/start`,
      {
        method: "POST",
        body: JSON.stringify({ provider_session_id: realProviderSessionId }),
      },
    );
    if (current.state?.active_task_id !== task.id) {
      current = await activeRequest(
        `/sessions/${encodeURIComponent(current.session_id)}/focus`,
        {
          method: "POST",
          body: JSON.stringify({ task_id: task.id }),
        },
      );
    }
    for (let step = 0; step < 4 && !current.state?.autonomy?.awaiting_learner; step += 1) {
      current = await activeRequest(
        `/sessions/${encodeURIComponent(current.session_id)}/teach-next`,
        {
          method: "POST",
          body: JSON.stringify({ provider_session_id: realProviderSessionId }),
        },
      );
    }
    window.setTimeout(
      () => {
        const workbench = document.getElementById("active-teacher-workbench");
        if (typeof workbench?.scrollIntoView === "function") {
          workbench.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      },
      0,
    );
    return current;
  };

  const toggleWeekday = (day: number) => {
    setStudyWeekdays((current) =>
      current.includes(day)
        ? current.filter((item) => item !== day)
        : [...current, day].sort((a, b) => a - b),
    );
  };

  const startReview = (reviewId: string) => {
    if (!session?.session_id) throw new Error("请先启动 AI 教师");
    return activeRequest(
      `/sessions/${encodeURIComponent(session.session_id)}/reviews/${encodeURIComponent(reviewId)}/start`,
      {
        method: "POST",
        body: JSON.stringify({
          as_of: reviewAsOf,
          provider_session_id: realProviderSessionId,
        }),
      },
    );
  };

  const submitReview = (assistanceLevel: "none" | "hinted") => {
    if (!session?.session_id || !session.state.active_review?.id) {
      throw new Error("请先启动一项到期复习");
    }
    if (!reviewAnswer.trim()) throw new Error("请输入复习回答");
    return activeRequest(
      `/sessions/${encodeURIComponent(session.session_id)}/reviews/${encodeURIComponent(session.state.active_review.id)}/submit`,
      {
        method: "POST",
        body: JSON.stringify({
          message: reviewAnswer,
          assistance_level: assistanceLevel,
          as_of: reviewAsOf,
          provider_session_id: realProviderSessionId,
        }),
      },
    ).then(() => setReviewAnswer(""));
  };

  const importProblemSpecs = (materialId: string) =>
    activeRequest(`/workspaces/${encodeURIComponent(activeWorkspaceId)}/problems/import`, {
      method: "POST",
      body: JSON.stringify({ material_id: materialId }),
    });

  const practiceRequest = async (mode: "run" | "submit") => {
    if (!session?.session_id || !problemId) throw new Error("请选择可执行题目并启动教师");
    const payload = await activeRequest(
      `/sessions/${encodeURIComponent(session.session_id)}/problems/${encodeURIComponent(problemId)}/${mode}`,
      {
        method: "POST",
        body: JSON.stringify({
          code,
          idempotency_key:
            globalThis.crypto?.randomUUID?.() || `${mode}-${Date.now()}-${Math.random()}`,
        }),
      },
    );
    setCodeResult(payload);
  };

  const saveDraft = async () => {
    if (!session?.session_id || !problemId) throw new Error("请选择可执行题目并启动教师");
    const payload = await activeRequest(
      `/sessions/${encodeURIComponent(session.session_id)}/problems/${encodeURIComponent(problemId)}/draft`,
      {
        method: "PUT",
        body: JSON.stringify({
          language: "python",
          code,
          expected_revision: draftRevision,
        }),
      },
    );
    setDraftRevision(payload.revision);
  };

  const requestHint = async () => {
    if (!session?.session_id || !problemId) throw new Error("请选择可执行题目并启动教师");
    const currentLevel = Number(hint?.level || 0);
    const confirmFullAnswer =
      currentLevel >= 3
        ? window.confirm("第四级提示会公开完整参考实现，确认继续吗？")
        : false;
    const payload = await activeRequest(
      `/sessions/${encodeURIComponent(session.session_id)}/problems/${encodeURIComponent(problemId)}/hints`,
      {
        method: "POST",
        body: JSON.stringify({ confirm_full_answer: confirmFullAnswer }),
      },
    );
    setHint(payload);
  };


  return (
    <section className="active-chain" data-testid="active-teaching-chain">
      <header className="active-chain__hero">
        <div>
          <span className="active-chain__eyebrow">统一学习任务中心</span>
          <h2>建立一次任务，之后每天点开就学</h2>
          <p>每个任务独立保存计划、知识地图、教师记忆和本地 RAG；切换任务不会串资料。</p>
        </div>
        <button data-testid="active-new-task" onClick={createNewTask}>＋ 新建学习任务</button>
      </header>

      {error && <div className="active-chain__error" role="alert">{error}</div>}
      {busy && (
        <div className="active-chain__busy" role="status">
          正在执行：{busy}
          {(busy.includes("学习任务") || busy.includes("知识地图"))
            && realProviderSessionId && readyChunkCount > 24
            ? `；真实 AI 正在并行分析 ${readyChunkCount} 个资料分块，通常需要 1–3 分钟。`
            : ""}
        </div>
      )}

      <div className="active-task-hub" data-testid="active-task-hub">
        <aside className="active-task-hub__rail">
          <div className="active-task-hub__title">
            <div>
              <b>我的学习任务</b>
              <span>{learningTasks.filter((item) => !item.archived).length} 个进行中</span>
            </div>
            <button
              className="active-task-hub__archive-toggle"
              data-testid="active-show-archived"
              onClick={() => setShowArchived((current) => !current)}
            >
              {showArchived ? "隐藏已归档" : "查看已归档"}
            </button>
          </div>
          {learningTasks.length === 0 && (
            <p>还没有任务。上传第一份资料后，系统会自动建立计划。</p>
          )}
          {learningTasks.map((item) => {
            const progress = item.task_count
              ? Math.round(item.completed_task_count / item.task_count * 100)
              : 0;
            return (
              <article
                className={item.workspace_id === activeWorkspaceId ? "is-active" : ""}
                key={item.workspace_id}
              >
                <button
                  className="active-task-hub__select"
                  data-testid={`active-task-switch-${item.workspace_id}`}
                  onClick={() => {
                    setCreatingNewTask(false);
                    setActiveWorkspaceId(item.workspace_id);
                  }}
                >
                  <span><b>{item.title}</b><em>{item.archived ? "已归档" : item.status === "learning" ? "学习中" : item.status === "planned" ? "已规划" : "待完善"}</em></span>
                  <small>{item.knowledge_point_count} 个知识点 · {item.completed_task_count}/{item.task_count} 项</small>
                  <i><span style={{ width: `${progress}%` }} /></i>
                </button>
                <button
                  className="active-task-hub__archive-action"
                  data-testid={`active-task-archive-${item.workspace_id}`}
                  onClick={() => toggleTaskArchive(item)}
                >
                  {item.archived ? "恢复" : "归档"}
                </button>
              </article>
            );
          })}
        </aside>
        <section className="active-task-hub__current">
          <span>当前任务</span>
          <h3>{snapshot?.receipt?.learning_goal || activeTaskSummary?.title || "创建一个新的学习任务"}</h3>
          {plan ? (
            <p>
              计划 {plan.feasible ? "可执行" : "需要调整"} ·
              {graph.knowledge_points.length} 个知识点 ·
              系统分配峰值 {plan.daily_task_minutes_budget} 标准任务分钟/日
            </p>
          ) : (
            <p>填写目标、期限并上传资料，系统一次完成知识地图、估时和排程。</p>
          )}
          <code data-testid="active-workspace-id">{activeWorkspaceId}</code>
        </section>
      </div>

      <div className="active-chain__grid">
        <article className="active-chain__card active-chain__creator-card">
          <span className="active-chain__step">01 · 设置学习任务</span>
          <span className="active-chain__autosave" data-testid="active-draft-status">
            {draftSavedAt
              ? `草稿已自动保存 · ${new Date(draftSavedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
              : "你填写的内容会自动保存在当前浏览器"}
          </span>
          <h3>{plan ? "任务资料与学习条件" : "上传资料，系统自动完成知识地图和计划"}</h3>
          <p className="active-chain__muted">只填写目标、基础和截止日期；每天投入、知识拆解与排程由系统计算。</p>
          <input data-testid="active-material-file" type="file" accept=".md,.markdown,.txt,.pdf,.docx,.html,.htm,.csv,.json,.yaml,.yml,.xml,.rtf,.pptx,.xlsx,.epub,.py,.js,.jsx,.ts,.tsx,.java,.c,.cpp,.h,.go,.rs,.sql,.sh,.ps1" onChange={(event) => setFile(event.target.files?.[0] || null)} />
          <label>学习目标<textarea data-testid="active-goal" value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
          <div className="active-chain__row">
            <label>当前基础<select data-testid="active-foundation" value={level} onChange={(event) => setLevel(event.target.value)}><option value="zero_foundation">零基础</option><option value="beginner">初级</option><option value="intermediate">中级</option><option value="advanced">高级</option></select></label>
            <label>开始日期<input data-testid="active-start-date" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
            <label>截止日期<input data-testid="active-deadline" type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
          </div>
          <div className="active-chain__allocation" data-testid="active-system-allocation">
            <span>系统按阶段分配学习负荷</span>
            <b>
              {plan && snapshot?.receipt?.allocation?.phase_daily_minutes
                ? `前半段首学（${phaseRange(initialPhaseDays)}）：${snapshot.receipt.allocation.phase_daily_minutes.initial_learning}/日；后半段复习（${phaseRange(reviewPhaseDays)}）：${snapshot.receipt.allocation.phase_daily_minutes.review}/日`
                : snapshot?.receipt?.allocation?.recommended_minutes_per_day
                ? `暂估 ${snapshot.receipt.allocation.recommended_minutes_per_day} 标准任务分钟/日`
                : "保存后自动计算"}
            </b>
            <small>两个数字不会在同一天相加：前 50% 的日期只完成完整第一遍，后 50% 的日期才进行滚动复习。</small>
          </div>
          <label>
            AI 教师人设
            <textarea
              data-testid="active-teacher-persona"
              value={teacherPersona}
              onChange={(event) => setTeacherPersona(event.target.value)}
              placeholder="例如：温柔但不敷衍，擅长用生活梗和小故事，把我当零基础学生一步步带会。"
            />
          </label>
          <label>
            我的兴趣或熟悉场景
            <input
              data-testid="active-learner-interests"
              value={learnerInterests}
              onChange={(event) => setLearnerInterests(event.target.value)}
              placeholder="例如：游戏、做饭、购物、篮球；教师会优先从这些生活经验举例"
            />
          </label>
          <details className="active-chain__advanced">
            <summary>高级时间与学习偏好（可选）</summary>
            <fieldset>
              <legend>每周可学习日（未选日期为固定休息日）</legend>
              <div className="active-chain__row" data-testid="active-study-weekdays">
                {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map((label, index) => (
                  <label key={label}>
                    <input
                      type="checkbox"
                      checked={studyWeekdays.includes(index)}
                      onChange={() => toggleWeekday(index)}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="active-chain__row">
              <label>额外不可学习日期（逗号分隔）<input data-testid="active-unavailable-dates" value={unavailableDates} onChange={(event) => setUnavailableDates(event.target.value)} /></label>
              <label>短时学习日<input data-testid="active-short-date" type="date" value={shortDate} onChange={(event) => setShortDate(event.target.value)} /></label>
              <label>短时日分钟<input data-testid="active-short-minutes" type="number" value={shortMinutes} onChange={(event) => setShortMinutes(Number(event.target.value))} /></label>
            </div>
            <label>学习方式<input value={learningStyle} onChange={(event) => setLearningStyle(event.target.value)} /></label>
            <label>备注<textarea data-testid="active-notes" value={notes} onChange={(event) => setNotes(event.target.value)} /></label>
          </details>
          {(!snapshot?.receipt || file) && (
            <button
              data-testid="active-upload"
              disabled={(!file && !materials.some((item: any) => item.status === "ready")) || Boolean(busy)}
              onClick={() => run("创建学习任务", createOrCompleteTask)}
            >
              {busy === "创建学习任务"
                ? `正在分析 ${readyChunkCount || "资料"} 个分块…`
                : file
                  ? materials.length
                    ? "添加资料并生成知识地图与计划"
                    : "上传资料并创建任务"
                  : "使用已保存资料继续创建任务"}
            </button>
          )}
          {busy === "创建学习任务" && realProviderSessionId && readyChunkCount > 24 && (
            <div className="active-chain__busy active-chain__busy--inline" data-testid="active-create-progress">
              已保存你的填写内容。正在调用真实 AI 分批建立知识地图，完成后会自动生成计划；超过 3 分 30 秒会自动解除按钮，不会一直锁住。
            </div>
          )}
          {snapshot?.receipt && (
            <button
              data-testid="active-save-receipt"
              disabled={!materials.length || Boolean(file) || Boolean(busy) || !taskNeedsRecalculation}
              onClick={() => run("更新任务条件与计划", async () => {
                await saveReceipt();
                if (knowledgeInputsChanged) {
                  await generateKnowledgeMap();
                }
                await generateMasteryPlan();
              })}
            >
              {planNeedsRestoration
                ? "恢复当前知识地图的学习计划"
                : knowledgeInputsChanged
                  ? "保存更改并重建知识地图与计划"
                  : taskConditionsChanged
                    ? "保存更改并重新排程"
                    : "当前设置未变化，无需重新计算"}
            </button>
          )}
          <div data-testid="active-material-count">已保存资料 {materials.length} 份</div>
          <div className="active-chain__receipt" data-testid="active-local-knowledge-base">
            <b>
              当前资料库：
              {localKnowledgeBase?.status === "ready" ? "已索引，可检索" : "等待资料"}
            </b>
            <span>
              {localKnowledgeBase?.material_count || 0} 份资料 · {localKnowledgeBase?.chunk_count || 0} 个可引用分块 ·
              {localKnowledgeBase?.indexed_characters || 0} 字符
            </span>
            {localKnowledgeBase?.status === "ready" && (
              <span>
                已索引只表示可以引用原文分块，不代表资料包含完整答案，也不代表知识地图和计划已经生成。
              </span>
            )}
            {materials.length > 0 && !snapshot?.receipt && (
              <span className="active-chain__warning">
                资料已经保存，但任务尚未建立；请使用上方按钮继续创建，无需重新选择文件。
              </span>
            )}
            <span>
              {localKnowledgeBase?.scope === "current_plan"
                ? `已冻结到计划 ${localKnowledgeBase?.plan_id || ""}，不会检索其他任务`
                : "计划建立后自动冻结资料范围"}
            </span>
          </div>
          {materials.map((item: any) => (
            <div className="active-chain__receipt" key={item.id}>
              <b>{item.original_filename}</b>
              <span>{item.chunk_count} chunks · SHA-256 {item.sha256.slice(0, 12)}…</span>
              {item.original_filename.toLowerCase().endsWith(".json") && graph.knowledge_points.length > 0 && (
                <button disabled={Boolean(busy)} onClick={() => run("导入资料内的可执行题目规格", () => importProblemSpecs(item.id))}>
                  导入经来源验证的题目与测试
                </button>
              )}
            </div>
          ))}
        </article>

        <article className="active-chain__card">
          <span className="active-chain__step">02 · 计划知识地图</span>
          <h3>先看清要学什么，再进入日历</h3>
          <button
            data-testid="active-generate-graph"
            disabled={!snapshot?.receipt || Boolean(busy) || graph.knowledge_points.length > 0}
            onClick={() => run("从资料生成知识地图", generateKnowledgeMap)}
          >
            {graph.knowledge_points.length > 0
              ? "知识地图已生成（资料未变化，无需重复生成）"
              : "从上传资料生成知识地图"}
          </button>
          {graph.knowledge_points.length > 0 && (
            <p className="active-chain__muted">
              修改目标、日期、教师人设或学习偏好只会重新排程，不会再次调用模型抽取；上传新资料时才重建知识地图。
            </p>
          )}
          <div className="active-chain__receipt" data-testid="active-extraction-mode">
            <b>抽取模式：{graph.extraction?.mode || "尚未生成"}</b>
            <span>
              {graph.extraction?.mode === "provider_grounded"
                ? "已验证真实 Provider 参与语义抽取，结果仍经过来源与结构校验。"
                : "未配置真实 Provider 时使用可审计结构解析，不冒充 AI 语义抽取。"}
            </span>
            {(graph.extraction?.warnings || []).map((warning: string) => <span key={warning}>{warning}</span>)}
          </div>
          <div className="active-chain__metrics">
            <strong data-testid="active-point-count">{graph.knowledge_points.length}</strong><span>知识点</span>
            <strong data-testid="active-edge-count">{graph.edges.length}</strong><span>前置依赖</span>
          </div>
          {graph.assessment && (
            <div className="active-chain__receipt" data-testid="active-material-assessment">
              <b>
                通用资料评估：
                {graph.assessment.readiness?.status === "ready"
                  ? "可直接生成计划"
                  : graph.assessment.readiness?.status === "usable_with_review"
                    ? graph.assessment.verification?.executable_verification_ready === false
                      ? "可排程，完整验收尚未就绪"
                      : "可排程，建议语义复核"
                    : "暂不可排程"}
              </b>
              <span>
                {graph.assessment.source?.material_count || 0} 份资料 ·
                {graph.assessment.source?.chunk_count || 0} 个分块 ·
                {graph.assessment.knowledge_structure?.knowledge_point_count || 0} 个知识点 ·
                证据覆盖 {Math.round((graph.assessment.knowledge_structure?.evidence_coverage_ratio || 0) * 100)}%
              </span>
              <span>
                系统估算完全掌握 {graph.assessment.workload?.total_mastery_task_minutes || 0} 任务分钟：
                第一遍 {graph.assessment.workload?.initial_learning_task_minutes || 0} ·
                复习 {graph.assessment.workload?.review_task_minutes || 0}
              </span>
              {(graph.assessment.verification?.executable_required_point_count || 0) > 0 && (
                <span data-testid="active-executable-readiness">
                  可执行验收：
                  {graph.assessment.verification?.system_verified_problem_point_count || 0}/
                  {graph.assessment.verification?.executable_required_point_count || 0}
                  个要求编码的知识点已绑定系统验证题目
                </span>
              )}
              {(graph.assessment.verification?.system_verified_problem_point_count || 0) > 0 && (
                <span data-testid="active-independent-oracle-readiness">
                  独立答案依据：
                  {graph.assessment.verification?.independent_oracle_problem_point_count || 0}/
                  {graph.assessment.verification?.executable_required_point_count || 0}
                  个编码知识点具备来源与独立测试证据
                </span>
              )}
              {(graph.assessment.readiness?.risks || []).map((risk: string) => (
                <span key={risk}>{risk}</span>
              ))}
            </div>
          )}
          <div className="active-chain__knowledge-map" data-testid="active-knowledge-map">
            {graph.knowledge_points.map((point: any) => {
              const prerequisites = graph.edges
                .filter((edge: any) => edge.dependent_id === point.id)
                .map((edge: any) => graph.knowledge_points.find((candidate: any) => candidate.id === edge.prerequisite_id)?.name)
                .filter(Boolean);
              return (
                <div className="active-chain__knowledge-node" key={point.id}>
                  <span>{prerequisites.length ? `前置：${prerequisites.join("、")}` : "起点知识"}</span>
                  <b>{point.name}</b>
                  <small>
                    {point.difficulty} ·
                    {point.mastery_estimate?.total_mastery_task_minutes || point.estimated_task_minutes} 分钟 ·
                    掌握 {(point.current_mastery * 100).toFixed(0)}%
                  </small>
                  <i style={{ width: `${Math.max(4, point.current_mastery * 100)}%` }} />
                </div>
              );
            })}
            {!graph.knowledge_points.length && <p>任务建立后，知识结构会完整显示在这里。</p>}
          </div>
        </article>

        <article className="active-chain__card">
          <span className="active-chain__step">03 · 每天安排</span>
          <h3>点击任意首学任务，直接进入对应课程</h3>
          <button data-testid="active-generate-plan" disabled={!graph.knowledge_points.length || Boolean(busy)} onClick={() => run("生成完全掌握计划", generateMasteryPlan)}>生成完全掌握计划</button>
          {plan && (
            <>
              <div className="active-chain__metrics">
                <strong data-testid="active-plan-budget">{plan.daily_task_minutes_budget}</strong><span>系统日负荷峰值</span>
                <strong>{plan.tasks.length}</strong><span>可执行任务</span>
                <strong>{plan.unscheduled_task_minutes}</strong><span>未排分钟</span>
                <strong data-testid="active-plan-feasible">{plan.feasible ? "可行" : "不可行"}</strong><span>容量结论</span>
              </div>
              <div className="active-chain__time">
                <span>完全掌握预计 {plan.total_mastery_task_minutes ?? plan.total_estimated_task_minutes ?? plan.tasks.reduce((sum: number, item: any) => sum + item.estimated_task_minutes, 0)} 任务分钟</span>
                <span>第一遍预计 {plan.initial_learning_task_minutes ?? plan.tasks.reduce((sum: number, item: any) => sum + item.estimated_task_minutes, 0)} 分钟</span>
                <span>后半正式复习预计 {plan.projected_review_task_minutes ?? 0} 分钟；分钟占比不代表阶段占比</span>
                {plan.expected_actual_elapsed_minutes_range && (
                  <span data-testid="active-expected-elapsed-range">
                    预计真实耗时范围 {plan.expected_actual_elapsed_minutes_range.min}–
                    {plan.expected_actual_elapsed_minutes_range.max} 分钟（不是任务完成量）
                  </span>
                )}
                <span>完成任务分钟 {plan.tasks.reduce((sum: number, item: any) => sum + item.completed_task_minutes, 0)}</span>
                <span>实际耗时分钟 {plan.tasks.reduce((sum: number, item: any) => sum + item.actual_elapsed_minutes, 0).toFixed(3)}</span>
              </div>
              {plan.review_scheduling_policy && <p className="active-chain__muted">{plan.review_scheduling_policy}</p>}
              {plan.warning && <div className="active-chain__warning">{plan.warning}</div>}
              {plan.calendar && (
                <div className="active-chain__calendar" data-testid="active-plan-calendar">
                  {plan.calendar
                    .map((day: any) => (
                    <div className={`active-chain__day ${day.capacity_minutes === 0 ? "is-rest" : ""} ${day.tasks.some((task: any) => task.id === session?.state?.active_task_id) ? "is-current" : ""}`} key={day.date}>
                      <b>{day.date}</b>
                      <span>{day.availability_reason}</span>
                      <span>{day.study_phase === "initial_learning" ? "首学阶段" : day.study_phase === "review" ? "复习阶段" : "休息"}</span>
                      <span>容量 {day.capacity_minutes} · 计划 {day.planned_task_minutes}</span>
                      {day.study_phase === "review" && (
                        <span>
                          预留复习 {day.projected_review_task_minutes || 0} 分钟 ·
                          预计覆盖 {(day.projected_review_items || []).length} 个知识点
                        </span>
                      )}
                      {day.expected_actual_elapsed_minutes_range && (
                        <span>
                          预计实际 {day.expected_actual_elapsed_minutes_range.min}–
                          {day.expected_actual_elapsed_minutes_range.max} 分钟
                        </span>
                      )}
                      <span>
                        首学知识点 {new Set(day.tasks.filter((task: any) => task.task_type === "initial_learning").map((task: any) => task.knowledge_point_id)).size} ·
                        复习 {day.tasks.filter((task: any) => task.task_type === "review").length} ·
                        补救 {day.tasks.filter((task: any) => task.task_type === "remediation").length}
                      </span>
                      {day.tasks.map((task: any, taskIndex: number) => (
                        task.task_type === "initial_learning" ? (
                          <button
                            className="active-chain__calendar-task"
                            data-testid={`active-calendar-task-${task.id}`}
                            disabled={Boolean(busy)}
                            key={task.id || `${day.date}-${task.knowledge_point_id}-${task.learning_phase}-${taskIndex}`}
                            onClick={() => run(`进入 ${task.title}`, () => enterCalendarTask(task))}
                          >
                            <b>{task.title}</b>
                            <small>{task.estimated_task_minutes} 分钟 · {task.status}</small>
                            <span>进入学习 →</span>
                          </button>
                        ) : (
                          <small key={task.id || `${day.date}-${task.knowledge_point_id}-${task.learning_phase}-${taskIndex}`}>
                            {task.title} · {task.estimated_task_minutes} 分钟 · {task.status}
                          </small>
                        )
                      ))}
                      {(day.projected_review_items || []).slice(0, 4).map((item: any) => (
                        <small key={`projected-${day.date}-${item.knowledge_point_id}`}>
                          预计复习：{item.title} · {item.projected_review_task_minutes} 分钟
                        </small>
                      ))}
                      {(day.projected_review_items || []).length > 4 && (
                        <small>另有 {(day.projected_review_items || []).length - 4} 个知识点；将按首学验收结果滚动调整</small>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </article>
      </div>

      <article className="active-chain__teacher" id="active-teacher-workbench">
        <div className="active-chain__teacher-head">
          <div><span className="active-chain__step">04 · 今日学习</span><h3>当前任务专属教师 · 计划 RAG 与记忆不会串线</h3></div>
          <button data-testid="active-start-teacher" disabled={!plan || Boolean(busy)} onClick={() => run("启动今日主动课程", startTeacherLedLesson)}>开始今日课程（教师主动带学）</button>
          {session && <button data-testid="active-replan" disabled={Boolean(busy)} onClick={() => run("滚动未完成任务", () => activeRequest(`/sessions/${encodeURIComponent(session.session_id)}/replan`, {
            method: "POST",
            body: JSON.stringify({
              trigger: "manual_rollover",
              reason: "用户要求从今天滚动未完成任务",
              affected_from: isoAfterDays(0),
            }),
          }))}>从今天滚动未完成任务</button>}
        </div>
        {session && (
          <>
            <div className="active-chain__state" data-testid="active-session-state">
              <span>步骤 <b>{session.state.current_teaching_step}</b></span>
              <span>路线 <b>{session.state.route_index + 1}/{session.state.route_length}</b></span>
              <span>今日剩余 <b>{session.state.today_remaining_task_minutes}</b> 任务分钟</span>
              <span>补救栈 <b>{session.state.remediation_stack.length}</b></span>
              <span>实际耗时 <b>{session.actual_elapsed_minutes}</b> 分钟</span>
              <span>检索 <b>{session.state.last_retrieval?.mode || "local_hybrid_v1"}</b></span>
              <span>
                教师状态 <b>{session.state.autonomy?.awaiting_learner ? "等待你的最小回答" : "主动讲授中"}</b>
              </span>
            </div>
            {session.state.today_plan && (
              <div className="active-chain__receipt" data-testid="active-today-plan">
                <b>{session.state.today_plan.date} · 今日课程</b>
                <span>{session.state.today_plan.knowledge_point_count ?? session.state.today_plan.task_count} 个知识点 · {session.state.today_plan.task_minutes} 标准任务分钟</span>
                <span>教师会先建立知识框架并检索讲解，然后主动邀请你多轮追问；确认没有疑问后才进入练习与验收。</span>
              </div>
            )}
            {(session.state.replan_history || []).length > 0 && (
              <div className="active-chain__receipt" data-testid="active-replan-summary">
                <b>最近一次自动重排</b>
                <span>{session.state.replan_history.at(-1).trigger} · {session.state.replan_history.at(-1).reason}</span>
                <span>
                  移动 {session.state.replan_history.at(-1).moved_task_ids.length} 项 ·
                  未排 {session.state.replan_history.at(-1).unscheduled_task_minutes} 分钟
                </span>
              </div>
            )}
            {(session.state.mastery_evidence || []).length > 0 && (
              <div className="active-chain__receipt" data-testid="active-mastery-evidence">
                <b>最近掌握证据</b>
                {session.state.mastery_evidence.slice(-5).map((item: any, index: number) => (
                  <span key={`${item.knowledge_point_id}-${index}`}>
                    {item.learning_phase || (item.review_item_id ? "延迟复测" : "理解检测")} ·
                    {item.passed ? "通过" : "未通过"} · {(Number(item.score || 0) * 100).toFixed(0)}%
                    {item.assistance_level && ` · assistance=${item.assistance_level}`}
                  </span>
                ))}
              </div>
            )}
            <div className="active-chain__history" data-testid="active-conversation-history">
              {(session.messages || []).slice(-6).map((item: any) => (
                <div
                  className={`active-chain__message ${item.role === "user" ? "is-user" : ""}`}
                  data-testid={item.id === latestTeacher?.id ? "active-latest-teacher" : undefined}
                  key={item.id}
                >
                  <span>{item.role === "user" ? "你" : item.action}</span><p>{item.content}</p>
                  {(item.citations || []).map((citation: any) => (
                    <blockquote key={citation.chunk_id}>{citation.quote}<small>段落 {citation.paragraph_start} · 字符 {citation.char_start}-{citation.char_end} · {citation.content_hash.slice(0, 12)}…</small></blockquote>
                  ))}
                </div>
              ))}
            </div>
            <div className="active-chain__composer">
              <input data-testid="active-teacher-input" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="例如：我不理解，为什么？" />
              <button data-testid="active-send-teacher" disabled={!message || Boolean(busy)} onClick={() => run("推进教学", () => advance())}>发送</button>
              <button data-testid="active-understood" disabled={Boolean(busy)} onClick={() => run("提交理解证据", () => advance(message || "我理解了，可以继续", true))}>我已理解并接受检测</button>
            </div>
            <div className="active-chain__receipt" data-testid="active-review-queue">
              <b>持久化复习队列</b>
              <label>
                验收学习日
                <input
                  data-testid="active-review-as-of"
                  type="date"
                  value={reviewAsOf}
                  onChange={(event) => setReviewAsOf(event.target.value)}
                />
              </label>
              <span>
                正式复习门禁：
                {session.state.formal_review_gate?.unlocked
                  ? "已完成第一遍，可以按后半阶段日历执行"
                  : `未解锁，第一遍仍有 ${session.state.formal_review_gate?.incomplete_initial_knowledge_point_count ?? "未知"} 个知识点未完成`}
                ；每天学习后的巩固不计入这里。
              </span>
              {(session.state.pending_reviews || []).length === 0 && <span>当前没有待执行的正式复习。</span>}
              {(session.state.pending_reviews || []).map((review: any) => (
                <div key={review.id}>
                  <span>
                    {review.knowledge_point_name || review.knowledge_point_id} ·
                    到期 {review.due_date} · 间隔 {review.interval_days} 天 ·
                    排期 {review.scheduled_date || "容量外"} ·
                    本轮 {review.estimated_task_minutes} 分钟 ·
                    第 {review.success_streak + 1} 次独立复测
                  </span>
                  <button
                    data-testid={`active-start-review-${review.id}`}
                    disabled={Boolean(busy) || review.status === "in_progress" || !session.state.formal_review_gate?.unlocked}
                    onClick={() => run("启动到期复习", () => startReview(review.id))}
                  >
                    {review.status === "in_progress" ? "复习进行中" : "启动复习"}
                  </button>
                </div>
              ))}
              {session.state.active_review && (
                <div className="active-chain__composer">
                  <input
                    data-testid="active-review-answer"
                    value={reviewAnswer}
                    onChange={(event) => setReviewAnswer(event.target.value)}
                    placeholder="不看答案，写下延迟复测回答"
                  />
                  <button
                    data-testid="active-submit-review"
                    disabled={!reviewAnswer.trim() || Boolean(busy)}
                    onClick={() => run("提交独立复测", () => submitReview("none"))}
                  >
                    独立提交
                  </button>
                  <button
                    data-testid="active-submit-hinted-review"
                    disabled={!reviewAnswer.trim() || Boolean(busy)}
                    onClick={() => run("记录提示后完成", () => submitReview("hinted"))}
                  >
                    使用提示后提交
                  </button>
                </div>
              )}
            </div>
            <div className="active-chain__receipt" data-testid="active-code-practice">
              <b>真实代码练习与测试</b>
              {problems.length === 0 ? (
                <span>
                  当前资料没有经验证的结构化测试包。系统不会让模型臆造标准答案或伪造测试通过；
                  可上传包含 problem_specs 的 JSON 资料后导入。
                </span>
              ) : (
                <>
                  <label>
                    题目
                    <select
                      data-testid="active-code-problem"
                      value={problemId}
                      onChange={(event) => setProblemId(event.target.value)}
                    >
                      {problems.map((problem: any) => (
                        <option key={problem.id} value={problem.id}>
                          {problem.title} · {problem.language}
                        </option>
                      ))}
                    </select>
                  </label>
                  <span>
                    样例 {problems.find((item: any) => item.id === problemId)?.test_summary?.sample || 0} ·
                    边界 {problems.find((item: any) => item.id === problemId)?.test_summary?.boundary || 0} ·
                    隐藏 {problems.find((item: any) => item.id === problemId)?.test_summary?.hidden || 0}
                  </span>
                  <textarea
                    data-testid="active-code-editor"
                    className="active-chain__code"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    spellCheck={false}
                  />
                  <div className="active-chain__composer">
                    <button disabled={Boolean(busy)} onClick={() => run("保存代码草稿", saveDraft)}>
                      保存草稿
                    </button>
                    <button data-testid="active-run-code" disabled={Boolean(busy)} onClick={() => run("运行样例与边界测试", () => practiceRequest("run"))}>
                      运行
                    </button>
                    <button data-testid="active-submit-code" disabled={Boolean(busy)} onClick={() => run("提交全部测试", () => practiceRequest("submit"))}>
                      提交
                    </button>
                    <button data-testid="active-code-hint" disabled={Boolean(busy)} onClick={() => run("获取下一层提示", requestHint)}>
                      下一层提示
                    </button>
                  </div>
                  {hint && <pre data-testid="active-code-hint-content">提示 {hint.level}：{hint.content}</pre>}
                  {codeResult && (
                    <div data-testid="active-code-result">
                      <b>{codeResult.status}</b>
                      <span>
                        通过 {codeResult.passed_count}/{codeResult.total_count} ·
                        隐藏测试 {codeResult.hidden_passed_count}/{codeResult.hidden_total_count} ·
                        {codeResult.duration_ms} ms
                      </span>
                      {(codeResult.results || []).map((result: any) => (
                        <span key={`${result.kind}-${result.ordinal}`}>
                          {result.kind} #{result.ordinal + 1}: {result.passed ? "通过" : result.error || "答案错误"}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )}
      </article>
      {completionReport && (
        <article className="active-chain__teacher" data-testid="active-completion-report">
          <div className="active-chain__teacher-head">
            <div>
              <span className="active-chain__step">06 · 权威完成度报告</span>
              <h3>{completionReport.verified_complete ? "已满足全部验证门" : "仍在学习或验证中"}</h3>
            </div>
            <b>{completionReport.status}</b>
          </div>
          <div className="active-chain__metrics">
            <strong>{completionReport.time.planned_task_minutes}</strong><span>计划任务分钟</span>
            <strong>{completionReport.time.completed_task_minutes}</strong><span>完成任务分钟</span>
            <strong>{completionReport.time.actual_elapsed_minutes}</strong><span>实际耗时分钟</span>
            <strong>{completionReport.time.variance_minutes}</strong><span>耗时差异</span>
          </div>
          <p className="active-chain__muted">{completionReport.time.variance_reason}</p>
          <div className="active-chain__receipt">
            <b>完成门禁</b>
            {Object.entries(completionReport.gates).map(([gate, passed]) => (
              <span key={gate}>{passed ? "✓" : "○"} {gate}</span>
            ))}
          </div>
          <div className="active-chain__receipt" data-testid="active-mistake-queue">
            <b>持久化错题队列</b>
            {completionReport.mistake_queue.length === 0 && <span>暂无代码错误提交。</span>}
            {completionReport.mistake_queue.map((item: any) => (
              <span key={item.submission_id}>
                {item.problem_title} · {item.status} · {item.passed_count}/{item.total_count} ·
                {item.resolved ? "已由后续独立通过解决" : "待重做"}
              </span>
            ))}
          </div>
        </article>
      )}
    </section>
  );
}
