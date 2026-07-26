import { useEffect, useMemo, useRef, useState } from "react";
import { activeRequest, activeSnapshot, uploadActiveMaterial } from "./api";
import { useMentorStore } from "../../stores/useMentorStore";
import "./active-teaching.css";

const isoAfterDays = (days: number) => {
  const value = new Date(Date.now() + days * 86400000);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
};
const ACTIVE_WORKSPACE_KEY = "mentorflow_active_teaching_workspace_v1";

export function ActiveTeachingChainView({ workspaceId }: { workspaceId: string }) {
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
  const refreshSequence = useRef(0);

  const refresh = async () => {
    const requestSequence = ++refreshSequence.current;
    const next = await activeSnapshot(activeWorkspaceId);
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
    setSnapshot(next);
    setProblems(problemItems);
    setProblemId((current) =>
      problemItems.some((item: any) => item.id === current)
        ? current
        : problemItems[0]?.id || "",
    );
    if (next.receipt) {
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
    try {
      localStorage.setItem(ACTIVE_WORKSPACE_KEY, activeWorkspaceId);
    } catch {
      // Private browsing may disable local storage; server state remains durable.
    }
    refresh().catch(() => setSnapshot(null));
  }, [activeWorkspaceId]);

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
  const localKnowledgeBase = snapshot?.local_knowledge_base;
  const graph = snapshot?.knowledge_graph || { knowledge_points: [], edges: [] };
  const plan = snapshot?.plan;
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
    });

  const generateMasteryPlan = () =>
    activeRequest(`/workspaces/${encodeURIComponent(activeWorkspaceId)}/mastery-plans/generate`, {
      method: "POST",
    });

  const uploadAndAnalyze = async () => {
    if (!file) throw new Error("请选择文件");
    const uploaded = await uploadActiveMaterial(activeWorkspaceId, file);
    const ids = Array.from(new Set([
      ...materials.filter((item: any) => item.status === "ready").map((item: any) => item.id),
      uploaded.id,
    ]));
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
          <span className="active-chain__eyebrow">通用主动式 AI 教学链</span>
          <h2>上传任意学习资料，生成可追溯知识地图与掌握计划</h2>
          <p>资料内容决定知识结构、验收方式和任务估时；页面不内置具体课程或固定指标。</p>
        </div>
        <label>
          验收空间 ID
          <input data-testid="active-workspace-id" value={activeWorkspaceId} onChange={(event) => setActiveWorkspaceId(event.target.value)} />
        </label>
      </header>

      {error && <div className="active-chain__error" role="alert">{error}</div>}
      {busy && <div className="active-chain__busy">正在执行：{busy}</div>}

      <div className="active-chain__grid">
        <article className="active-chain__card">
          <span className="active-chain__step">01 · 资料摄取</span>
          <h3>上传并解析学习资料</h3>
          <input data-testid="active-material-file" type="file" accept=".md,.markdown,.txt,.pdf,.docx,.html,.htm,.csv,.json,.yaml,.yml,.xml,.rtf,.pptx,.xlsx,.epub,.py,.js,.jsx,.ts,.tsx,.java,.c,.cpp,.h,.go,.rs,.sql,.sh,.ps1" onChange={(event) => setFile(event.target.files?.[0] || null)} />
          <button data-testid="active-upload" disabled={!file || Boolean(busy)} onClick={() => run("上传、分析并规划", uploadAndAnalyze)}>上传并自动生成知识地图与计划</button>
          <div data-testid="active-material-count">资料 {materials.length} 份</div>
          <div className="active-chain__receipt" data-testid="active-local-knowledge-base">
            <b>本地 RAG 知识库：{localKnowledgeBase?.status === "ready" ? "可用" : "等待资料"}</b>
            <span>
              {localKnowledgeBase?.material_count || 0} 份资料 · {localKnowledgeBase?.chunk_count || 0} 个可引用分块 ·
              {localKnowledgeBase?.indexed_characters || 0} 字符
            </span>
              <span>
                {localKnowledgeBase?.scope === "current_plan"
                  ? `当前计划独立 · 冻结 ${(localKnowledgeBase?.material_ids || []).length} 份资料`
                  : "计划建立后自动冻结资料范围"}
                {" · "}无需联网 · 混合召回（词项、中文片段、标题权重与来源分散）
              </span>
          </div>
          {materials.map((item: any) => (
            <div className="active-chain__receipt" key={item.id}>
              <b>{item.original_filename}</b>
              <span>{item.size_bytes} bytes · SHA-256 {item.sha256.slice(0, 12)}…</span>
              <span>{item.chunk_count} chunks · {item.parser_name}</span>
              {item.original_filename.toLowerCase().endsWith(".json") && graph.knowledge_points.length > 0 && (
                <button
                  disabled={Boolean(busy)}
                  onClick={() => run("导入资料内的可执行题目规格", () => importProblemSpecs(item.id))}
                >
                  导入经来源验证的题目与测试
                </button>
              )}
            </div>
          ))}
        </article>

        <article className="active-chain__card">
          <span className="active-chain__step">02 · 创建掌握计划</span>
          <h3>告诉系统目标和真实可用时间</h3>
          <p className="active-chain__muted">你不需要猜每天该学多少分钟。系统会先评估资料，再把任务分配到可用日期。</p>
          <label>学习目标<textarea data-testid="active-goal" value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
          <div className="active-chain__row">
            <label>当前基础<select data-testid="active-foundation" value={level} onChange={(event) => setLevel(event.target.value)}><option value="zero_foundation">零基础</option><option value="beginner">初级</option><option value="intermediate">中级</option><option value="advanced">高级</option></select></label>
            <label>开始日期<input data-testid="active-start-date" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
            <label>截止日期<input data-testid="active-deadline" type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
          </div>
          <div className="active-chain__allocation" data-testid="active-system-allocation">
            <span>系统分配每日投入</span>
            <b>
              {snapshot?.receipt?.allocation?.recommended_minutes_per_day
                ? `${snapshot.receipt.allocation.recommended_minutes_per_day} 分钟 / 可学习日`
                : "保存后自动计算"}
            </b>
            <small>前 50% 可学习日期完成第一遍，后 50% 进入滚动复习；生成知识地图后会按真实知识量再次校准。</small>
          </div>
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
          <label>备注<textarea data-testid="active-notes" value={notes} onChange={(event) => setNotes(event.target.value)} /></label>
          <button
            data-testid="active-save-receipt"
            disabled={!materials.length || Boolean(busy)}
            onClick={() => run("保存学习条件并分析资料", async () => {
              await saveReceipt();
              await generateKnowledgeMap();
            })}
          >
            保存条件并生成知识地图
          </button>
        </article>

        <article className="active-chain__card">
          <span className="active-chain__step">03 · 知识图谱</span>
          <h3>生成有来源的知识点与依赖</h3>
          <button data-testid="active-generate-graph" disabled={!snapshot?.receipt || Boolean(busy)} onClick={() => run("从资料生成知识地图", generateKnowledgeMap)}>从上传资料生成知识地图</button>
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
                    ? "可排程，建议语义复核"
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
              {(graph.assessment.readiness?.risks || []).map((risk: string) => (
                <span key={risk}>{risk}</span>
              ))}
            </div>
          )}
          {graph.knowledge_points.slice(0, 4).map((point: any) => (
            <div className="active-chain__receipt" key={point.id}>
              <b>{point.name}</b>
              <span>{point.difficulty} · 完全掌握 {point.mastery_estimate?.total_mastery_task_minutes || point.estimated_task_minutes} 标准任务分钟 · 掌握度 {(point.current_mastery * 100).toFixed(0)}%</span>
              {point.mastery_estimate && (
                <>
                  <span>
                    理解 {point.mastery_estimate.understand_minutes} · 回忆 {point.mastery_estimate.recall_minutes} ·
                    引导练习 {point.mastery_estimate.guided_practice_minutes} · 独立应用 {point.mastery_estimate.independent_application_minutes} ·
                    复习 {point.mastery_estimate.review_minutes}
                  </span>
                  <span>
                    估时依据 {point.mastery_estimate.basis || "legacy"} ·
                    置信度 {point.mastery_estimate.confidence || "未知"}
                  </span>
                </>
              )}
            </div>
          ))}
        </article>

        <article className="active-chain__card">
          <span className="active-chain__step">04 · 依赖计划</span>
          <h3>按知识依赖与可用时间生成计划</h3>
          <button data-testid="active-generate-plan" disabled={!graph.knowledge_points.length || Boolean(busy)} onClick={() => run("生成完全掌握计划", generateMasteryPlan)}>生成完全掌握计划</button>
          {plan && (
            <>
              <div className="active-chain__metrics">
                <strong data-testid="active-plan-budget">{plan.daily_task_minutes_budget}</strong><span>每日预算</span>
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
                    <div className={`active-chain__day ${day.capacity_minutes === 0 ? "is-rest" : ""}`} key={day.date}>
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
                        <small key={task.id || `${day.date}-${task.knowledge_point_id}-${task.learning_phase}-${taskIndex}`}>
                          {task.title} · {task.estimated_task_minutes} 分钟 · {task.status}
                        </small>
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

      <article className="active-chain__teacher">
        <div className="active-chain__teacher-head">
          <div><span className="active-chain__step">05 · 主动教学工作台</span><h3>知识简报 → 检索讲解 → 邀请提问 → 引导练习 → 补救</h3></div>
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
