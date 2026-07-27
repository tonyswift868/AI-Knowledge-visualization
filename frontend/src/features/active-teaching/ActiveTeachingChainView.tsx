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
import {
  TeacherCompanionPortrait,
  type TeacherCompanionState,
} from "../teachers/TeacherCompanionStage";
import "../teachers/teacher-avatar.css";
import "./active-teaching.css";

const isoAfterDays = (days: number) => {
  const value = new Date(Date.now() + days * 86400000);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
};
const ACTIVE_WORKSPACE_KEY = "mentorflow_active_teaching_workspace_v1";
const ACTIVE_DRAFT_PREFIX = "mentorflow_active_task_draft_v1:";

const teacherActionLabels: Record<string, string> = {
  daily_briefing: "今天先学这一点",
  explain: "讲解",
  invite_questions: "轮到你提问",
  grounded_answer: "回答你的问题",
  guided_practice: "小练习",
  check_understanding: "理解检查",
  remediation: "换一种方式讲",
  remediation_example: "补充示例",
  praise: "做得很好",
};

const readableMessageParagraphs = (content: string) => {
  const source = String(content || "").trim();
  if (!source) return [];
  const paragraphs = source.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean);
  return paragraphs.flatMap((paragraph) => {
    if (paragraph.length <= 160) return [paragraph];
    const sentences = paragraph.match(/[^。！？!?；;]+[。！？!?；;]?/g) || [paragraph];
    const chunks: string[] = [];
    let current = "";
    for (const sentence of sentences) {
      if (current && current.length + sentence.length > 160) {
        chunks.push(current.trim());
        current = "";
      }
      current += sentence;
    }
    if (current.trim()) chunks.push(current.trim());
    return chunks;
  });
};

type ActiveTaskDraft = {
  goal?: string;
  level?: string;
  startDate?: string;
  deadline?: string;
  reviewRounds?: number;
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

type PendingUserMessage = {
  id: string;
  content: string;
  status: "processing" | "error";
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

const groupDailyTasks = (tasks: any[] = []) => {
  const groups = new Map<string, any>();
  for (const task of tasks) {
    const key = `${task.task_type || "task"}:${task.knowledge_point_id || task.id}`;
    const current = groups.get(key) || {
      key,
      knowledgePointId: task.knowledge_point_id,
      title: String(task.knowledge_point_name || task.title || "学习任务").replace(
        /\s*·\s*(理解|回忆|引导练习|独立应用).*$/,
        "",
      ),
      difficulty: task.difficulty || "",
      prerequisiteNames: task.prerequisite_names || [],
      pointSequence: Number(task.point_sequence || 0),
      taskType: task.task_type,
      tasks: [],
      minutes: 0,
    };
    current.tasks.push(task);
    current.minutes += Number(task.estimated_task_minutes || 0);
    groups.set(key, current);
  }
  const grouped = Array.from(groups.values())
    .map((group) => {
      const completed = group.tasks.filter(
        (task: any) => task.status === "completed",
      ).length;
      return {
        ...group,
        completed,
        nextTask:
          group.tasks.find((task: any) => task.status !== "completed")
          || group.tasks[0],
      };
    });
  const byTitle = new Map(
    grouped.map((group) => [String(group.title).trim(), group]),
  );
  const outgoing = new Map(grouped.map((group) => [group.key, [] as string[]]));
  const indegree = new Map(grouped.map((group) => [group.key, 0]));
  for (const group of grouped) {
    for (const prerequisiteName of group.prerequisiteNames) {
      const prerequisite = byTitle.get(String(prerequisiteName).trim());
      if (!prerequisite || prerequisite.key === group.key) continue;
      outgoing.get(prerequisite.key)?.push(group.key);
      indegree.set(group.key, Number(indegree.get(group.key) || 0) + 1);
    }
  }
  const difficultyRank = (difficulty: string) => {
    const value = String(difficulty || "").toLowerCase();
    if (/(easy|简单|基础)/.test(value)) return 0;
    if (/(hard|困难|高级)/.test(value)) return 2;
    return 1;
  };
  const compare = (left: any, right: any) =>
    difficultyRank(left.difficulty) - difficultyRank(right.difficulty)
    || left.pointSequence - right.pointSequence
    || String(left.title).localeCompare(String(right.title), "zh-CN");
  const byKey = new Map(grouped.map((group) => [group.key, group]));
  const ready = grouped
    .filter((group) => indegree.get(group.key) === 0)
    .sort(compare);
  const ordered: any[] = [];
  while (ready.length) {
    const group = ready.shift();
    ordered.push(group);
    for (const targetKey of outgoing.get(group.key) || []) {
      const remaining = Number(indegree.get(targetKey) || 0) - 1;
      indegree.set(targetKey, remaining);
      if (remaining === 0) {
        ready.push(byKey.get(targetKey));
        ready.sort(compare);
      }
    }
  }
  if (ordered.length < grouped.length) {
    ordered.push(
      ...grouped.filter((group) => !ordered.includes(group)).sort(compare),
    );
  }
  return ordered;
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
  const [showPlanDetails, setShowPlanDetails] = useState(false);
  const [showLesson, setShowLesson] = useState(false);
  const [expandedDailyPlans, setExpandedDailyPlans] = useState<string[]>([]);
  const [selectedStudyDate, setSelectedStudyDate] = useState(isoAfterDays(0));
  const [creatingNewTask, setCreatingNewTask] = useState(() =>
    Boolean(readTaskDraft(activeWorkspaceId)),
  );
  const [file, setFile] = useState<File | null>(null);
  const [goal, setGoal] = useState("完全掌握上传资料，并能用可验证的方式独立应用");
  const [level, setLevel] = useState("beginner");
  const [startDate, setStartDate] = useState(isoAfterDays(0));
  const [deadline, setDeadline] = useState(isoAfterDays(90));
  const [reviewRounds, setReviewRounds] = useState(2);
  const [studyWeekdays, setStudyWeekdays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]);
  const [unavailableDates, setUnavailableDates] = useState("");
  const [shortDate, setShortDate] = useState("");
  const [shortMinutes, setShortMinutes] = useState(0);
  const [notes, setNotes] = useState("");
  const [learningStyle, setLearningStyle] = useState("主动讲解、示例、理解检查、练习与复习");
  const [teacherPersona, setTeacherPersona] = useState("");
  const [learnerInterests, setLearnerInterests] = useState("");
  const [message, setMessage] = useState("");
  const [pendingUserMessages, setPendingUserMessages] = useState<PendingUserMessage[]>([]);
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
  const conversationHistoryRef = useRef<HTMLDivElement | null>(null);

  const refresh = async (workspaceOverride?: string) => {
    const requestedWorkspaceId = workspaceOverride || activeWorkspaceId;
    const requestSequence = ++refreshSequence.current;
    const [next, taskItems] = await Promise.all([
      activeSnapshot(requestedWorkspaceId),
      activeLearningTasks(showArchived),
    ]);
    let problemItems: any[] = [];
    try {
      const problemPayload = await activeRequest(
        `/workspaces/${encodeURIComponent(requestedWorkspaceId)}/problems`,
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
      && !taskItems.some((item) => item.workspace_id === requestedWorkspaceId)
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
    const storedDraft = readTaskDraft(requestedWorkspaceId);
    if (next.receipt && !storedDraft) {
      setGoal(next.receipt.learning_goal);
      setLevel(next.receipt.current_level);
      setStartDate(next.receipt.start_date || isoAfterDays(0));
      setDeadline(next.receipt.deadline);
      setReviewRounds(Number(next.receipt.review_rounds || 2));
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
    if (!storedDraft) setDraftLoadedWorkspace(requestedWorkspaceId);
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
      if (typeof draft.reviewRounds === "number") setReviewRounds(draft.reviewRounds);
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
      setDraftLoadedWorkspace(activeWorkspaceId);
    }
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
        reviewRounds,
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
    reviewRounds,
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
    let operationError: any = null;
    try {
      await operation();
    } catch (reason: any) {
      operationError = reason;
      setError(reason?.message || "操作失败");
    } finally {
      try {
        await refresh();
      } catch (refreshError: any) {
        if (!operationError) {
          setError(refreshError?.message || "操作已完成，但刷新最新状态失败");
        }
      }
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
    (day: any) => ["initial_learning", "transition"].includes(day.study_phase),
  );
  const reviewPhaseDays = (plan?.calendar || []).filter(
    (day: any) => ["review", "transition"].includes(day.study_phase),
  );
  const transitionDays = (plan?.calendar || []).filter(
    (day: any) => day.study_phase === "transition",
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
  const currentKnowledgePoint = graph.knowledge_points.find(
    (item: any) => item.id === session?.state?.current_knowledge_point_id,
  );
  const teacherPresentationState = useMemo<TeacherCompanionState>(() => {
    if (busy) return "thinking";
    const action = String(latestTeacher?.action || "");
    if (/praise|pass|complete/.test(action)) return "praising";
    if (/question|check/.test(action)) return "questioning";
    if (/remediation|confus|encourag/.test(action)) return "encouraging";
    return session ? "explaining" : "idle";
  }, [busy, latestTeacher?.action, session]);
  useEffect(() => {
    if (!showLesson) return;
    const frame = window.requestAnimationFrame(() => {
      const history = conversationHistoryRef.current;
      if (history) history.scrollTop = history.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [latestTeacher?.id, pendingUserMessages.length, showLesson]);
  const activeTaskSummary = learningTasks.find(
    (item) => item.workspace_id === activeWorkspaceId,
  );
  const selectedStudyDay = (plan?.calendar || []).find(
    (day: any) => day.date === selectedStudyDate,
  );
  const dailyTaskGroups = useMemo(
    () => groupDailyTasks(selectedStudyDay?.tasks || []),
    [selectedStudyDay],
  );
  const selectedDayRemainingMinutes = (selectedStudyDay?.tasks || []).reduce(
    (total: number, task: any) =>
      total
      + Math.max(
        0,
        Number(task.estimated_task_minutes || 0)
          - Number(task.completed_task_minutes || 0),
      ),
    0,
  );
  const dailyPlanItems = useMemo(() => {
    const items = learningTasks
      .filter((item) => !item.archived && item.plan_id)
      .map((item) => {
        const fallbackDay =
          item.workspace_id === activeWorkspaceId ? selectedStudyDay : null;
        const todayPlan = item.today_plan || (fallbackDay
          ? {
              ...fallbackDay,
              remaining_task_minutes: selectedDayRemainingMinutes,
            }
          : null);
        return {
          workspaceId: item.workspace_id,
          title: item.title,
          planId: item.plan_id,
          sessionId: item.session_id,
          todayPlan,
          groups: groupDailyTasks(todayPlan?.tasks || []),
        };
      });
    if (plan && !items.some((item) => item.workspaceId === activeWorkspaceId)) {
      items.unshift({
        workspaceId: activeWorkspaceId,
        title:
          snapshot?.receipt?.learning_goal
          || activeTaskSummary?.title
          || "当前学习计划",
        planId: plan.id,
        sessionId: session?.session_id || null,
        todayPlan: selectedStudyDay
          ? {
              ...selectedStudyDay,
              remaining_task_minutes: selectedDayRemainingMinutes,
              learning_order_policy: (
                "严格先满足前置依赖；同一时刻可学习的知识点按简单、"
                + "中等、困难排序，难度相同再按资料原始顺序。"
              ),
            }
          : null,
        groups: dailyTaskGroups,
      });
    }
    return items;
  }, [
    activeTaskSummary?.title,
    activeWorkspaceId,
    dailyTaskGroups,
    learningTasks,
    plan,
    selectedDayRemainingMinutes,
    selectedStudyDay,
    session?.session_id,
    snapshot?.receipt?.learning_goal,
  ]);
  const dailyPlanTaskCount = dailyPlanItems.reduce(
    (total, item) => total + item.groups.length,
    0,
  );

  useEffect(() => {
    const days = plan?.calendar || [];
    if (!days.length) return;
    const today = isoAfterDays(0);
    const nextDate =
      days.find((day: any) => day.date === today)?.date
      || days.find((day: any) =>
        (day.tasks || []).some((task: any) => task.status !== "completed")
      )?.date
      || days[0].date;
    setSelectedStudyDate(nextDate);
    setShowPlanDetails(false);
  }, [activeWorkspaceId, plan?.id]);

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
    review_rounds: reviewRounds,
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
    review_rounds: Number(value?.review_rounds || 2),
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
      JSON.stringify(readyMaterialIds)
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
    const content = String(text || "").trim();
    if (!content) throw new Error("请先输入你想说的话");
    const pendingId =
      globalThis.crypto?.randomUUID?.()
      || `pending-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setPendingUserMessages((current) => [
      ...current,
      { id: pendingId, content, status: "processing" },
    ]);
    setMessage("");
    try {
      const updatedSession = await activeRequest(
        `/sessions/${encodeURIComponent(session.session_id)}/advance`,
        {
          method: "POST",
          body: JSON.stringify({
            message: content,
            understood,
            provider_session_id: realProviderSessionId,
          }),
        },
      );
      setSnapshot((current: any) =>
        current ? { ...current, session: updatedSession } : current,
      );
      setPendingUserMessages((current) =>
        current.filter((item) => item.id !== pendingId),
      );
      return updatedSession;
    } catch (reason) {
      setPendingUserMessages((current) =>
        current.map((item) =>
          item.id === pendingId ? { ...item, status: "error" } : item,
        ),
      );
      throw reason;
    }
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
    setShowLesson(false);
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

  const enterCalendarTask = async (
    task: any,
    targetWorkspaceId = activeWorkspaceId,
  ) => {
    let current = await activeRequest(
      `/workspaces/${encodeURIComponent(targetWorkspaceId)}/sessions/start`,
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

  const revealTeacherWorkbench = () => {
    setShowLesson(true);
    window.setTimeout(() => {
      const workbench = document.getElementById("active-teacher-workbench");
      if (typeof workbench?.scrollIntoView === "function") {
        workbench.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 0);
  };

  const enterDailyTask = (task: any) => {
    revealTeacherWorkbench();
    return enterCalendarTask(task);
  };

  const enterTaskFromDailyPlan = async (
    targetWorkspaceId: string,
    task: any,
    title: string,
  ) => {
    setBusy(`进入 ${title}`);
    setError("");
    revealTeacherWorkbench();
    try {
      setActiveWorkspaceId(targetWorkspaceId);
      await enterCalendarTask(task, targetWorkspaceId);
      await refresh(targetWorkspaceId);
    } catch (reason: any) {
      setError(reason?.message || "进入学习任务失败");
    } finally {
      setBusy("");
    }
  };

  const startTodayLesson = () => {
    revealTeacherWorkbench();
    return startTeacherLedLesson();
  };

  const leaveLessonForToday = () => {
    setShowLesson(false);
    window.setTimeout(() => {
      const dashboard = document.getElementById("active-today-dashboard");
      if (typeof dashboard?.scrollIntoView === "function") {
        dashboard.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 0);
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
          <span className="active-chain__eyebrow">
            {plan ? "学习任务" : "统一学习任务中心"}
          </span>
          <h2>{plan ? "今天按计划学习，随时切换任务" : "建立一次任务，之后每天点开就学"}</h2>
          <p>
            {plan
              ? "直接选择今天的一项开始；计划、教师记忆和学习进度都会自动保存。"
              : "每个任务独立保存计划、知识地图、教师记忆和本地 RAG；切换任务不会串资料。"}
          </p>
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
                    setShowLesson(false);
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

      {dailyPlanItems.length > 0 && (
        <article
          className="active-today"
          data-testid="active-today-dashboard"
          id="active-today-dashboard"
        >
          <div className="active-today__head">
            <div>
              <span className="active-chain__eyebrow">全部计划 · 今日学习</span>
              <h2>今天有 {dailyPlanItems.length} 个学习计划</h2>
              <p>先选计划，再按编号从简单和前置知识开始；切换计划、任务或退出页面都不会丢失教师记忆。</p>
            </div>
            <div className="active-today__actions">
              {session && (
                <button
                  data-testid="active-continue-lesson"
                  disabled={Boolean(busy)}
                  onClick={revealTeacherWorkbench}
                >
                  继续当前课程
                </button>
              )}
              {!session && (
                <button
                  data-testid="active-start-today"
                  disabled={Boolean(busy)}
                  onClick={() => run("启动今日主动课程", startTodayLesson)}
                >
                  开始今日课程
                </button>
              )}
              <button
                className="active-today__secondary"
                data-testid="active-toggle-plan-details"
                onClick={() => setShowPlanDetails((current) => !current)}
              >
                {showPlanDetails ? "收起当前计划设置" : "查看或调整当前计划"}
              </button>
            </div>
          </div>

          <div className="active-today__summary">
            <span><b>{isoAfterDays(0)}</b> 今日</span>
            <span><b>{dailyPlanItems.length}</b> 个进行中的计划</span>
            <span><b>{dailyPlanTaskCount}</b> 项知识主题</span>
            <span>
              <b>
                {dailyPlanItems.reduce(
                  (total, item) =>
                    total + Number(item.todayPlan?.remaining_task_minutes || 0),
                  0,
                )}
              </b>
              标准任务分钟待完成
            </span>
          </div>

          <div className="active-today__plans" data-testid="active-daily-plans">
            {dailyPlanItems.map((dailyPlan) => {
              const expanded = expandedDailyPlans.includes(dailyPlan.workspaceId);
              const remainingMinutes = Number(
                dailyPlan.todayPlan?.remaining_task_minutes || 0,
              );
              return (
                <section
                  className={`${dailyPlan.workspaceId === activeWorkspaceId ? "is-active" : ""}`}
                  data-testid={`active-daily-plan-${dailyPlan.workspaceId}`}
                  key={dailyPlan.workspaceId}
                >
                  <button
                    className="active-today__plan-toggle"
                    data-testid={`active-daily-plan-toggle-${dailyPlan.workspaceId}`}
                    onClick={() => {
                      setExpandedDailyPlans((current) =>
                        current.includes(dailyPlan.workspaceId)
                          ? current.filter((item) => item !== dailyPlan.workspaceId)
                          : [...current, dailyPlan.workspaceId]
                      );
                    }}
                  >
                    <span>
                      <b>{dailyPlan.title}</b>
                      <small>
                        {dailyPlan.groups.length} 项 · {remainingMinutes} 分钟待完成
                      </small>
                    </span>
                    <em>{expanded ? "收起" : "展开今天内容"}</em>
                  </button>

                  {expanded && (
                    <div className="active-today__plan-body">
                      <p className="active-today__order-policy">
                        学习顺序：前置依赖优先；当前可学习内容按简单 → 中等 → 困难排列。
                      </p>
                      {dailyPlan.groups.length > 0 ? (
                        <div
                          className="active-today__tasks"
                          data-testid={`active-today-tasks-${dailyPlan.workspaceId}`}
                        >
                          {dailyPlan.groups.map((group, groupIndex) => {
                            const active =
                              dailyPlan.workspaceId === activeWorkspaceId
                              && group.tasks.some(
                                (task: any) =>
                                  task.id === session?.state?.active_task_id,
                              );
                            const complete = group.completed === group.tasks.length;
                            return (
                              <article
                                className={`${active ? "is-active" : ""} ${complete ? "is-complete" : ""}`}
                                key={group.key}
                              >
                                <div className="active-today__sequence">
                                  {String(groupIndex + 1).padStart(2, "0")}
                                </div>
                                <div>
                                  <span>
                                    {group.taskType === "review"
                                      ? "完整复习"
                                      : group.taskType === "remediation"
                                        ? "补救学习"
                                        : "首学"}
                                    {group.difficulty ? ` · ${group.difficulty}` : ""}
                                  </span>
                                  <h3>{group.title}</h3>
                                  <p>
                                    {group.prerequisiteNames.length
                                      ? `前置：${group.prerequisiteNames.join("、")} · `
                                      : "无未满足前置 · "}
                                    {group.minutes} 分钟 ·
                                    {group.completed}/{group.tasks.length} 个步骤完成
                                  </p>
                                </div>
                                <button
                                  data-testid={`active-today-task-${dailyPlan.workspaceId}-${group.knowledgePointId || group.nextTask.id}`}
                                  disabled={Boolean(busy) || complete}
                                  onClick={() =>
                                    enterTaskFromDailyPlan(
                                      dailyPlan.workspaceId,
                                      group.nextTask,
                                      group.title,
                                    )}
                                >
                                  {complete
                                    ? "今日已完成"
                                    : active
                                      ? "继续学习"
                                      : "开始学习"}
                                </button>
                              </article>
                            );
                          })}
                        </div>
                      ) : (
                        <div className="active-today__empty">
                          <b>{dailyPlan.todayPlan?.date || isoAfterDays(0)} 没有新的首学任务</b>
                          <span>
                            {(dailyPlan.todayPlan?.projected_review_items || []).length > 0
                              ? `预留了 ${dailyPlan.todayPlan.projected_review_items.length} 项完整复习，进入该计划教师后执行。`
                              : "该计划今天没有安排，明天会继续按原顺序推进。"}
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        </article>
      )}

      <div
        className={`active-chain__grid ${plan && !showPlanDetails ? "active-chain__is-hidden" : ""}`}
        data-testid="active-plan-details"
      >
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
            <label>
              完整复习轮数
              <select
                data-testid="active-review-rounds"
                value={reviewRounds}
                onChange={(event) => setReviewRounds(Number(event.target.value))}
              >
                <option value={2}>2 轮（推荐）</option>
                <option value={3}>3 轮</option>
                <option value={4}>4 轮</option>
              </select>
            </label>
          </div>
          <div className="active-chain__allocation" data-testid="active-system-allocation">
            <span>系统按总任务量平均分配</span>
            <b>
              {plan && snapshot?.receipt?.allocation?.recommended_minutes_per_day
                ? `每天约 ${snapshot.receipt.allocation.recommended_minutes_per_day} 标准任务分钟：首学 1 次 + 完整复习 ${snapshot.receipt.review_rounds || 2} 轮`
                : snapshot?.receipt?.allocation?.recommended_minutes_per_day
                ? `暂估 ${snapshot.receipt.allocation.recommended_minutes_per_day} 标准任务分钟/日`
                : "保存后自动计算"}
            </b>
            <small>
              首学约 {snapshot?.receipt?.allocation?.initial_learning_minutes || "待估算"} 分钟；
              每轮完整复习约 {snapshot?.receipt?.allocation?.one_review_round_minutes || "待估算"} 分钟。
              {transitionDays.length
                ? ` ${phaseRange(transitionDays)} 是交界日：先完成首学，再用当天剩余任务量开始复习。`
                : " 系统按顺序排入每天，不再强制按日期比例切开。"}
            </small>
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
          <button
            data-testid="active-generate-plan"
            disabled={!graph.knowledge_points.length || Boolean(busy) || Boolean(plan)}
            onClick={() => run("生成完全掌握计划", generateMasteryPlan)}
          >
            {plan
              ? "完整掌握计划已生成（修改条件请在上方重新排程）"
              : "生成完整掌握计划"}
          </button>
          {plan && (
            <>
              <div className="active-chain__metrics">
                <strong data-testid="active-plan-budget">{plan.daily_task_minutes_budget}</strong><span>每日任务目标</span>
                <strong>{plan.tasks.length}</strong><span>可执行任务</span>
                <strong>{plan.unscheduled_task_minutes}</strong><span>未排分钟</span>
                <strong data-testid="active-plan-feasible">{plan.feasible ? "可行" : "不可行"}</strong><span>容量结论</span>
              </div>
              <div className="active-chain__time">
                <span>完全掌握预计 {plan.total_mastery_task_minutes ?? plan.total_estimated_task_minutes ?? plan.tasks.reduce((sum: number, item: any) => sum + item.estimated_task_minutes, 0)} 任务分钟</span>
                <span>第一遍预计 {plan.initial_learning_task_minutes ?? plan.tasks.reduce((sum: number, item: any) => sum + item.estimated_task_minutes, 0)} 分钟</span>
                <span>
                  完整复习 {plan.review_rounds ?? reviewRounds} 轮，共预计 {plan.projected_review_task_minutes ?? 0} 分钟
                  （每轮约 {plan.one_review_round_task_minutes ?? 0} 分钟）
                </span>
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
                      <span>
                        {day.study_phase === "initial_learning"
                          ? "首学"
                          : day.study_phase === "transition"
                            ? "首学→复习交界日"
                            : day.study_phase === "review"
                              ? "复习"
                              : day.study_phase === "available"
                                ? "可学习"
                                : "休息"}
                      </span>
                      <span>
                        容量 {day.capacity_minutes} ·
                        计划 {Number(day.planned_task_minutes || 0) + Number(day.projected_review_task_minutes || 0)}
                      </span>
                      {["review", "transition"].includes(day.study_phase) && (
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
                            onClick={() => run(`进入 ${task.title}`, () => enterDailyTask(task))}
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
                        <small key={`projected-${day.date}-${item.knowledge_point_id}-${item.review_round || 1}-${item.part_index || 1}`}>
                          第 {item.review_round || 1} 轮：{item.title} · {item.projected_review_task_minutes} 分钟
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

      <article
        className={`active-chain__teacher ${plan && !showLesson ? "active-chain__is-hidden" : ""}`}
        id="active-teacher-workbench"
      >
        <div className="active-chain__teacher-head">
          <div><span className="active-chain__step">04 · 今日学习</span><h3>当前任务专属教师 · 计划 RAG 与记忆不会串线</h3></div>
          <button data-testid="active-start-teacher" disabled={!plan || Boolean(busy)} onClick={() => run("启动今日主动课程", startTodayLesson)}>开始今日课程（教师主动带学）</button>
          {plan && (
            <button
              className="active-today__secondary"
              data-testid="active-leave-lesson"
              disabled={Boolean(busy)}
              onClick={leaveLessonForToday}
            >
              暂存并返回今日任务
            </button>
          )}
          {session && <button data-testid="active-replan" disabled={Boolean(busy)} onClick={() => run("滚动未完成任务", () => activeRequest(`/sessions/${encodeURIComponent(session.session_id)}/replan`, {
            method: "POST",
            body: JSON.stringify({
              trigger: "manual_rollover",
              reason: "用户要求从今天滚动未完成任务",
              affected_from: isoAfterDays(0),
            }),
          }))}>从今天滚动未完成任务</button>}
        </div>
        <p className="active-chain__muted">
          教师会依次带完首学与所选完整复习轮次；计划完成后仍可继续检索当前知识库并自由提问。
        </p>
        {session && (
          <>
            <div className="active-classroom">
              <aside className="active-classroom__teacher">
                <TeacherCompanionPortrait
                  teacherName="艾莉丝"
                  state={teacherPresentationState}
                />
                <div className="active-classroom__focus" data-testid="active-current-knowledge-point">
                  <span>现在只学这一点</span>
                  <h4>{currentKnowledgePoint?.name || "当前知识点"}</h4>
                  <p>
                    第 {Math.min(Number(session.state.route_index || 0) + 1, Number(session.state.route_length || 1))}
                    {" / "}{session.state.route_length || 1} 个知识点
                  </p>
                  <small>你可以随时打断、追问、要求换个例子。</small>
                </div>
              </aside>

              <section className="active-classroom__dialogue">
                <div className="active-classroom__dialogue-head">
                  <div>
                    <span>教师正在带你完成</span>
                    <h3>{currentKnowledgePoint?.name || "今天的学习任务"}</h3>
                  </div>
                  <b>{session.state.autonomy?.awaiting_learner ? "等你说话" : "讲解中"}</b>
                </div>

                {session.state.today_plan && (
                  <div className="active-chain__receipt" data-testid="active-today-plan">
                    <b>{session.state.today_plan.date} · 今日课程</b>
                    <span>
                      今天共 {session.state.today_plan.knowledge_point_count ?? session.state.today_plan.task_count} 个知识点，
                      但每次只推进一个；当前约 {session.state.today_remaining_task_minutes} 标准任务分钟待完成。
                    </span>
                  </div>
                )}

                <div
                  className="active-chain__history"
                  data-testid="active-conversation-history"
                  ref={conversationHistoryRef}
                >
                  {(session.messages || []).slice(-8).map((item: any) => {
                    const paragraphs = readableMessageParagraphs(item.content);
                    const visibleParagraphs = item.role === "teacher" ? paragraphs.slice(0, 2) : paragraphs;
                    const remainingParagraphs = item.role === "teacher" ? paragraphs.slice(2) : [];
                    return (
                      <div
                        className={`active-chain__message ${item.role === "user" ? "is-user" : ""}`}
                        data-testid={item.id === latestTeacher?.id ? "active-latest-teacher" : undefined}
                        key={item.id}
                      >
                        <span>
                          {item.role === "user"
                            ? "你"
                            : teacherActionLabels[item.action] || "艾莉丝"}
                        </span>
                        {visibleParagraphs.map((paragraph, index) => (
                          <p key={`${item.id}-visible-${index}`}>{paragraph}</p>
                        ))}
                        {remainingParagraphs.length > 0 && (
                          <details className="active-chain__message-more">
                            <summary>展开本轮完整讲解</summary>
                            {remainingParagraphs.map((paragraph, index) => (
                              <p key={`${item.id}-remaining-${index}`}>{paragraph}</p>
                            ))}
                          </details>
                        )}
                        {(item.citations || []).length > 0 && (
                          <details className="active-chain__citations">
                            <summary>查看资料依据（{item.citations.length}）</summary>
                            {(item.citations || []).map((citation: any) => (
                              <blockquote key={citation.chunk_id}>
                                {citation.quote}
                                <small>
                                  段落 {citation.paragraph_start} · 字符 {citation.char_start}-{citation.char_end}
                                </small>
                              </blockquote>
                            ))}
                          </details>
                        )}
                      </div>
                    );
                  })}
                  {pendingUserMessages.map((item) => (
                    <div
                      className={`active-chain__message is-user is-pending ${item.status === "error" ? "is-error" : ""}`}
                      data-testid="active-pending-user-message"
                      key={item.id}
                    >
                      <span>你 · {item.status === "processing" ? "教师正在思考…" : "发送失败，请重试"}</span>
                      <p>{item.content}</p>
                    </div>
                  ))}
                </div>

                <div className="active-chain__composer active-classroom__composer">
                  <textarea
                    data-testid="active-teacher-input"
                    value={message}
                    onChange={(event) => setMessage(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        if (message.trim() && !busy) {
                          void run("推进教学", () => advance());
                        }
                      }
                    }}
                    placeholder="哪里没听懂就直接说；Enter 发送，Shift+Enter 换行"
                  />
                  <button
                    data-testid="active-send-teacher"
                    disabled={!message.trim() || Boolean(busy)}
                    onClick={() => run("推进教学", () => advance())}
                  >
                    发送
                  </button>
                  <button
                    data-testid="active-understood"
                    disabled={Boolean(busy)}
                    onClick={() => run("提交理解证据", () => advance(message || "我理解了，可以继续", true))}
                  >
                    我已理解并接受检测
                  </button>
                </div>

                <details className="active-classroom__status">
                  <summary>查看课程状态与系统依据</summary>
                  <div className="active-chain__state" data-testid="active-session-state">
                    <span>步骤 <b>{session.state.current_teaching_step}</b></span>
                    <span>路线 <b>{session.state.route_index + 1}/{session.state.route_length}</b></span>
                    <span>今日剩余 <b>{session.state.today_remaining_task_minutes}</b> 任务分钟</span>
                    <span>补救栈 <b>{session.state.remediation_stack.length}</b></span>
                    <span>实际耗时 <b>{session.actual_elapsed_minutes}</b> 分钟</span>
                    <span>检索 <b>{session.state.last_retrieval?.mode || "local_hybrid_v1"}</b></span>
                  </div>
                  {(session.state.replan_history || []).length > 0 && (
                    <div className="active-chain__receipt" data-testid="active-replan-summary">
                      <b>最近一次自动重排</b>
                      <span>{session.state.replan_history.at(-1).trigger} · {session.state.replan_history.at(-1).reason}</span>
                    </div>
                  )}
                  {(session.state.mastery_evidence || []).length > 0 && (
                    <div className="active-chain__receipt" data-testid="active-mastery-evidence">
                      <b>最近掌握证据</b>
                      {session.state.mastery_evidence.slice(-5).map((item: any, index: number) => (
                        <span key={`${item.knowledge_point_id}-${index}`}>
                          {item.learning_phase || (item.review_item_id ? "延迟复测" : "理解检测")} ·
                          {item.passed ? "通过" : "未通过"} · {(Number(item.score || 0) * 100).toFixed(0)}%
                        </span>
                      ))}
                    </div>
                  )}
                </details>
              </section>
            </div>

            <details className="active-classroom__tools">
              <summary>复习、代码练习与其他课程工具</summary>
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
            </details>
          </>
        )}
      </article>
      {completionReport && (
        <article
          className={`active-chain__teacher ${showPlanDetails ? "" : "active-chain__is-hidden"}`}
          data-testid="active-completion-report"
        >
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
